from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from .db import FinanceDB
from .ocr import recognize_screenshot
from .parser import ParsedCommand, parse_message, parse_screenshot_text
from .reminders import start_reminder_scheduler
from .report import generate_html_report


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

router = Router()
db = FinanceDB()


def parse_allowed_user_ids(raw: str | None = None) -> set[int]:
    value = os.getenv("ALLOWED_USER_IDS", "") if raw is None else raw
    result: set[int] = set()
    for part in value.replace(";", ",").split(","):
        item = part.strip()
        if item:
            result.add(int(item))
    return result


ALLOWED_USER_IDS = parse_allowed_user_ids()


def _is_allowed(user_id: int | None) -> bool:
    return user_id is not None and user_id in ALLOWED_USER_IDS


async def _deny(message: Message) -> None:
    await message.answer("Доступ закрыт. Добавь свой Telegram user id в ALLOWED_USER_IDS.")


def _help_text() -> str:
    return (
        "Понимаю фразы:\n"
        "• потратил 450 на такси\n"
        "• заплатил 450 за такси\n"
        "• пришла зп 100000\n"
        "• заработал 100000\n"
        "• добавь подписку YouTube 299 дата 12.03.2026\n"
        "• лимит на такси 5000\n"
        "• отчет за день / неделю / месяц\n"
        "• /paid 3 или оплатил подписку YouTube\n"
        "\nПришли скриншот чека или банковской операции — "
        "я распознаю текст и попробую записать расход."
    )


async def _handle_transaction(message: Message, parsed: ParsedCommand) -> None:
    assert message.from_user is not None
    assert parsed.type is not None
    assert parsed.amount is not None
    transaction_id = db.add_transaction(
        user_id=message.from_user.id,
        type_=parsed.type,
        amount=parsed.amount,
        category=parsed.category,
        note=parsed.note,
    )
    tx_name = "доход" if parsed.type == "income" else "расход"
    answer = f"Записал {tx_name}: {parsed.amount:.2f}, категория: {parsed.category or 'прочее'}."
    if parsed.type == "expense":
        status = db.check_budget_status(message.from_user.id, parsed.category or "прочее")
        if status:
            answer += f"\n\n{status.message}"
    answer += f"\nID операции: {transaction_id}"
    await message.answer(answer)


async def _handle_subscription(message: Message, parsed: ParsedCommand) -> None:
    assert message.from_user is not None
    assert parsed.name is not None
    assert parsed.amount is not None
    assert parsed.next_payment_date is not None
    subscription_id = db.add_subscription(
        user_id=message.from_user.id,
        name=parsed.name,
        amount=parsed.amount,
        next_payment_date=parsed.next_payment_date,
    )
    await message.answer(
        f"Добавил подписку {parsed.name}: {parsed.amount:.2f}, ближайшая оплата {parsed.next_payment_date}.\n"
        f"ID подписки: {subscription_id}"
    )


async def _handle_budget(message: Message, parsed: ParsedCommand) -> None:
    assert message.from_user is not None
    assert parsed.category is not None
    assert parsed.amount is not None
    db.upsert_budget(message.from_user.id, parsed.category, parsed.amount)
    await message.answer(f"Лимит на {parsed.category.lower()} установлен: {parsed.amount:.2f} на текущий месяц.")


async def _handle_report(message: Message, parsed: ParsedCommand) -> None:
    assert message.from_user is not None
    path = generate_html_report(message.from_user.id, parsed.period or "day", db_path=db.path)
    await message.answer_document(FSInputFile(path), caption="Готово, отчёт в HTML.")


async def _mark_paid(user_id: int, target: str | None) -> str:
    if not target:
        return "Укажи ID или название подписки, например: /paid 3"
    subscription = db.find_subscription(user_id, target)
    if not subscription:
        return "Не нашёл активную подписку."
    updated = db.mark_subscription_paid(int(subscription["id"]), user_id=user_id)
    if not updated:
        return "Не смог отметить подписку оплаченной."
    return f"Отметил {updated['name']} оплаченной. Следующая дата: {updated['next_payment_date']}."


