from __future__ import annotations

import base64
import html
import os
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .db import APP_TZ, FinanceDB


REPORT_DIR = Path(os.getenv("FINANCE_REPORT_DIR", "~/finance_bot/reports")).expanduser()


def _period_bounds(period: str, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    current = now or datetime.now(APP_TZ)
    if period == "day":
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        title = "за день"
    elif period == "week":
        start = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        title = "за неделю"
    elif period == "month":
        start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        title = "за месяц"
    else:
        raise ValueError("period must be one of: day, week, month")
    return start, end, title


def _money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _chart_to_base64(fig: plt.Figure) -> str:
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _expense_pie(expenses: dict[str, float]) -> str:
    fig, ax = plt.subplots(figsize=(5.8, 4.2))
    fig.patch.set_facecolor("white")
    if expenses:
        labels = list(expenses.keys())
        values = list(expenses.values())
        ax.pie(values, labels=labels, autopct="%1.0f%%", startangle=90)
        ax.set_title("Расходы по категориям")
    else:
        ax.text(0.5, 0.5, "Нет расходов", ha="center", va="center", fontsize=14)
        ax.axis("off")
    return _chart_to_base64(fig)


def _daily_bars(expenses_by_day: dict[str, float], start: datetime, end: datetime) -> str:
    labels: list[str] = []
    values: list[float] = []
    cursor = start.date()
    while cursor < end.date():
        key = cursor.isoformat()
        labels.append(cursor.strftime("%d.%m"))
        values.append(float(expenses_by_day.get(key, 0)))
        cursor += timedelta(days=1)

    fig_width = max(5.8, min(11.0, len(labels) * 0.42))
    fig, ax = plt.subplots(figsize=(fig_width, 4.0))
    fig.patch.set_facecolor("white")
    ax.bar(labels, values, color="#2474A6")
    ax.set_title("Расходы по дням")
    ax.set_ylabel("Сумма")
    ax.tick_params(axis="x", rotation=45)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return _chart_to_base64(fig)


def _transaction_rows(rows: list) -> str:
    if not rows:
        return "<tr><td colspan=\"5\" class=\"muted\">Операций за период нет</td></tr>"
    result = []
    for row in rows:
        created = datetime.fromisoformat(row["created_at"]).strftime("%d.%m.%Y %H:%M")
        tx_type = "Доход" if row["type"] == "income" else "Расход"
        result.append(
            "<tr>"
            f"<td>{html.escape(created)}</td>"
            f"<td>{html.escape(tx_type)}</td>"
            f"<td>{html.escape(row['category'])}</td>"
            f"<td class=\"num\">{_money(float(row['amount']))}</td>"
            f"<td>{html.escape(row['note'] or '')}</td>"
            "</tr>"
        )
    return "\n".join(result)


def _subscription_rows(rows: list) -> str:
    if not rows:
        return "<tr><td colspan=\"3\" class=\"muted\">Активных подписок нет</td></tr>"
    result = []
    for row in rows:
        result.append(
            "<tr>"
            f"<td>{html.escape(row['name'])}</td>"
            f"<td class=\"num\">{_money(float(row['amount']))}</td>"
            f"<td>{html.escape(row['next_payment_date'])}</td>"
            "</tr>"
        )
    return "\n".join(result)


def generate_html_report(user_id: int, period: str = "day", db_path: str | os.PathLike[str] | None = None) -> str:
    db = FinanceDB(db_path)
    db.initialize()
    start, end, title = _period_bounds(period)
    transactions = db.list_transactions(user_id, start, end)
    summary = db.transaction_summary(user_id, start, end)
    subscriptions = db.list_active_subscriptions(user_id)
    expense_categories = db.expense_by_category(user_id, start, end)
    expense_days = db.expense_by_day(user_id, start, end)
    pie_chart = _expense_pie(expense_categories)
    daily_chart = _daily_bars(expense_days, start, end)
    generated_at = datetime.now(APP_TZ).strftime("%d.%m.%Y %H:%M")

    document = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Финансовый отчёт {html.escape(title)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 16px;
      background: #f6f7f9;
      color: #16202a;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      line-height: 1.45;
    }}
    main {{
      width: 100%;
      max-width: 920px;
      margin: 0 auto;
      background: #fff;
      border: 1px solid #e5e8eb;
      border-radius: 8px;
      overflow: hidden;
    }}
    header {{ padding: 20px 18px 8px; }}
    section {{ padding: 12px 18px 20px; border-top: 1px solid #eef0f2; }}
    h1 {{ margin: 0 0 6px; font-size: 26px; line-height: 1.2; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    .muted {{ color: #6c7680; }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      padding: 0 18px 18px;
    }}
    .metric {{
      border: 1px solid #e5e8eb;
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfd;
    }}
    .metric span {{ display: block; color: #6c7680; font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 20px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid #edf0f2; text-align: left; vertical-align: top; }}
    th {{ color: #52606d; font-weight: 600; background: #fbfcfd; }}
    .num {{ text-align: right; white-space: nowrap; }}
    .chart {{ width: 100%; margin: 4px 0 14px; border: 1px solid #e8ecef; border-radius: 8px; }}
    .scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
    @media (max-width: 640px) {{
      body {{ padding: 8px; }}
      h1 {{ font-size: 22px; }}
      .summary {{ grid-template-columns: 1fr; }}
      th, td {{ padding: 8px 6px; font-size: 13px; }}
      .chart {{ border-radius: 6px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Финансовый отчёт {html.escape(title)}</h1>
      <div class="muted">Сформировано: {html.escape(generated_at)}</div>
    </header>
    <div class="summary">
      <div class="metric"><span>Доходы</span><strong>{_money(summary["income"])}</strong></div>
      <div class="metric"><span>Расходы</span><strong>{_money(summary["expense"])}</strong></div>
      <div class="metric"><span>Остаток</span><strong>{_money(summary["balance"])}</strong></div>
    </div>
    <section>
      <h2>Графики</h2>
      <img class="chart" alt="Круговая диаграмма расходов" src="data:image/png;base64,{pie_chart}">
      <img class="chart" alt="Расходы по дням" src="data:image/png;base64,{daily_chart}">
    </section>
    <section>
      <h2>Операции</h2>
      <div class="scroll">
        <table>
          <thead><tr><th>Дата</th><th>Тип</th><th>Категория</th><th class="num">Сумма</th><th>Примечание</th></tr></thead>
          <tbody>{_transaction_rows(transactions)}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Подписки</h2>
      <div class="scroll">
        <table>
          <thead><tr><th>Название</th><th class="num">Сумма/мес</th><th>Ближайшая оплата</th></tr></thead>
          <tbody>{_subscription_rows(subscriptions)}</tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>"""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"finance_report_{user_id}_{period}_{datetime.now(APP_TZ).strftime('%Y%m%d_%H%M%S')}.html"
    path = REPORT_DIR / filename
    path.write_text(document, encoding="utf-8")
    return str(path)
