from datetime import datetime, timezone

from pokedex_cli.infrastructure.diagnostics import DIAGNOSTIC_LOG_ENV, log_failure


def test_diagnostics_are_disabled_by_default(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "diagnostics.log"
    monkeypatch.delenv(DIAGNOSTIC_LOG_ENV, raising=False)
    log_failure("capture", RuntimeError("broken"))
    assert not destination.exists()


def test_opt_in_log_contains_timestamp_context_and_traceback(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "nested" / "diagnostics.log"
    monkeypatch.setenv(DIAGNOSTIC_LOG_ENV, str(destination))
    try:
        raise RuntimeError("broken adapter")
    except RuntimeError as error:
        log_failure(
            "wild encounter",
            error,
            clock=lambda: datetime(2026, 7, 15, 10, tzinfo=timezone.utc),
        )

    contents = destination.read_text()
    assert "2026-07-15T10:00:00+00:00" in contents
    assert "wild encounter" in contents
    assert "RuntimeError: broken adapter" in contents
    assert "Traceback" in contents


def test_unwritable_diagnostic_destination_is_never_fatal(tmp_path) -> None:
    parent_as_file = tmp_path / "file"
    parent_as_file.write_text("occupied")
    log_failure("test", OSError("failure"), destination=parent_as_file / "log")
