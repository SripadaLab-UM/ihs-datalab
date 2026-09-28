"""Windows' asyncio noise when DataLab stops is hidden, and nothing else is."""

from __future__ import annotations

import asyncio
import logging

import pytest

from datalab.cli import ignore_windows_connection_resets


class _ProactorBasePipeTransport:
    """Named as asyncio's Windows transport, whose callback raises the noise."""

    def _call_connection_lost(self, exc: Exception | None) -> None:
        raise ConnectionResetError(10054, "An existing connection was forcibly closed")


def _run_callback(callback) -> None:
    async def main() -> None:
        ignore_windows_connection_resets(asyncio.get_running_loop())
        asyncio.get_running_loop().call_soon(callback, None)
        await asyncio.sleep(0.01)

    asyncio.run(main())


def test_the_proactor_connection_reset_is_not_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        _run_callback(_ProactorBasePipeTransport()._call_connection_lost)
    assert "ConnectionResetError" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_other_errors_in_callbacks_are_still_reported(caplog: pytest.LogCaptureFixture) -> None:
    def other(_: object) -> None:
        raise ConnectionResetError(10054, "reset somewhere else")

    def broken(_: object) -> None:
        raise ValueError("a real bug")

    with caplog.at_level(logging.ERROR, logger="asyncio"):
        _run_callback(other)
        _run_callback(broken)
    assert "reset somewhere else" in caplog.text
    assert "a real bug" in caplog.text
