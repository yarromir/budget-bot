from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from .db import APP_TZ


Action = Literal["transaction", "subscription", "budget", "report", "mark_paid", "balance", "last", "clear", "help", "unknown"]


@dataclass(frozen=True)
class ParsedCommand:
    action: Action
    type: str | None = None
    amount: float | None = None
    category: str | None = None
    note: str | None = None
    name: str | None = None
    next_payment_date: date | None = None
    period: str | None = None
    target: str | None = None
    error: str | None = None


AMOUNT_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,](\d{1,2}))?(?!\d)")
DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b")
SCREENSHOT_TRANSACTION_HINT_RE = re.compile(
    r"\b(?:итого|сумма|оплата|покупка|списание|к\s+оплате|всего|перевод|чек|receipt|total|payment)\b",
    flags=re.IGNORECASE,
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_amount(value: str) -> float | None:
    match = AMOUNT_RE.search(value)
    if not match:
        return None
    whole, fraction = match.groups()
    normalized = whole.replace(" ", "").replace("\u00a0", "")
    if fraction is not None:
        normalized = f"{normalized}.{fraction}"
    return float(normalized)


def _parse_date(value: str) -> date | None:
    lowered = value.lower()
    today = datetime.now(APP_TZ).date()
    if re.search(r"\bпослезавтра\b", lowered):
        return today + timedelta(days=2)
    if re.search(r"\bзавтра\b", lowered):
        return today + timedelta(days=1)
    if re.search(r"\bсегодня\b", lowered):
        return today

    match = DATE_RE.search(value)
    if not match:
        return None
    day, month, year = match.groups()
    year_number = int(year)
    if year_number < 100:
        year_number += 2000
    try:
        return date(year_number, int(month), int(day))
    except ValueError:
        return None


def _tail_after_amount(value: str) -> str:
    match = AMOUNT_RE.search(value)
    if not match:
        return ""
    return _clean(value[match.end() :])


def _strip_category_prefix(value: str) -> str:
    value = re.sub(r"^(?:на|за|в|по)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:сегодня|вчера|завтра|вечером|утром|днем|днём)\b", "", value, flags=re.IGNORECASE)
    return _clean(value)


def _remove_amount_and_date(value: str) -> str:
    value = DATE_RE.sub("", value)
    value = AMOUNT_RE.sub("", value)
    return _clean(value)


def parse_message(text: str) -> ParsedCommand:
    original = _clean(text)
    lowered = original.lower()

    if lowered in {"/start", "/help", "help", "помощь", "что умеешь"}:
        return ParsedCommand(action="help")

    if lowered in {"/balance", "баланс", "мой баланс", "сколько денег"}:
        return ParsedCommand(action="balance")

    if lowered in {"/last", "последние операции", "последние траты", "история"}:
        return ParsedCommand(action="last")

    if lowered.startswith("/paid "):
        return ParsedCommand(action="mark_paid", target=_clean(original.split(maxsplit=1)[1]))

    clear_match = re.fullmatch(r"/(?:clear|reset)(?:\s+(transactions|subscriptions|budgets|all))?(?:\s+(yes|да|confirm|подтверждаю))?", lowered)
    if clear_match:
        target = clear_match.group(1) or "all"
        confirmed = clear_match.group(2) is not None
        if not confirmed:
            return ParsedCommand(
                action="clear",
                target=target,
                error=f"Это удалит данные: {target}. Для подтверждения напиши /clear {target} yes",
            )
        return ParsedCommand(action="clear", target=target)

    russian_clear_match = re.fullmatch(
        r"(?:очисти|очистить|сбрось|сбросить)\s+(операции|транзакции|подписки|лимиты|бюджеты|базу|всё|все)(?:\s+(да|подтверждаю))?",
        lowered,
    )
    if russian_clear_match:
        aliases = {
            "операции": "transactions",
            "транзакции": "transactions",
            "подписки": "subscriptions",
            "лимиты": "budgets",
            "бюджеты": "budgets",
            "базу": "all",
            "всё": "all",
            "все": "all",
        }
        target = aliases[russian_clear_match.group(1)]
        if russian_clear_match.group(2) is None:
            return ParsedCommand(
                action="clear",
                target=target,
                error=f"Это удалит данные: {target}. Для подтверждения напиши /clear {target} yes",
            )
        return ParsedCommand(action="clear", target=target)

    paid_match = re.search(
        r"^(?:оплатил|оплатила|оплачено)\s+(?:подписк[ау]\s+)?(.+)$|^подписка\s+(.+?)\s+оплачена$",
        lowered,
        flags=re.IGNORECASE,
    )
    if paid_match:
        target = paid_match.group(1) or paid_match.group(2)
        return ParsedCommand(action="mark_paid", target=_clean(target))

    if re.fullmatch(r"отч[её]т\s+за\s+(?:день|сегодня)", lowered):
        return ParsedCommand(action="report", period="day")
    if re.fullmatch(r"отч[её]т\s+за\s+недел[юи]", lowered):
        return ParsedCommand(action="report", period="week")
    if re.fullmatch(r"отч[её]т\s+за\s+месяц", lowered):
        return ParsedCommand(action="report", period="month")

    if "подписк" in lowered and re.search(r"^(?:добавь|добавить|создай|создать)", lowered):
        amount = _parse_amount(original)
        if amount is None:
            return ParsedCommand(action="subscription", error="Укажи сумму подписки, например: добавь подписку YouTube 299 дата 12.03.2026")
        next_payment_date = _parse_date(original)
        if next_payment_date is None:
            return ParsedCommand(action="subscription", error="Укажи дату оплаты, например: дата 12.03.2026")
        name_part = re.sub(r"^(?:добавь|добавить|создай|создать)\s+подписк[ау]\s+", "", original, flags=re.IGNORECASE)
        name = _remove_amount_and_date(name_part)
        name = re.sub(r"\bдата\b", "", name, flags=re.IGNORECASE)
        name = _clean(name)
        if not name:
            return ParsedCommand(action="subscription", error="Укажи название подписки.")
        return ParsedCommand(
            action="subscription",
            amount=amount,
            name=name,
            next_payment_date=next_payment_date,
        )

    budget_match = re.search(r"^лимит\s+на\s+(.+)$", original, flags=re.IGNORECASE)
    if budget_match:
        rest = budget_match.group(1)
        amount = _parse_amount(rest)
        if amount is None:
            return ParsedCommand(action="budget", error="Укажи сумму лимита, например: лимит на такси 5000")
        category = _remove_amount_and_date(rest) or "прочее"
        return ParsedCommand(action="budget", amount=amount, category=category)

    if re.search(r"\b(?:потратил|потратила|потрачено|заплатил|заплатила|купил|купила|оплатил|оплатила)\b", lowered):
        amount = _parse_amount(original)
        if amount is None:
            return ParsedCommand(action="transaction", type="expense", error="Не вижу сумму. Напиши, например: потратил 450 на такси")
        category_tail = _tail_after_amount(original)
        category = _strip_category_prefix(category_tail) or "прочее"
        return ParsedCommand(
            action="transaction",
            type="expense",
            amount=amount,
            category=category,
            note=original,
        )

    income_match = re.search(r"^(?:пришл[ао]|получил|получила)\s+(.+)$", original, flags=re.IGNORECASE)
    if income_match:
        amount = _parse_amount(original)
        if amount is None:
            return ParsedCommand(action="transaction", type="income", error="Не вижу сумму. Напиши, например: пришла зп 100000")
        before_amount = original[: AMOUNT_RE.search(original).start()]
        category = re.sub(r"^(?:пришл[ао]|получил|получила)\s+", "", before_amount, flags=re.IGNORECASE)
        category = _clean(category) or "прочее"
        return ParsedCommand(
            action="transaction",
            type="income",
            amount=amount,
            category=category,
            note=original,
        )

    if re.search(r"\b(?:заработал|заработала)\b", lowered):
        amount = _parse_amount(original)
        if amount is None:
            return ParsedCommand(action="transaction", type="income", error="Не вижу сумму. Напиши, например: заработал 100000")
        category_tail = _strip_category_prefix(_tail_after_amount(original))
        return ParsedCommand(
            action="transaction",
            type="income",
            amount=amount,
            category=category_tail or "зп",
            note=original,
        )

    return ParsedCommand(action="unknown")


def parse_screenshot_text(text: str) -> ParsedCommand:
    original = _clean(text)
    lowered = original.lower()
    if not original:
        return ParsedCommand(action="unknown")

    total_match = re.search(
        r"(?:итого|сумма|оплата|покупка|списание|к\s+оплате|всего)[^0-9]{0,40}"
        r"(\d+(?:[.,]\d{1,2})?)",
        lowered,
        flags=re.IGNORECASE,
    )
    amount_matches = list(AMOUNT_RE.finditer(original))
    has_transaction_hint = SCREENSHOT_TRANSACTION_HINT_RE.search(original) is not None

    if total_match:
        amount = float(total_match.group(1).replace(",", "."))
    elif len(amount_matches) == 1 and has_transaction_hint:
        amount = _parse_amount(original)
    elif len(amount_matches) > 1:
        return ParsedCommand(
            action="transaction",
            type="expense",
            error="На скриншоте несколько сумм без явного итога или списания. Пришли чек/банковскую операцию или напиши сумму текстом.",
        )
    else:
        amount = None

    if amount is None:
        return ParsedCommand(action="transaction", type="expense", error="Не вижу сумму на скриншоте.")

    category = "чек"
    merchant_match = re.search(
        r"(?:магазин|получатель|мерчант|merchant)[:\s]+(.+?)(?=\s+(?:итого|сумма|оплата|покупка|списание|к\s+оплате|всего)\b|$)",
        original,
        flags=re.IGNORECASE,
    )
    if merchant_match:
        category = _clean(merchant_match.group(1)).lower()[:40]

    return ParsedCommand(
        action="transaction",
        type="expense",
        amount=amount,
        category=category,
        note=f"Распознано со скриншота: {original}",
    )
