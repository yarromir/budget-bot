from __future__ import annotations

from datetime import date

from finance_bot.parser import parse_message


def test_expense_with_spaced_amount_and_time_word() -> None:
    parsed = parse_message("потратил 1 234,50 на такси вчера")

    assert parsed.action == "transaction"
    assert parsed.type == "expense"
    assert parsed.amount == 1234.50
    assert parsed.category == "такси"


def test_invalid_subscription_date_returns_error() -> None:
    parsed = parse_message("добавь подписку YouTube 299 дата 31.02.2026")

    assert parsed.action == "subscription"
    assert parsed.error == "Укажи дату оплаты, например: дата 12.03.2026"


def test_two_digit_year_subscription_date() -> None:
    parsed = parse_message("добавь подписку YouTube 299 дата 12.03.26")

    assert parsed.action == "subscription"
    assert parsed.next_payment_date == date(2026, 3, 12)


def test_income_with_spaced_amount() -> None:
    parsed = parse_message("пришла зп 100 000")

    assert parsed.action == "transaction"
    assert parsed.type == "income"
    assert parsed.amount == 100000
    assert parsed.category == "зп"
