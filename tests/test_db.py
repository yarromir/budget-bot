from __future__ import annotations

import sqlite3
from datetime import date, datetime

from finance_bot.db import APP_TZ, FinanceDB, add_months, month_bounds


def test_transaction_summary_uses_integer_cents(tmp_path) -> None:
    db = FinanceDB(tmp_path / "finance.db")
    db.initialize()
    created = datetime(2026, 7, 10, 12, 0, tzinfo=APP_TZ)

    db.add_transaction(1, "income", 1000.10, "зп", created_at=created)
    db.add_transaction(1, "expense", 100.05, "такси", created_at=created)
    start, end = month_bounds("2026-07")

    summary = db.transaction_summary(1, start, end)

    assert summary == {"income": 1000.10, "expense": 100.05, "balance": 900.05}


def test_budget_status_thresholds(tmp_path) -> None:
    db = FinanceDB(tmp_path / "finance.db")
    db.initialize()
    db.upsert_budget(1, "такси", 1000, month="2026-07")
    db.add_transaction(1, "expense", 800, "такси", created_at=datetime(2026, 7, 1, 12, tzinfo=APP_TZ))

    status = db.check_budget_status(1, "такси", month="2026-07")

    assert status is not None
    assert status.level == "near"
    assert status.percent == 80


def test_list_budgets_with_spending(tmp_path) -> None:
    db = FinanceDB(tmp_path / "finance.db")
    db.initialize()
    db.upsert_budget(1, "такси", 1000, month="2026-07")
    db.add_transaction(1, "expense", 250, "такси", created_at=datetime(2026, 7, 3, 12, tzinfo=APP_TZ))
    db.add_transaction(1, "expense", 500, "такси", created_at=datetime(2026, 8, 3, 12, tzinfo=APP_TZ))

    budgets = db.list_budgets_with_spending(1, month="2026-07")

    assert len(budgets) == 1
    assert budgets[0]["category"] == "такси"
    assert budgets[0]["limit_amount"] == 1000
    assert budgets[0]["spent"] == 250


def test_subscription_reminder_flags_can_be_disabled(tmp_path) -> None:
    db = FinanceDB(tmp_path / "finance.db")
    db.initialize()
    db.add_subscription(1, "YouTube", 299, date(2026, 8, 1), remind_7=False, remind_3=True, remind_1=False)

    subscription = db.list_active_subscriptions(1)[0]

    assert subscription["remind_7"] == 0
    assert subscription["remind_3"] == 1
    assert subscription["remind_1"] == 0


def test_add_months_clamps_end_of_month() -> None:
    assert add_months(date(2026, 1, 31)) == date(2026, 2, 28)


def test_migrates_legacy_real_amounts(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL, amount REAL NOT NULL, category TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL, amount REAL NOT NULL, next_payment_date TEXT NOT NULL, remind_7 INTEGER NOT NULL DEFAULT 1, remind_3 INTEGER NOT NULL DEFAULT 1, remind_1 INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1)")
        conn.execute("CREATE TABLE budgets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, category TEXT NOT NULL, limit_amount REAL NOT NULL, month TEXT NOT NULL, UNIQUE (user_id, category, month))")
        conn.execute("INSERT INTO transactions (user_id, type, amount, category, note, created_at) VALUES (1, 'expense', 12.34, 'такси', '', '2026-07-01T10:00:00+07:00')")

    db = FinanceDB(path)
    db.initialize()
    start, end = month_bounds("2026-07")

    assert db.transaction_summary(1, start, end)["expense"] == 12.34


def test_clear_user_data_only_affects_selected_user(tmp_path) -> None:
    db = FinanceDB(tmp_path / "finance.db")
    db.initialize()
    db.add_transaction(1, "expense", 100, "такси")
    db.add_transaction(2, "expense", 200, "такси")
    db.add_subscription(1, "YouTube", 299, date(2026, 8, 1))
    db.upsert_budget(1, "такси", 1000, month="2026-07")

    deleted = db.clear_user_data(1, "all")

    assert deleted == {"transactions": 1, "subscriptions": 1, "budgets": 1}
    assert db.count_user_records(1) == {"transactions": 0, "subscriptions": 0, "budgets": 0}
    assert db.count_user_records(2)["transactions"] == 1
