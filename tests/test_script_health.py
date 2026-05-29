from ygotrainingbot.script_health import (
    count_script_runtime_errors,
    is_script_runtime_error_message,
    script_health_summary,
)


def test_is_script_runtime_error_message() -> None:
    assert is_script_runtime_error_message(
        '[string "c72989439.lua"]:3: attempt to call a nil value (global \'GetID\')'
    )
    assert not is_script_runtime_error_message('{"event": "script_loaded"}')


def test_count_script_runtime_errors_from_stats() -> None:
    assert count_script_runtime_errors((), script_stats={"runtime_errors": 3}) == 3


def test_count_script_runtime_errors_from_logs() -> None:
    logs = [
        "attempt to call a nil value (global 'GetID')",
        {"event": "script_loaded", "script": "c1.lua"},
        {"event": "script_runtime_error", "message": "CallCardFunction failed"},
    ]
    assert count_script_runtime_errors(logs) == 2


def test_script_health_summary_clean() -> None:
    summary = script_health_summary(
        (),
        script_stats={"runtime_errors": 0, "prelude_loaded": ["constant.lua", "utility.lua"]},
    )
    assert summary["clean"] is True
    assert summary["prelude_loaded"] == ["constant.lua", "utility.lua"]
