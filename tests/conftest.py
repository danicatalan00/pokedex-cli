from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def block_undeclared_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordinary tests must inject transports instead of reaching the Internet."""

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is disabled during tests")

    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home"
    data = tmp_path / "xdg-data"
    home.mkdir()
    data.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    yield home
