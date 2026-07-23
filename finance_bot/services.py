from __future__ import annotations

from dataclasses import dataclass

from .db import FinanceDB
from .parser import ParsedCommand
from .report import generate_html_report


@dataclass(frozen=True)
class TransactionResult:
    transaction_id: int
    message: str


class FinanceService:
    """Business operations used by Telegram handlers."""

    def __init__(self, db: FinanceDB) -> None:
        self.db = db

    def record_transaction(self, user_id: int, parsed: ParsedCommand) -> TransactionResult:
        if parsed.type is None or parsed.amount is None:
            raise ValueError("Parsed transaction must include type and amount")
        transaction_id = self.db.add_transaction(
            user_id=user_id,
            type_=parsed.type,
            amount=parsed.amount,
            category=parsed.category,
            note=parsed.note,
        )
        tx_name = "доход" if parsed.type == "income" else "расход"
        message = f"Записал {tx_name}: {parsed.amount:.2f}, категория: {parsed.category or 'прочее'}."
        if parsed.type == "expense":
            status = self.db.check_budget_status(user_id, parsed.category or "прочее")
            if status:
                message += f"\n\n{status.message}"
        message += f"\nID операции: {transaction_id}"
        return TransactionResult(transaction_id=transaction_id, message=message)

    def add_subscription(self, user_id: int, parsed: ParsedCommand) -> str:
        if parsed.name is None or parsed.amount is None or parsed.next_payment_date is None:
            raise ValueError("Parsed subscription must include name, amount and next_payment_date")
        subscription_id = self.db.add_subscription(user_id, parsed.name, parsed.amount, parsed.next_payment_date)
        return (
            f"Добавил подписку {parsed.name}: {parsed.amount:.2f}, ближайшая оплата {parsed.next_payment_date}.\n"
            f"ID подписки: {subscription_id}"
        )

    def set_budget(self, user_id: int, parsed: ParsedCommand) -> str:
        if parsed.category is None or parsed.amount is None:
            raise ValueError("Parsed budget must include category and amount")
        self.db.upsert_budget(user_id, parsed.category, parsed.amount)
        return f"Лимит на {parsed.category.lower()} установлен: {parsed.amount:.2f} на текущий месяц."

    def report_path(self, user_id: int, period: str) -> str:
        return generate_html_report(user_id, period, db_path=self.db.path)

    def mark_paid(self, user_id: int, target: str | None) -> str:
        if not target:
            return "Укажи ID или название подписки, например: /paid 3"
        subscription = self.db.find_subscription(user_id, target)
        if not subscription:
            return "Не нашёл активную подписку."
        updated = self.db.mark_subscription_paid(int(subscription["id"]), user_id=user_id)
        if not updated:
            return "Не смог отметить подписку оплаченной."
        return f"Отметил {updated['name']} оплаченной. Следующая дата: {updated['next_payment_date']}."

    def clear_data(self, user_id: int, target: str | None) -> str:
        clean_target = target or "all"
        labels = {
            "transactions": "операций",
            "subscriptions": "подписок",
            "budgets": "лимитов",
            "all": "записей",
        }
        if clean_target not in labels:
            return "Неизвестный тип очистки. Используй transactions, subscriptions, budgets или all."
        deleted = self.db.clear_user_data(user_id, clean_target)
        total = sum(deleted.values())
        details = ", ".join(
            f"{deleted[key]} {label}"
            for key, label in (("transactions", "операций"), ("subscriptions", "подписок"), ("budgets", "лимитов"))
            if clean_target == "all" or key == clean_target
        )
        return f"Готово: удалено {total} {labels[clean_target]} ({details})."
