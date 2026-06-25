import json
import os

from finance_crawler.auth import configure_ziniu_client_environment
from finance_crawler.config import load_app_config


def test_ziniu_auth_concurrency_defaults_to_one(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.json").write_text(json.dumps({"runtime": {}}), encoding="utf-8")
    (config_dir / "accounts.local.json").write_text("{}", encoding="utf-8")
    (config_dir / "tasks.json").write_text('{"tasks":[]}', encoding="utf-8")

    config = load_app_config("local", config_dir)

    assert config.ziniu_auth_concurrency() == 1


def test_ziniu_auth_concurrency_reads_runtime_value(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.json").write_text(
        json.dumps({"runtime": {"ziniu_auth_concurrency": 2}}),
        encoding="utf-8",
    )
    (config_dir / "accounts.local.json").write_text("{}", encoding="utf-8")
    (config_dir / "tasks.json").write_text('{"tasks":[]}', encoding="utf-8")

    config = load_app_config("local", config_dir)

    assert config.ziniu_auth_concurrency() == 2


def test_account_module_concurrency_defaults_to_one(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.json").write_text(json.dumps({"runtime": {}}), encoding="utf-8")
    (config_dir / "accounts.local.json").write_text("{}", encoding="utf-8")
    (config_dir / "tasks.json").write_text('{"tasks":[]}', encoding="utf-8")

    config = load_app_config("local", config_dir)

    assert config.account_module_concurrency() == 1


def test_account_module_concurrency_reads_runtime_value(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.json").write_text(
        json.dumps({"runtime": {"account_module_concurrency": 2}}),
        encoding="utf-8",
    )
    (config_dir / "accounts.local.json").write_text("{}", encoding="utf-8")
    (config_dir / "tasks.json").write_text('{"tasks":[]}', encoding="utf-8")

    config = load_app_config("local", config_dir)

    assert config.account_module_concurrency() == 2


def test_final_failed_rerun_count_defaults_to_zero(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.json").write_text(json.dumps({"runtime": {}}), encoding="utf-8")
    (config_dir / "accounts.local.json").write_text("{}", encoding="utf-8")
    (config_dir / "tasks.json").write_text('{"tasks":[]}', encoding="utf-8")

    config = load_app_config("local", config_dir)

    assert config.final_failed_rerun_count() == 0


def test_final_failed_rerun_count_reads_runtime_value(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.json").write_text(
        json.dumps({"runtime": {"final_failed_rerun_count": 1}}),
        encoding="utf-8",
    )
    (config_dir / "accounts.local.json").write_text("{}", encoding="utf-8")
    (config_dir / "tasks.json").write_text('{"tasks":[]}', encoding="utf-8")

    config = load_app_config("local", config_dir)

    assert config.final_failed_rerun_count() == 1


def test_save_run_log_defaults_to_true(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.json").write_text(json.dumps({"runtime": {}}), encoding="utf-8")
    (config_dir / "accounts.local.json").write_text("{}", encoding="utf-8")
    (config_dir / "tasks.json").write_text('{"tasks":[]}', encoding="utf-8")

    config = load_app_config("local", config_dir)

    assert config.save_run_log()


def test_save_run_log_can_be_disabled(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.json").write_text(
        json.dumps({"runtime": {"save_run_log": False}}),
        encoding="utf-8",
    )
    (config_dir / "accounts.local.json").write_text("{}", encoding="utf-8")
    (config_dir / "tasks.json").write_text('{"tasks":[]}', encoding="utf-8")

    config = load_app_config("local", config_dir)

    assert not config.save_run_log()


def test_configure_ziniu_client_environment_sets_install_dir_and_port(monkeypatch):
    monkeypatch.delenv("ZINIAO_INSTALL_DIR", raising=False)
    monkeypatch.delenv("ZINIAO_API_URL", raising=False)
    monkeypatch.delenv("ZINIAO_WEBDRIVER_PORT", raising=False)

    configure_ziniu_client_environment(r"F:\ziniao", "127.0.0.1", 16888)

    assert os.environ["ZINIAO_INSTALL_DIR"] == r"F:\ziniao"
    assert os.environ["ZINIAO_API_URL"] == "http://127.0.0.1:16888"
    assert os.environ["ZINIAO_WEBDRIVER_PORT"] == "16888"
