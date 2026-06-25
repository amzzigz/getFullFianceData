from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from finance_crawler.periods import PeriodRange
from finance_crawler.platforms import shein_funds


class DummyResponse:
    content = b"not really excel"
    headers = {"Content-Type": "application/vnd.ms-excel"}
    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        return None


class DummySession:
    def post(self, _url: str, json: dict, timeout: int) -> DummyResponse:
        return DummyResponse()


def test_pop_funds_exports_to_pop_folder(monkeypatch, tmp_path: Path):
    task = {
        "id": "pop_funds",
        "platform": "pop",
        "export_folder": "提现明细",
        "api": {
            "list_url": "https://example.test/list",
            "export_url": "https://example.test/export",
            "req_system_code": "mws-front",
        },
    }
    period = PeriodRange(
        "monthly",
        datetime(2026, 5, 1),
        datetime(2026, 5, 31, 23, 59, 59),
    )

    monkeypatch.setattr(
        shein_funds,
        "auth_login",
        lambda *_args, **_kwargs: SimpleNamespace(
            success=True,
            message="success",
            cookie="sid=ok",
            user_agent="ua",
            final_url="https://sso.geiwohuo.com/#/home/",
        ),
    )
    monkeypatch.setattr(shein_funds, "build_session", lambda *_args, **_kwargs: DummySession())
    monkeypatch.setattr(shein_funds, "resolve_supplier_id", lambda *_args, **_kwargs: 123)
    monkeypatch.setattr(
        shein_funds,
        "post_json",
        lambda *_args, **_kwargs: {"code": "0", "msg": "OK", "info": {"count": 1}},
    )

    result = shein_funds.export_shein_funds(task, "A1POP", period, Path("auth.py"), tmp_path)

    assert result.success
    assert result.platform == "pop"
    assert "\\downloads\\pop\\" in result.output_path.replace("/", "\\")
    assert "\\captures\\pop\\" in result.capture_path.replace("/", "\\")
