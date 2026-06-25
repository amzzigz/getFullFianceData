from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from finance_crawler import auth
from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange
from finance_crawler.platforms import platform_fees


def _period() -> PeriodRange:
    tz = ZoneInfo("Asia/Shanghai")
    return PeriodRange(
        "monthly",
        datetime(2026, 5, 1, tzinfo=tz),
        datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )


def test_shein_auth_uses_target_url_fallback(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.py"
    auth_path.write_text("# placeholder\n", encoding="utf-8")
    target_url = "https://sso.geiwohuo.com/#/gsfs/finance-management/supplementary-deduction"
    calls = []

    def fake_fallback(account_name, received_auth_path, target_url="", timeout_seconds=60):
        calls.append(
            {
                "account_name": account_name,
                "auth_path": received_auth_path,
                "target_url": target_url,
                "timeout_seconds": timeout_seconds,
            }
        )
        return auth.AuthResult(
            True,
            "success",
            account=account_name,
            platform="shein",
            cookie="sso=1; session=2;" + "x" * 60,
            user_agent="ua",
            final_url=target_url,
        )

    monkeypatch.setattr(auth, "shein_mws_cookie_login_fallback", fake_fallback)
    auth._AUTH_CACHE.clear()

    result = auth.auth_login("A3-主账号-LYB-1393YB", auth_path, fallback_timeout_seconds=30, target_url=target_url)

    assert result.success
    assert calls == [
        {
            "account_name": "A3-主账号-LYB-1393YB",
            "auth_path": auth_path,
            "target_url": target_url,
            "timeout_seconds": 60,
        }
    ]


def test_platform_fees_passes_target_page_to_auth(tmp_path, monkeypatch):
    target_url = "https://sso.geiwohuo.com/#/gsfs/finance-management/supplementary-deduction"
    calls = []

    class DummySession:
        def __init__(self):
            self.headers = {}

    def fake_auth_login(account_name, auth_path, fallback_timeout_seconds=30, target_url=""):
        calls.append(
            {
                "account_name": account_name,
                "target_url": target_url,
                "fallback_timeout_seconds": fallback_timeout_seconds,
            }
        )
        return auth.AuthResult(
            True,
            "success",
            account=account_name,
            platform="shein",
            cookie="sso=1; session=2;" + "x" * 60,
            user_agent="ua",
            final_url=target_url,
        )

    monkeypatch.setattr(platform_fees, "auth_login", fake_auth_login)
    monkeypatch.setattr(platform_fees, "build_session", lambda *_args, **_kwargs: DummySession())
    monkeypatch.setattr(platform_fees, "resolve_supplier_context", lambda *_args, **_kwargs: {"supplierId": "1"})
    monkeypatch.setattr(platform_fees, "post_json", lambda *_args, **_kwargs: {"code": "0", "info": {"data": []}})
    monkeypatch.setattr(
        platform_fees,
        "wait_download_file_url",
        lambda *_args, **_kwargs: ({"fileExtension": "xlsx", "id": "f1"}, "https://example.com/a.xlsx", [], {}, {}),
    )
    monkeypatch.setattr(platform_fees, "download_file", lambda *_args, **_kwargs: type("Resp", (), {"status_code": 200, "headers": {}, "content": b"x"})())
    monkeypatch.setattr(platform_fees, "write_capture_file", lambda *_args, **_kwargs: "")

    result = platform_fees.export_platform_fees(
        {"id": "shein_platform_fees", "platform": "shein", "target_page": target_url},
        "A3-主账号-LYB-1393YB",
        _period(),
        Path("tools/ziniu_auth_login_extracted.py"),
        tmp_path,
    )

    assert isinstance(result, TaskResult)
    assert result.success
    assert calls[0]["target_url"] == target_url


def test_platform_fees_reuses_shared_auth_result(tmp_path, monkeypatch):
    target_url = "https://sso.geiwohuo.com/#/gsfs/finance-management/supplementary-deduction"

    class DummySession:
        def __init__(self):
            self.headers = {}

    def forbidden_auth_login(*_args, **_kwargs):
        raise AssertionError("module should reuse shared auth result")

    monkeypatch.setattr(platform_fees, "auth_login", forbidden_auth_login)
    monkeypatch.setattr(platform_fees, "build_session", lambda *_args, **_kwargs: DummySession())
    monkeypatch.setattr(platform_fees, "resolve_supplier_context", lambda *_args, **_kwargs: {"supplierId": "1"})
    monkeypatch.setattr(platform_fees, "post_json", lambda *_args, **_kwargs: {"code": "0", "info": {"data": []}})
    monkeypatch.setattr(
        platform_fees,
        "wait_download_file_url",
        lambda *_args, **_kwargs: ({"fileExtension": "xlsx", "id": "f1"}, "https://example.com/a.xlsx", [], {}, {}),
    )
    monkeypatch.setattr(
        platform_fees,
        "download_file",
        lambda *_args, **_kwargs: type("Resp", (), {"status_code": 200, "headers": {}, "content": b"x"})(),
    )
    monkeypatch.setattr(platform_fees, "write_capture_file", lambda *_args, **_kwargs: "")

    result = platform_fees.export_platform_fees(
        {
            "id": "shein_platform_fees",
            "platform": "shein",
            "target_page": target_url,
            "_auth_result": auth.AuthResult(
                True,
                "success",
                account="A3-主账号-LYB-1393YB",
                platform="shein",
                cookie="sso=1; session=2;" + "x" * 60,
                user_agent="ua",
                final_url=target_url,
            ),
        },
        "A3-主账号-LYB-1393YB",
        _period(),
        Path("tools/ziniu_auth_login_extracted.py"),
        tmp_path,
    )

    assert result.success
