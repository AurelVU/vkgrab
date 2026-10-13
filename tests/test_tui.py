"""TUI smoke tests: the app constructs, mounts, reacts to keys, quits."""

import asyncio
from pathlib import Path

from textual.widgets import Input


def test_app_constructs() -> None:
    from vkgrab.tui import VkGrabApp

    app = VkGrabApp()
    assert app.TITLE == "vkgrab"


async def _wait_for(pilot, cond, attempts: int = 200, delay: float = 0.05) -> bool:
    for _ in range(attempts):
        if cond():
            return True
        await pilot.pause(delay)
    return False


def test_app_pilot_quit(tmp_path: Path) -> None:
    from vkgrab.tui import VkGrabApp

    async def run() -> None:
        app = VkGrabApp()
        async with app.run_test() as pilot:
            app.query_one("#src", Input).value = str(tmp_path)
            await pilot.pause()
            await pilot.press("q")
        assert not app._job_running

    asyncio.run(run())


def test_app_pilot_invalid_dir_does_not_start(tmp_path: Path) -> None:
    from vkgrab.tui import VkGrabApp

    async def run() -> None:
        app = VkGrabApp()
        async with app.run_test() as pilot:
            app.query_one("#src", Input).value = str(tmp_path / "nope")
            await pilot.pause()
            await pilot.press("s")  # focus is on the Start button after mount
            await pilot.pause()
            assert not app._job_running
            await pilot.press("q")

    asyncio.run(run())


def test_app_pilot_full_job(tmp_path: Path) -> None:
    """Pressing 's' with a valid empty source dir runs scan+download to completion."""
    from vkgrab.tui import VkGrabApp

    async def run() -> None:
        app = VkGrabApp()
        async with app.run_test() as pilot:
            app.query_one("#src", Input).value = str(tmp_path)
            await pilot.pause()
            await pilot.press("s")
            # the job may finish before the first poll on a tiny fixture —
            # accept either the running state or the finished phase
            assert await _wait_for(
                pilot,
                lambda: app._job_running
                or "finished" in app.query_one("#phase").content,
            )
            assert await _wait_for(
                pilot, lambda: "finished" in app.query_one("#phase").content
            )
            await pilot.press("q")

    asyncio.run(run())
