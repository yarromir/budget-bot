from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date
from typing import Any, cast

from .parser import Action, ParsedCommand

logger = logging.getLogger(__name__)

DEFAULT_NOUS_MODEL = "Hermes-4-70B"
DEFAULT_NOUS_BASE_URL = "https://inference-api.nousresearch.com/v1/chat/completions"
_ALLOWED_ACTIONS = {
    "transaction",
    "subscription",
    "budget",
    "report",
    "mark_paid",
    "balance",
    "last",
    "help",
    "unknown",
}
_ALLOWED_TYPES = {"income", "expense"}
_ALLOWED_PERIODS = {"day", "week", "month"}


def is_llm_enabled() -> bool:
    return bool(os.getenv("NOUS_API_KEY"))


def parse_message_with_llm(text: str) -> ParsedCommand:
    """Parse a free-form Russian finance message with Nous Hermes API."""
    api_key = os.getenv("NOUS_API_KEY")
    if not api_key:
        return ParsedCommand(action="unknown")

    payload = {
        "model": os.getenv("NOUS_MODEL", DEFAULT_NOUS_MODEL),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты парсер сообщений для Telegram-бота учета финансов. "
                    "Верни только JSON без markdown. Поддержанные action: transaction, subscription, "
                    "budget, report, mark_paid, balance, last, help, unknown. "
                    "Для transaction нужны type (income или expense), amount, category. "
                    "Для subscription нужны name, amount, next_payment_date в YYYY-MM-DD. "
                    "Для budget нужны category и amount. Для report period: day, week или month. "
                    "Для mark_paid нужен target. Если не уверен или нет суммы для операции, верни unknown. "
                    "Категорию делай короткой на русском, без предлогов."
                ),
            },
            {"role": "user", "content": text},
        ],
    }
    request = urllib.request.Request(
        os.getenv("NOUS_BASE_URL", DEFAULT_NOUS_BASE_URL),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    timeout = float(os.getenv("NOUS_TIMEOUT_SECONDS", "8"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.warning("Nous Hermes parsing failed: %s", exc)
        return ParsedCommand(action="unknown")

    content = _extract_content(data)
    if not content:
        return ParsedCommand(action="unknown")
    try:
        parsed_data = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("Nous Hermes returned invalid JSON: %s", exc)
        return ParsedCommand(action="unknown")
    return _command_from_json(parsed_data, note=text)


def _extract_content(data: dict[str, Any]) -> str | None:
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        return content if isinstance(content, str) else None
    return None


def _command_from_json(data: dict[str, Any], note: str) -> ParsedCommand:
    action = str(data.get("action", "unknown"))
    if action not in _ALLOWED_ACTIONS:
        return ParsedCommand(action="unknown")

    amount = _as_float(data.get("amount"))
    category = _as_clean_str(data.get("category"))
    tx_type = _as_clean_str(data.get("type"))
    if action == "transaction":
        if tx_type not in _ALLOWED_TYPES or amount is None:
            return ParsedCommand(action="unknown")
        return ParsedCommand(
            action="transaction",
            type=tx_type,
            amount=amount,
            category=category or "прочее",
            note=note,
        )

    if action == "subscription":
        name = _as_clean_str(data.get("name"))
        next_payment_date = _as_date(data.get("next_payment_date"))
        if not name or amount is None or next_payment_date is None:
            return ParsedCommand(action="unknown")
        return ParsedCommand(
            action="subscription",
            name=name,
            amount=amount,
            next_payment_date=next_payment_date,
        )

    if action == "budget":
        if not category or amount is None:
            return ParsedCommand(action="unknown")
        return ParsedCommand(action="budget", category=category, amount=amount)

    if action == "report":
        period = _as_clean_str(data.get("period"))
        return ParsedCommand(action="report", period=period if period in _ALLOWED_PERIODS else "day")

    if action == "mark_paid":
        return ParsedCommand(action="mark_paid", target=_as_clean_str(data.get("target")))

    return ParsedCommand(action=cast(Action, action))


def _as_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        normalized = value.replace(" ", "").replace("\u00a0", "").replace(",", ".")
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def _as_clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _as_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
