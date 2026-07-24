from __future__ import annotations

import calendar
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from .money import to_cents


APP_TZ = ZoneInfo(os.getenv("FINANCE_BOT_TIMEZONE", "Asia/Novosibirsk"))
DEFAULT_DB_PATH = Path(os.getenv("FINANCE_DB_PATH", "~/finance_bot/finance.db")).expanduser()
VALID_TRANSACTION_TYPES = {"income", "expense"}


@dataclass(frozen=True)
class BudgetStatus:
    category: str
    limit_amount: float
    spent: float
    percent: float
    level: str

    @property
    def message(self) -> str:
        if self.level == "exceeded":
            return f"лимит превышен: {self.category} {self.spent:.2f}/{self.limit_amount:.2f}"
        return f"подход к лимиту на {self.category}: {self.spent:.2f}/{self.limit_amount:.2f}"


def normalize_category(category: str | None) -> str:
    value = (category or "").strip().lower()
    return value or "прочее"


def current_month(now: datetime | None = None) -> str:
    value = now or datetime.now(APP_TZ)
    return value.strftime("%Y-%m")


def month_bounds(month: str) -> tuple[datetime, datetime]:
    year, month_number = map(int, month.split("-", 1))
    start = datetime(year, month_number, 1, tzinfo=APP_TZ)
    if month_number == 12:
        end = datetime(year + 1, 1, 1, tzinfo=APP_TZ)
    else:
        end = datetime(year, month_number + 1, 1, tzinfo=APP_TZ)
    return start, end


def add_months(value: date, months: int = 1) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return value.replace(year=year, month=month, day=min(value.day, last_day))


