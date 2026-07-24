from __future__ import annotations

from datetime import date

from finance_bot.parser import parse_message, parse_screenshot_text


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


def test_clear_command_requires_confirmation() -> None:
    parsed = parse_message("/clear all")

    assert parsed.action == "clear"
    assert parsed.target == "all"
    assert parsed.error is not None


def test_clear_transactions_command_with_confirmation() -> None:
    parsed = parse_message("/clear transactions yes")

    assert parsed.action == "clear"
    assert parsed.target == "transactions"
    assert parsed.error is None


def test_russian_clear_database_command() -> None:
    parsed = parse_message("очистить базу подтверждаю")

    assert parsed.action == "clear"
    assert parsed.target == "all"


def test_screenshot_category_summary_is_not_recorded_as_single_expense() -> None:
    text = (
        "Продукты, хозтова... Подарки Транспорт Одежда, товары "
        "314 Br 285 Br 255 Br 205 Br Еда вне дома Развлечения "
        "Дом, подписки Здоровье 113 Br 105 Br 81 Br 76 Br Вред 58 Br"
    )

    parsed = parse_screenshot_text(text)

    assert parsed.action == "transaction"
    assert parsed.type == "expense"
    assert parsed.amount is None
    assert parsed.error is not None
    assert "несколько сумм" in parsed.error


def test_screenshot_with_single_payment_hint_records_expense() -> None:
    parsed = parse_screenshot_text("Списание по карте 314,00 Br")

    assert parsed.action == "transaction"
    assert parsed.type == "expense"
    assert parsed.amount == 314.00
