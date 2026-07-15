import time

from tests.test_cli_e2e import isolated_environment, run_cli


def test_degraded_hook_returns_prompt_within_latency_budget(tmp_path) -> None:
    environment = isolated_environment(tmp_path)
    environment["PATH"] = "/usr/bin:/bin"

    started = time.perf_counter()
    result = run_cli(environment, "hook", "1")
    elapsed = time.perf_counter() - started

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert elapsed < 3.0
