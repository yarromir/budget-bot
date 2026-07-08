from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Iterable

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .db import APP_TZ, FinanceDB


logger = logging.getLogger(__name__)
REMINDER_DAYS = (7, 3, 1)
REMINDER_TIME = time(hour=10, minute=0, tzinfo=APP_TZ)


def _paid_keyboard(subscription_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отметить оплаченной",
                    callback_data=f"sub_paid:{subscription_id}",
                )
            ]
        ]
    )


async def send_subscription_reminders(
    bot: Bot,
    db: FinanceDB | None = None,
    allowed_user_ids: Iterable[int] | None = None,
    today: date | None = None,
) -> int:
    db = db or FinanceDB()
    db.initialize()
    allowed = set(allowed_user_ids or [])
    if not allowed:
        logger.warning("Subscription reminders skipped: ALLOWED_USER_IDS is empty")
        return 0

    sent = 0
    for days in REMINDER_DAYS:
        rows = db.due_subscriptions(days, allowed_user_ids=allowed, today=today)
        for row in rows:
            suffix = "завтра" if days == 1 else f"через {days} дня"
            text = (
                f"Напоминание: {suffix} оплата подписки {row['name']} "
                f"на {float(row['amount']):.2f}. Дата: {row['next_payment_date']}."
            )
            await bot.send_message(
                chat_id=row["user_id"],
                text=text,
                reply_markup=_paid_keyboard(int(row["id"])),
            )
            sent += 1
    return sent


def _seconds_until_next_run(now: datetime | None = None) -> float:
    current = now or datetime.now(APP_TZ)
    next_run = datetime.combine(current.date(), REMINDER_TIME)
    if current >= next_run:
        next_run += timedelta(days=1)
    return max(1.0, (next_run - current).total_seconds())


async def reminder_loop(
    bot: Bot,
    db: FinanceDB | None = None,
    allowed_user_ids: Iterable[int] | None = None,
) -> None:
    db = db or FinanceDB()
    while True:
        await asyncio.sleep(_seconds_until_next_run())
        try:
            await send_subscription_reminders(bot, db=db, allowed_user_ids=allowed_user_ids)
        except Exception:
            logger.exception("Failed to send subscription reminders")


def start_reminder_scheduler(
    bot: Bot,
    db: FinanceDB | None = None,
    allowed_user_ids: Iterable[int] | None = None,
) -> asyncio.Task[None]:
    return asyncio.create_task(reminder_loop(bot, db=db, allowed_user_ids=allowed_user_ids))