async def _process_recognized_text(message: Message, text: str) -> None:
    parsed = parse_message(text)
    if parsed.action == "unknown":
        parsed = parse_screenshot_text(text)
    if parsed.error:
        await message.answer(f"Распознал текст, но не смог записать операцию: {parsed.error}\n\nТекст: {text}")
        return
    if parsed.action != "transaction":
        await message.answer(
            "Распознал текст, но не нашёл расход или доход, который можно записать.\n\n"
            f"Текст: {text}"
        )
        return
    await message.answer(f"Распознал текст: {text}")
    await _handle_transaction(message, parsed)


async def _download_message_image(message: Message) -> Path | None:
    bot = message.bot
    suffix = ".jpg"
    file_id: str | None = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and message.document.mime_type in {"image/png", "image/jpeg", "image/webp"}:
        file_id = message.document.file_id
        if message.document.file_name:
            suffix = Path(message.document.file_name).suffix or suffix
    if not file_id:
        return None

    with tempfile.NamedTemporaryFile(prefix="finance-bot-screenshot-", suffix=suffix, delete=False) as tmp:
        path = Path(tmp.name)
    await bot.download(file_id, destination=path)
    return path


@router.message(Command("start", "help"))
async def handle_start(message: Message) -> None:
    if not _is_allowed(message.from_user.id if message.from_user else None):
        await _deny(message)
        return
    await message.answer(_help_text())


@router.message(Command("paid"))
async def handle_paid_command(message: Message) -> None:
    if not _is_allowed(message.from_user.id if message.from_user else None):
        await _deny(message)
        return
    args = message.text.split(maxsplit=1)[1] if message.text and len(message.text.split(maxsplit=1)) > 1 else None
    await message.answer(await _mark_paid(message.from_user.id, args))


@router.callback_query(F.data.startswith("sub_paid:"))
async def handle_paid_callback(callback: CallbackQuery) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Доступ закрыт.", show_alert=True)
        return
    subscription_id = callback.data.split(":", 1)[1] if callback.data else ""
    await callback.message.answer(await _mark_paid(callback.from_user.id, subscription_id))
    await callback.answer("Готово")


@router.message(F.photo | (F.document.mime_type.in_({"image/png", "image/jpeg", "image/webp"})))
async def handle_screenshot(message: Message) -> None:
    if not _is_allowed(message.from_user.id if message.from_user else None):
        await _deny(message)
        return
    path = await _download_message_image(message)
    if path is None:
        await message.answer("Пришли скриншот как фото или PNG/JPG файл.")
        return
    try:
        result = await asyncio.to_thread(recognize_screenshot, path)
    finally:
        path.unlink(missing_ok=True)
    if result.error:
        await message.answer(result.error)
        return
    await _process_recognized_text(message, result.text)


@router.message(F.text)
async def handle_text(message: Message) -> None:
    if not _is_allowed(message.from_user.id if message.from_user else None):
        await _deny(message)
        return
    parsed = parse_message(message.text or "")
    if parsed.error:
        await message.answer(parsed.error)
        return
    if parsed.action == "help":
        await message.answer(_help_text())
    elif parsed.action == "transaction":
        await _handle_transaction(message, parsed)
    elif parsed.action == "subscription":
        await _handle_subscription(message, parsed)
    elif parsed.action == "budget":
        await _handle_budget(message, parsed)
    elif parsed.action == "report":
        await _handle_report(message, parsed)
    elif parsed.action == "mark_paid":
        await message.answer(await _mark_paid(message.from_user.id, parsed.target))
    else:
        await message.answer("Не понял фразу. Напиши /help, чтобы посмотреть примеры.")


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    if not ALLOWED_USER_IDS:
        logger.warning("ALLOWED_USER_IDS is empty; nobody can use this bot yet")
    db.initialize()
    bot = Bot(token=token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    start_reminder_scheduler(bot, db=db, allowed_user_ids=ALLOWED_USER_IDS)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
