from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from .db import APP_TZ, FinanceDB, current_month, month_bounds, normalize_category

STATIC_DIR = Path(__file__).with_name("webapp_static")


def _money(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _json_row(row: object) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def _validate_init_data(init_data: str, bot_token: str) -> dict[str, object] | None:
    if not init_data or not bot_token:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        return None
    try:
        return json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        return None


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path.startswith("/api/"):
        header = request.headers.get("Authorization", "")
        init_data = header.removeprefix("tma ").strip() if header.startswith("tma ") else ""
        bot_token = request.app["bot_token"]
        user = _validate_init_data(init_data, bot_token)
        if not user:
            return web.json_response({"error": "invalid_init_data"}, status=401)
        user_id = int(user.get("id") or 0)
        if user_id not in request.app["allowed_user_ids"]:
            return web.json_response({"error": "forbidden"}, status=403)
        request["user_id"] = user_id
    return await handler(request)


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def summary(request: web.Request) -> web.Response:
    db: FinanceDB = request.app["db"]
    user_id = request["user_id"]
    period = request.query.get("period", "month")
    if period == "month":
        start, end = month_bounds(current_month())
    elif period == "day":
        start = datetime.now(APP_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    else:
        return web.json_response({"error": "unknown_period"}, status=400)
    tx_summary = db.transaction_summary(user_id, start, end)
    expenses = db.expense_by_category(user_id, start, end)
    transactions = [
        _json_row(row)
        for row in db.list_transactions(user_id, start, end)[:20]
    ]
    subscriptions = [_json_row(row) for row in db.list_active_subscriptions(user_id)]
    return web.json_response(
        {
            "summary": tx_summary,
            "expensesByCategory": expenses,
            "transactions": transactions,
            "subscriptions": subscriptions,
        }
    )


async def add_transaction(request: web.Request) -> web.Response:
    payload = await request.json()
    type_ = payload.get("type")
    amount = float(payload.get("amount") or 0)
    category = normalize_category(payload.get("category"))
    if type_ not in {"income", "expense"} or amount <= 0:
        return web.json_response({"error": "bad_transaction"}, status=400)
    tx_id = request.app["db"].add_transaction(
        user_id=request["user_id"],
        type_=type_,
        amount=amount,
        category=category,
        note=payload.get("note") or "Mini App",
    )
    return web.json_response({"id": tx_id, "amount": _money(amount), "category": category})


async def add_budget(request: web.Request) -> web.Response:
    payload = await request.json()
    amount = float(payload.get("amount") or 0)
    category = normalize_category(payload.get("category"))
    if amount <= 0:
        return web.json_response({"error": "bad_budget"}, status=400)
    budget_id = request.app["db"].upsert_budget(request["user_id"], category, amount)
    return web.json_response({"id": budget_id, "amount": _money(amount), "category": category})


async def add_subscription(request: web.Request) -> web.Response:
    payload = await request.json()
    amount = float(payload.get("amount") or 0)
    name = str(payload.get("name") or "").strip()
    next_payment_date = str(payload.get("nextPaymentDate") or "").strip()
    if not name or amount <= 0:
        return web.json_response({"error": "bad_subscription"}, status=400)
    try:
        parsed_date = date.fromisoformat(next_payment_date)
    except ValueError:
        return web.json_response({"error": "bad_date"}, status=400)
    subscription_id = request.app["db"].add_subscription(request["user_id"], name, amount, parsed_date)
    return web.json_response({"id": subscription_id, "name": name, "amount": _money(amount)})


async def mark_subscription_paid(request: web.Request) -> web.Response:
    subscription_id = int(request.match_info["subscription_id"])
    updated = request.app["db"].mark_subscription_paid(subscription_id, user_id=request["user_id"])
    if not updated:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(_json_row(updated))


def create_app(db: FinanceDB | None = None, bot_token: str | None = None, allowed_user_ids: set[int] | None = None) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app["db"] = db or FinanceDB()
    app["bot_token"] = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    app["allowed_user_ids"] = allowed_user_ids or set()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/summary", summary)
    app.router.add_post("/api/transactions", add_transaction)
    app.router.add_post("/api/budgets", add_budget)
    app.router.add_post("/api/subscriptions", add_subscription)
    app.router.add_post("/api/subscriptions/{subscription_id}/paid", mark_subscription_paid)
    app.router.add_static("/static", STATIC_DIR)
    return app


async def start_webapp_server(db: FinanceDB, bot_token: str, allowed_user_ids: set[int]) -> web.AppRunner | None:
    url = os.getenv("TELEGRAM_WEB_APP_URL")
    if not url:
        return None
    host = os.getenv("FINANCE_WEBAPP_HOST", "0.0.0.0")
    port = int(os.getenv("FINANCE_WEBAPP_PORT", "8080"))
    app = create_app(db=db, bot_token=bot_token, allowed_user_ids=allowed_user_ids)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
