import importlib.util
import sys
from pathlib import Path


def load_auth_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "ziniu_auth_login_extracted.py"
    spec = importlib.util.spec_from_file_location("finance_ziniu_start_payload_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_start_browser_payload_keeps_browser_id_and_oauth():
    module = load_auth_module()

    payload = module.ZiniuAuthLogin.build_start_browser_payload(
        {
            "browserId": "27540429911740",
            "browserOauth": "oauth-token",
        }
    )

    assert payload["action"] == "startBrowser"
    assert payload["browserId"] == "27540429911740"
    assert payload["browserOauth"] == "oauth-token"
    assert None not in payload.values()


def test_start_browser_payload_uses_oauth_when_browser_id_missing():
    module = load_auth_module()

    payload = module.ZiniuAuthLogin.build_start_browser_payload(
        {
            "browserId": None,
            "browserOauth": "oauth-token",
        }
    )

    assert "browserId" not in payload
    assert payload["browserOauth"] == "oauth-token"
    assert None not in payload.values()


def test_ziniu_helper_reads_api_url_and_port_from_environment(monkeypatch):
    module = load_auth_module()
    monkeypatch.setenv("ZINIAO_API_URL", "http://127.0.0.1:16888")
    monkeypatch.setenv("ZINIAO_WEBDRIVER_PORT", "16888")

    helper = module.ZiniuAuthLogin(user_info={})

    assert helper.api_url == "http://127.0.0.1:16888"
    assert helper.webdriver_port == 16888


def test_get_shop_info_matches_browser_name_after_normalization(monkeypatch):
    module = load_auth_module()
    helper = module.ZiniuAuthLogin(user_info={})

    monkeypatch.setattr(
        helper,
        "send_http",
        lambda payload: {
            "statusCode": "0",
            "browserList": [
                {
                    "browserName": "A1 主账号 QXM",
                    "browserId": "browser-1",
                    "browserOauth": "oauth-1",
                }
            ],
        },
    )

    info, error = helper.get_shop_info("A1-主账号-QXM")

    assert error == ""
    assert info["browserId"] == "browser-1"


def test_get_shop_info_not_found_reports_browser_samples(monkeypatch):
    module = load_auth_module()
    helper = module.ZiniuAuthLogin(user_info={})

    monkeypatch.setattr(
        helper,
        "send_http",
        lambda payload: {
            "statusCode": "0",
            "browserList": [
                {"browserName": "C1主账号"},
                {"browserName": "TIKTOK-POP-E1"},
            ],
        },
    )

    info, error = helper.get_shop_info("A1-主账号-QXM")

    assert info is None
    assert "account not found in browserList" in error
    assert "count=2" in error
    assert "C1主账号" in error
    assert "TIKTOK-POP-E1" in error


def test_get_shop_info_empty_browser_list_reports_client_state(monkeypatch):
    module = load_auth_module()
    helper = module.ZiniuAuthLogin(user_info={})

    monkeypatch.setattr(
        helper,
        "send_http",
        lambda payload: {
            "statusCode": "0",
            "browserList": [],
        },
    )

    info, error = helper.get_shop_info("A21POP")

    assert info is None
    assert "browserList is empty" in error
    assert "account not found" not in error


def test_ensure_client_online_accepts_empty_browser_list_without_restart(monkeypatch, tmp_path):
    module = load_auth_module()
    helper = module.ZiniuAuthLogin(user_info={})
    install_dir = tmp_path / "ZiNiao"
    install_dir.mkdir()
    exe = install_dir / "ziniao.exe"
    exe.write_text("", encoding="utf-8")
    calls = []

    monkeypatch.setattr(helper, "send_http", lambda payload: {"statusCode": "0", "browserList": []})
    monkeypatch.setattr(helper, "_detect_install_folder", lambda: str(install_dir))
    monkeypatch.setattr(helper, "kill_all_processes", lambda: calls.append("kill"))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    class FakePopen:
        def __init__(self, cmd, cwd=None, shell=False, env=None):
            calls.append(("popen", cmd, cwd))

    monkeypatch.setattr(module.subprocess, "Popen", FakePopen)

    ok, error = helper.ensure_client_online()

    assert ok
    assert error == ""
    assert calls == []


def test_detect_install_folder_prefers_running_process_over_config_env(monkeypatch, tmp_path):
    module = load_auth_module()
    helper = module.ZiniuAuthLogin(user_info={})
    configured_dir = tmp_path / "configured" / "ZiNiao"
    running_dir = tmp_path / "running" / "ZiNiao"
    configured_dir.mkdir(parents=True)
    running_dir.mkdir(parents=True)
    (configured_dir / "ziniao.exe").write_text("", encoding="utf-8")
    running_exe = running_dir / "ziniao.exe"
    running_exe.write_text("", encoding="utf-8")

    class FakeProcess:
        info = {"name": "ziniao.exe", "exe": str(running_exe)}

    monkeypatch.setenv("ZINIAO_INSTALL_DIR", str(configured_dir))
    monkeypatch.setattr(module.psutil, "process_iter", lambda fields: [FakeProcess()])

    assert helper._detect_install_folder() == str(running_dir)
