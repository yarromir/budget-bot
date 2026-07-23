from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo

from .db import APP_TZ, FinanceDB
from .ocr import recognize_screenshot
from .parser import ParsedCommand, parse_message, parse_screenshot_text
from .reminders import start_reminder_scheduler
from .services import FinanceService
from .webapp import start_webapp_server


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

router = Router()
db = FinanceDB()
service = FinanceService(db)


def parse_allowed_user_ids(raw: str | None = None) -> set[int]:
    value = os.getenv("ALLOWED_USER_IDS", "") if raw is None else raw
    result: set[int] = set()
    for part in value.replace(";", ",").split(","):
        item = part.strip()
        if item:
            try:
                result.add(int(item))
            except ValueError:
                logger.warning("Ignoring invalid Telegram user id in ALLOWED_USER_IDS: %s", item)
    return result


ALLOWED_USER_IDS = parse_allowed_user_ids()


def _is_allowed(user_id: int | None) -> bool:
    return user_id is not None and user_id in ALLOWED_USER_IDS


async def _deny(message: Message) -> None:
    await message.answer("Доступ закрыт. Добавь свой Telegram user id в ALLOWED_USER_IDS.")


def _main_keyboard() -> ReplyKeyboardMarkup | None:
    web_app_url = os.getenv("TELEGRAM_WEB_APP_URL")
    if not web_app_url:
        return None
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Открыть бюджет", web_app=WebAppInfo(url=web_app_url))]],
        resize_keyboard=True,
    )


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
        "• /balance или баланс — показать общий баланс\n"
        "• /last или последние операции — последние 10 операций\n"
        "• /paid 3 или оплатил подписку YouTube\n"
        "\nПришли скриншот чека или банковской операции — "
        "я распознаю текст и попробую записать расход."
    )


async def _handle_transaction(message: Message, parsed: ParsedCommand) -> None:
    assert message.from_user is not None
    await message.answer(service.record_transaction(message.from_user.id, parsed).message)


async def _handle_subscription(message: Message, parsed: ParsedCommand) -> None:
    assert message.from_user is not None
    await message.answer(service.add_subscription(message.from_user.id, parsed))


async def _handle_budget(message: Message, parsed: ParsedCommand) -> None:
    assert message.from_user is not None
    await message.answer(service.set_budget(message.from_user.id, parsed))


async def _handle_report(message: Message, parsed: ParsedCommand) -> None:
    assert message.from_user is not None
    path = service.report_path(message.from_user.id, parsed.period or "day")
    await message.answer_document(FSInputFile(path), caption="Готово, отчёт в HTML.")


def _money(value: float) -> str:
    return f"{value:.2f}"


async def _handle_balance(message: Message) -> None:
    assert message.from_user is not None
    summary = db.all_time_summary(message.from_user.id)
    await message.answer(
        "Баланс за всё время:\n"
        f"Доходы: {_money(summary['income'])}\n"
        f"Расходы: {_money(summary['expense'])}\n"
        f"Остаток: {_money(summary['balance'])}"
    )


async def _handle_last(message: Message) -> None:
    assert message.from_user is not None
    rows = db.list_transactions(message.from_user.id, limit=10)
    if not rows:
        await message.answer("Операций пока нет.")
        return
    lines = ["Последние операции:"]
    for row in rows:
        created = row["created_at"]
        try:
            created = datetime.fromisoformat(created).astimezone(APP_TZ).strftime("%d.%m %H:%M")
        except ValueError:
            created = created[:16]
        sign = "+" if row["type"] == "income" else "-"
        lines.append(f"#{row['id']} {created} {sign}{_money(float(row['amount']))} — {row['category']}")
    await message.answer("\n".join(lines))


async def _mark_paid(user_id: int, target: str | None) -> str:
    return service.mark_paid(user_id, target)


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
    await message.bot.download(file_id, destination=path)
    return path


@router.message(Command("start", "help"))
async def handle_start(message: Message) -> None:
    if not _is_allowed(message.from_user.id if message.from_user else None):
        await _deny(message)
        return
    await message.answer(_help_text(), reply_markup=_main_keyboard())


@router.message(Command("balance"))
async def handle_balance_command(message: Message) -> None:
    if not _is_allowed(message.from_user.id if message.from_user else None):
        await _deny(message)
        return
    await _handle_balance(message)


@router.message(Command("last"))
async def handle_last_command(message: Message) -> None:
    if not _is_allowed(message.from_user.id if message.from_user else None):
        await _deny(message)
        return
    await _handle_last(message)


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
    if callback.message:
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
    elif parsed.action == "balance":
        await _handle_balance(message)
    elif parsed.action == "last":
        await _handle_last(message)
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
    webapp_runner = await start_webapp_server(db, token, ALLOWED_USER_IDS)
    if webapp_runner:
        logger.info("Telegram Mini App server started")
    start_reminder_scheduler(bot, db=db, allowed_user_ids=ALLOWED_USER_IDS)
    try:
        await dispatcher.start_polling(bot)
    finally:
        if webapp_runner:
            await webapp_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