class FinanceDB:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path).expanduser() if path else DEFAULT_DB_PATH

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL CHECK (type IN ('income', 'expense')),
                    amount REAL,
                    amount_cents INTEGER CHECK (amount_cents > 0),
                    category TEXT NOT NULL DEFAULT 'прочее',
                    note TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_transactions_user_created
                    ON transactions (user_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_transactions_user_category_created
                    ON transactions (user_id, category, created_at);

                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    amount REAL,
                    amount_cents INTEGER CHECK (amount_cents > 0),
                    next_payment_date TEXT NOT NULL,
                    remind_7 INTEGER NOT NULL DEFAULT 1,
                    remind_3 INTEGER NOT NULL DEFAULT 1,
                    remind_1 INTEGER NOT NULL DEFAULT 1,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_subscriptions_user_active_date
                    ON subscriptions (user_id, active, next_payment_date);

                CREATE TABLE IF NOT EXISTS budgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    limit_amount REAL,
                    limit_amount_cents INTEGER CHECK (limit_amount_cents > 0),
                    month TEXT NOT NULL,
                    UNIQUE (user_id, category, month)
                );

                CREATE INDEX IF NOT EXISTS idx_budgets_user_month
                    ON budgets (user_id, month);
                """
            )
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version < 1:
            self._ensure_column(conn, "transactions", "amount_cents", "INTEGER CHECK (amount_cents > 0)")
            self._ensure_column(conn, "subscriptions", "amount_cents", "INTEGER CHECK (amount_cents > 0)")
            self._ensure_column(conn, "budgets", "limit_amount_cents", "INTEGER CHECK (limit_amount_cents > 0)")
            conn.execute("UPDATE transactions SET amount_cents = CAST(ROUND(amount * 100) AS INTEGER) WHERE amount_cents IS NULL AND amount IS NOT NULL")
            conn.execute("UPDATE subscriptions SET amount_cents = CAST(ROUND(amount * 100) AS INTEGER) WHERE amount_cents IS NULL AND amount IS NOT NULL")
            conn.execute("UPDATE budgets SET limit_amount_cents = CAST(ROUND(limit_amount * 100) AS INTEGER) WHERE limit_amount_cents IS NULL AND limit_amount IS NOT NULL")
            conn.execute("PRAGMA user_version = 1")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def add_transaction(self, user_id: int, type_: str, amount: float, category: str | None = None, note: str | None = None, created_at: datetime | None = None) -> int:
        if type_ not in VALID_TRANSACTION_TYPES:
            raise ValueError(f"Unknown transaction type: {type_}")
        timestamp = created_at or datetime.now(APP_TZ)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO transactions (user_id, type, amount, amount_cents, category, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, type_, float(amount), to_cents(amount), normalize_category(category), note or "", timestamp.isoformat(timespec="seconds")),
            )
            return int(cursor.lastrowid)

    def list_transactions(
        self,
        user_id: int,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT id, user_id, type, COALESCE(amount_cents / 100.0, amount) AS amount, category, note, created_at
                FROM transactions
                WHERE user_id = ?
                  AND (? IS NULL OR created_at >= ?)
                  AND (? IS NULL OR created_at < ?)
                ORDER BY created_at DESC, id DESC
                LIMIT COALESCE(?, -1)
                """,
                (
                    user_id,
                    start_at.isoformat(timespec="seconds") if start_at else None,
                    start_at.isoformat(timespec="seconds") if start_at else None,
                    end_at.isoformat(timespec="seconds") if end_at else None,
                    end_at.isoformat(timespec="seconds") if end_at else None,
                    limit,
                ),
            ).fetchall()

    def transaction_summary(self, user_id: int, start_at: datetime, end_at: datetime) -> dict[str, float]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT type, COALESCE(SUM(COALESCE(amount_cents / 100.0, amount)), 0) AS total
                FROM transactions
                WHERE user_id = ? AND created_at >= ? AND created_at < ?
                GROUP BY type
                """,
                (user_id, start_at.isoformat(timespec="seconds"), end_at.isoformat(timespec="seconds")),
            ).fetchall()
        result = {"income": 0.0, "expense": 0.0}
        for row in rows:
            result[row["type"]] = float(row["total"] or 0)
        result["balance"] = round(result["income"] - result["expense"], 2)
        return result

    def all_time_summary(self, user_id: int) -> dict[str, float]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT type, COALESCE(SUM(COALESCE(amount_cents / 100.0, amount)), 0) AS total
                FROM transactions
                WHERE user_id = ?
                GROUP BY type
                """,
                (user_id,),
            ).fetchall()
        result = {"income": 0.0, "expense": 0.0}
        for row in rows:
            result[row["type"]] = float(row["total"] or 0)
        result["balance"] = result["income"] - result["expense"]
        return result

    def expense_by_category(self, user_id: int, start_at: datetime, end_at: datetime) -> dict[str, float]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT category, COALESCE(SUM(COALESCE(amount_cents / 100.0, amount)), 0) AS total
                FROM transactions
                WHERE user_id = ? AND type = 'expense' AND created_at >= ? AND created_at < ?
                GROUP BY category
                ORDER BY total DESC
                """,
                (user_id, start_at.isoformat(timespec="seconds"), end_at.isoformat(timespec="seconds")),
            ).fetchall()
        return {row["category"]: float(row["total"] or 0) for row in rows}

    def expense_by_day(self, user_id: int, start_at: datetime, end_at: datetime) -> dict[str, float]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT SUBSTR(created_at, 1, 10) AS day, COALESCE(SUM(COALESCE(amount_cents / 100.0, amount)), 0) AS total
                FROM transactions
                WHERE user_id = ? AND type = 'expense' AND created_at >= ? AND created_at < ?
                GROUP BY day
                ORDER BY day
                """,
                (user_id, start_at.isoformat(timespec="seconds"), end_at.isoformat(timespec="seconds")),
            ).fetchall()
        return {row["day"]: float(row["total"] or 0) for row in rows}

    def add_subscription(self, user_id: int, name: str, amount: float, next_payment_date: date, remind_7: bool = True, remind_3: bool = True, remind_1: bool = True, active: bool = True) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO subscriptions (user_id, name, amount, amount_cents, next_payment_date, remind_7, remind_3, remind_1, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, name.strip(), float(amount), to_cents(amount), next_payment_date.isoformat(), int(remind_7), int(remind_3), int(remind_1), int(active)),
            )
            return int(cursor.lastrowid)

    def list_active_subscriptions(self, user_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT id, user_id, name, COALESCE(amount_cents / 100.0, amount) AS amount, next_payment_date, remind_7, remind_3, remind_1, active
                FROM subscriptions
                WHERE user_id = ? AND active = 1
                ORDER BY next_payment_date, name
                """,
                (user_id,),
            ).fetchall()

    def find_subscription(self, user_id: int, value: str) -> sqlite3.Row | None:
        value = value.strip()
        with self.connect() as conn:
            if value.isdigit():
                return conn.execute("SELECT id, user_id, name, COALESCE(amount_cents / 100.0, amount) AS amount, next_payment_date, active FROM subscriptions WHERE user_id = ? AND id = ? AND active = 1", (user_id, int(value))).fetchone()
            return conn.execute("SELECT id, user_id, name, COALESCE(amount_cents / 100.0, amount) AS amount, next_payment_date, active FROM subscriptions WHERE user_id = ? AND LOWER(name) = LOWER(?) AND active = 1 ORDER BY next_payment_date LIMIT 1", (user_id, value)).fetchone()

    def due_subscriptions(self, days_ahead: int, allowed_user_ids: Iterable[int] | None = None, today: date | None = None) -> list[sqlite3.Row]:
        field = {1: "remind_1", 3: "remind_3", 7: "remind_7"}[days_ahead]
        target = date.fromordinal((today or datetime.now(APP_TZ).date()).toordinal() + days_ahead).isoformat()
        allowed = list(allowed_user_ids or [])
        params: list[object] = [target]
        user_clause = ""
        if allowed:
            user_clause = f"AND user_id IN ({','.join('?' for _ in allowed)})"
            params.extend(allowed)
        with self.connect() as conn:
            return conn.execute(
                f"""
                SELECT id, user_id, name, COALESCE(amount_cents / 100.0, amount) AS amount, next_payment_date
                FROM subscriptions
                WHERE active = 1 AND next_payment_date = ? AND {field} = 1 {user_clause}
                ORDER BY user_id, next_payment_date, name
                """,
                params,
            ).fetchall()

    def mark_subscription_paid(self, subscription_id: int, user_id: int | None = None) -> sqlite3.Row | None:
        with self.connect() as conn:
            params: list[object] = [subscription_id]
            user_clause = ""
            if user_id is not None:
                user_clause = "AND user_id = ?"
                params.append(user_id)
            row = conn.execute(f"SELECT id, user_id, name, COALESCE(amount_cents / 100.0, amount) AS amount, next_payment_date FROM subscriptions WHERE id = ? {user_clause} AND active = 1", params).fetchone()
            if not row:
                return None
            next_date = add_months(date.fromisoformat(row["next_payment_date"]))
            conn.execute("UPDATE subscriptions SET next_payment_date = ? WHERE id = ?", (next_date.isoformat(), subscription_id))
            return conn.execute("SELECT id, user_id, name, COALESCE(amount_cents / 100.0, amount) AS amount, next_payment_date FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()

    def upsert_budget(self, user_id: int, category: str, limit_amount: float, month: str | None = None) -> int:
        clean_category = normalize_category(category)
        budget_month = month or current_month()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO budgets (user_id, category, limit_amount, limit_amount_cents, month)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, category, month)
                DO UPDATE SET limit_amount = excluded.limit_amount, limit_amount_cents = excluded.limit_amount_cents
                """,
                (user_id, clean_category, float(limit_amount), to_cents(limit_amount), budget_month),
            )
            row = conn.execute("SELECT id FROM budgets WHERE user_id = ? AND category = ? AND month = ?", (user_id, clean_category, budget_month)).fetchone()
            return int(row["id"])

    def get_budget(self, user_id: int, category: str, month: str | None = None) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT id, user_id, category, COALESCE(limit_amount_cents / 100.0, limit_amount) AS limit_amount, month FROM budgets WHERE user_id = ? AND category = ? AND month = ?", (user_id, normalize_category(category), month or current_month())).fetchone()

    def list_budgets_with_spending(self, user_id: int, month: str | None = None) -> list[sqlite3.Row]:
        budget_month = month or current_month()
        start_at, end_at = month_bounds(budget_month)
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT
                    budgets.id,
                    budgets.user_id,
                    budgets.category,
                    COALESCE(budgets.limit_amount_cents / 100.0, budgets.limit_amount) AS limit_amount,
                    budgets.month,
                    COALESCE(SUM(COALESCE(transactions.amount_cents / 100.0, transactions.amount)), 0) AS spent
                FROM budgets
                LEFT JOIN transactions
                    ON transactions.user_id = budgets.user_id
                    AND transactions.category = budgets.category
                    AND transactions.type = 'expense'
                    AND transactions.created_at >= ?
                    AND transactions.created_at < ?
                WHERE budgets.user_id = ? AND budgets.month = ?
                GROUP BY budgets.id
                ORDER BY spent DESC, budgets.category
                """,
                (start_at.isoformat(timespec="seconds"), end_at.isoformat(timespec="seconds"), user_id, budget_month),
            ).fetchall()

    def spent_for_category_month(self, user_id: int, category: str, month: str | None = None) -> float:
        start_at, end_at = month_bounds(month or current_month())
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(COALESCE(amount_cents / 100.0, amount)), 0) AS spent
                FROM transactions
                WHERE user_id = ? AND type = 'expense' AND category = ? AND created_at >= ? AND created_at < ?
                """,
                (user_id, normalize_category(category), start_at.isoformat(timespec="seconds"), end_at.isoformat(timespec="seconds")),
            ).fetchone()
        return float(row["spent"] or 0)

    def check_budget_status(self, user_id: int, category: str, month: str | None = None) -> BudgetStatus | None:
        budget = self.get_budget(user_id, category, month)
        if not budget:
            return None
        spent = self.spent_for_category_month(user_id, category, budget["month"])
        limit_amount = float(budget["limit_amount"])
        percent = spent / limit_amount * 100
        if spent > limit_amount:
            level = "exceeded"
        elif spent >= limit_amount * 0.8:
            level = "near"
        else:
            return None
        return BudgetStatus(category=budget["category"], limit_amount=limit_amount, spent=spent, percent=percent, level=level)

    def count_user_records(self, user_id: int) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "transactions": int(conn.execute("SELECT COUNT(*) FROM transactions WHERE user_id = ?", (user_id,)).fetchone()[0]),
                "subscriptions": int(conn.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()[0]),
                "budgets": int(conn.execute("SELECT COUNT(*) FROM budgets WHERE user_id = ?", (user_id,)).fetchone()[0]),
            }

    def clear_user_data(self, user_id: int, target: str = "all") -> dict[str, int]:
        tables = {
            "transactions": "transactions",
            "subscriptions": "subscriptions",
            "budgets": "budgets",
        }
        selected = list(tables) if target == "all" else [target]
        if any(item not in tables for item in selected):
            raise ValueError(f"Unknown clear target: {target}")
        deleted = {"transactions": 0, "subscriptions": 0, "budgets": 0}
        with self.connect() as conn:
            for item in selected:
                cursor = conn.execute(f"DELETE FROM {tables[item]} WHERE user_id = ?", (user_id,))
                deleted[item] = int(cursor.rowcount if cursor.rowcount is not None else 0)
        return deleted
