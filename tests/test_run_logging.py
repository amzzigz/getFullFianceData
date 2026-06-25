import sys

import main


def test_run_log_capture_writes_stdout_and_stderr(tmp_path):
    config = type(
        "Config",
        (),
        {
            "save_run_log": lambda self: True,
            "log_root": lambda self: tmp_path / "logs",
        },
    )()

    with main.run_log_capture(config) as log_path:
        print("hello stdout")
        sys.stderr.write("hello stderr\n")

    assert log_path is not None
    assert log_path.exists()
    assert log_path.parent.name == "runs"
    text = log_path.read_text(encoding="utf-8")
    assert "hello stdout" in text
    assert "hello stderr" in text


def test_run_log_capture_can_be_disabled(tmp_path):
    config = type(
        "Config",
        (),
        {
            "save_run_log": lambda self: False,
            "log_root": lambda self: tmp_path / "logs",
        },
    )()

    with main.run_log_capture(config) as log_path:
        print("not logged")

    assert log_path is None
    assert not (tmp_path / "logs").exists()
