from __future__ import annotations

from datetime import datetime

import finance_bot.report as report
from finance_bot.db import APP_TZ, FinanceDB


def test_generate_html_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(report, "REPORT_DIR", tmp_path / "reports")
    db_path = tmp_path / "finance.db"
    db = FinanceDB(db_path)
    db.initialize()
    db.add_transaction(1, "expense", 250, "такси", note="потратил 250 на такси", created_at=datetime.now(APP_TZ))

    path = report.generate_html_report(1, "day", db_path=db_path)

    html = (tmp_path / "reports" / path.split("/")[-1]).read_text(encoding="utf-8")
    assert "Финансовый отчёт" in html
    assert "такси" in html
