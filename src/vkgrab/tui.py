"""Interactive terminal UI (textual).

One screen: controls at the top (source dir, destination mode, type
checkboxes, workers slider, video quality), then a scan phase with
progress, then a download view — overall progress bar, per-type counters,
last-worker-actions list, scrolling log, speed + ETA.

All network/file work runs in worker threads; the UI is updated through
``App.call_from_thread``. ``q`` quits, ``s`` starts/stops the job.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RadioButton,
    RadioSet,
    RichLog,
)

try:  # Slider existed in textual 0.46–3.x; removed in later releases
    from textual.widgets import Slider

    _HAS_SLIDER = True
except ImportError:  # pragma: no cover - depends on textual version
    Slider = None  # type: ignore[assignment]
    _HAS_SLIDER = False

from .core import CACHE_DIR_NAME, Plan, iter_html_files, scan_html_file
from .downloader import CacheDownloader, DownloadResult
from .placer import plan_placements, place_files
from .util import url_type
from .video import DEFAULT_QUALITY, download_video_pages, find_ytdlp

ACTIVITY_LINES = 8
SCAN_UPDATE_EVERY = 25


class VkGrabApp(App):
    TITLE = "vkgrab"
    SUB_TITLE = "VK archive media downloader"

    CSS = """
    #controls {
        width: 45;
        border-right: solid $primary;
        padding: 0 1;
    }
    #controls Label { margin-top: 1; }
    #status { padding: 0 1; }
    #phase { text-style: bold; }
    #counters { height: auto; }
    #speed { height: auto; }
    #activity { height: 9; border: solid $secondary; }
    #log { height: 1fr; border: solid $primary; }
    """

    BINDINGS = [
        Binding("q", "request_quit", "Quit"),
        Binding("s", "toggle_start", "Start/Stop"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._stop = threading.Event()
        self._job_running = False
        self._t0 = 0.0
        self._done_count = 0
        self._total = 0

    # -- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="controls"):
                yield Label("Source directory:")
                yield Input(placeholder="path to exported VK archive", id="src")
                yield Label("Destination mode:")
                yield RadioSet(
                    RadioButton("Next to HTML (wget-style)", value=True),
                    RadioButton("Single directory"),
                    id="dest_mode",
                )
                yield Label("Destination dir (single-dir mode):")
                yield Input(placeholder="<src>/downloaded_media", id="dest_dir")
                yield Label("Media types:")
                yield Horizontal(
                    Checkbox("images", value=True),
                    Checkbox("audio", value=True),
                    Checkbox("video", value=True),
                    id="types",
                )
                yield Label("Workers:")
                if _HAS_SLIDER:
                    yield Slider(1, 32, 8, id="workers")
                else:
                    yield Input(value="8", id="workers", placeholder="1-32")
                yield Label("Video quality (yt-dlp format):")
                yield Input(value=DEFAULT_QUALITY, id="quality")
                yield Button("Start", id="start", variant="primary")
            with Vertical(id="status"):
                yield Label("idle — press 's' or the Start button", id="phase")
                yield ProgressBar(id="scan_progress", total=None)
                yield ProgressBar(id="overall", total=100)
                yield Label("", id="counters")
                yield Label("", id="speed")
                yield RichLog(id="activity", max_lines=ACTIVITY_LINES)
                yield RichLog(id="log", max_lines=1000)
        yield Footer()

    # -- helpers ---------------------------------------------------------

    def on_mount(self) -> None:
        self.query_one("#start", Button).focus()

    def _ui(self, fn, *args, **kwargs) -> None:
        """Update UI from a worker thread; ignore calls after shutdown."""
        try:
            self.call_from_thread(fn, *args, **kwargs)
        except RuntimeError:
            pass

    def _log(self, msg: str) -> None:
        def _write() -> None:
            stamp = time.strftime("%H:%M:%S")
            self.query_one("#log", RichLog).write(f"[{stamp}] {msg}")
        self._ui(_write)

    def _activity(self, msg: str) -> None:
        def _write() -> None:
            self.query_one("#activity", RichLog).write(msg)
        self._ui(_write)

    def _phase(self, msg: str) -> None:
        def _set() -> None:
            self.query_one("#phase", Label).update(msg)
        self._ui(_set)

    def _counters(self, by_type: dict[str, list[int]]) -> None:
        def _set() -> None:
            parts = []
            for t in ("images", "audio", "video"):
                f, d, fl, s = by_type.get(t, [0, 0, 0, 0])
                parts.append(f"{t}: {d + fl + s}/{f} (ok {d}, fail {fl}, skip {s})")
            self.query_one("#counters", Label).update("   ".join(parts))
        self._ui(_set)

    def _speed(self) -> None:
        elapsed = max(time.time() - self._t0, 0.001)
        rate = self._done_count / elapsed
        remaining = max(self._total - self._done_count, 0)
        eta = remaining / rate if rate > 0 else 0
        def _set() -> None:
            self.query_one("#speed", Label).update(
                f"speed {rate:.1f} files/s   ETA {eta:.0f}s"
            )
        self._ui(_set)

    def _progress(self, done: int) -> None:
        def _set() -> None:
            self.query_one("#overall", ProgressBar).update(progress=done)
        self._ui(_set)

    # -- start/stop ------------------------------------------------------

    def action_request_quit(self) -> None:
        if isinstance(self.focused, Input):
            return
        self.exit()

    def action_toggle_start(self) -> None:
        if isinstance(self.focused, Input):
            return
        if self._job_running:
            self._stop.set()
            self._log("stop requested — finishing current tasks...")
            return
        src = Path(self.query_one("#src", Input).value.strip()).expanduser()
        if not src.is_dir():
            self._log(f"ERROR: not a directory: {src}")
            return
        self._stop.clear()
        self._job_running = True
        self._t0 = time.time()
        self._done_count = 0
        self.query_one("#start", Button).disabled = True
        self.query_one("#start", Button).label = "Running..."
        self._run_job(src)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.action_toggle_start()

    def _finish(self) -> None:
        self._job_running = False
        def _set() -> None:
            btn = self.query_one("#start", Button)
            btn.disabled = False
            btn.label = "Start"
        self._ui(_set)

    # -- the job ---------------------------------------------------------

    @work(thread=True)
    def _run_job(self, src: Path) -> None:
        try:
            self._job(src)
        except Exception as exc:  # noqa: BLE001 - show and reset UI
            self._log(f"ERROR: {exc}")
        finally:
            self._finish()

    def _job(self, src: Path) -> None:
        dest_mode = (
            "next-to-html"
            if self.query_one("#dest_mode", RadioSet).pressed_index == 0
            else "single-dir"
        )
        dest_raw = self.query_one("#dest_dir", Input).value.strip()
        dest_dir = Path(dest_raw).expanduser() if dest_raw else src / "downloaded_media"
        workers_widget = self.query_one("#workers")
        if _HAS_SLIDER:
            workers = int(workers_widget.value)  # type: ignore[union-attr]
        else:
            try:
                workers = max(1, min(32, int(str(workers_widget.value).strip())))
            except ValueError:
                workers = 8
        quality = self.query_one("#quality", Input).value.strip() or DEFAULT_QUALITY
        types_on = {
            str(cb.label).lower()
            for cb in self.query_one("#types", Horizontal).query(Checkbox)
            if cb.value
        }
        if not types_on:
            self._log("ERROR: no media types selected")
            return

        # --- scan phase ---
        self._phase(f"scanning {src} ...")
        self._log(f"scanning {src} ...")

        def _scan_indeterminate() -> None:
            self.query_one("#scan_progress", ProgressBar).update(total=None)
        self._ui(_scan_indeterminate)
        plan = Plan()
        n = 0
        for path in iter_html_files(src):
            if self._stop.is_set():
                break
            res = scan_html_file(path)
            plan.results.append(res)
            for url in res.media_urls:
                plan.url_to_htmls.setdefault(url, []).append(path)
            plan.video_page_urls.update(res.video_page_urls)
            n += 1
            if n % SCAN_UPDATE_EVERY == 0:
                self._phase(f"scanning {src} ... {n} files, {len(plan.url_to_htmls)} urls")

        by_type: dict[str, list[int]] = {t: [0, 0, 0, 0] for t in types_on}
        keep_urls = []
        for url in plan.unique_media_urls:
            t = url_type(url)
            if t in by_type:
                by_type[t][0] += 1
                keep_urls.append(url)
        # keep scan results consistent with filtered urls for placement
        kept = set(keep_urls)
        for res in plan.results:
            res.media_urls = [u for u in res.media_urls if u in kept]
        plan.url_to_htmls = {u: h for u, h in plan.url_to_htmls.items() if u in kept}

        self._phase(
            f"scan done: {n} html, {len(keep_urls)} urls, "
            f"{len(plan.video_page_urls)} video page links"
        )
        self._log(
            f"scan done: {n} html files, {len(keep_urls)} media urls, "
            f"{len(plan.video_page_urls)} video page links"
        )
        if plan.video_page_urls:
            links = src / "video_page_links.txt"
            links.write_text("\n".join(sorted(plan.video_page_urls)) + "\n", encoding="utf-8")
            self._log(f"video page links -> {links}")

        if self._stop.is_set():
            self._phase("stopped during scan")
            return

        # --- download phase ---
        cache_root = src / CACHE_DIR_NAME
        downloader = CacheDownloader(cache_root, workers=workers)
        self._total = len(keep_urls)

        def _set_total() -> None:
            self.query_one("#overall", ProgressBar).update(total=max(self._total, 1))
        self._ui(_set_total)

        def on_done(res: DownloadResult) -> None:
            self._done_count += 1
            t = url_type(res.url)
            if t in by_type:
                if res.status == "ok":
                    by_type[t][1] += 1
                elif res.status == "exists":
                    by_type[t][3] += 1
                else:
                    by_type[t][2] += 1
            self._activity(f"{res.status}: {res.url}")
            if res.status == "failed":
                self._log(f"FAIL {res.url}: {res.error}")
            self._progress(self._done_count)
            self._counters(by_type)
            self._speed()

        self._phase(f"downloading {len(keep_urls)} urls ({workers} workers) ...")
        results = downloader.download_all(keep_urls, on_done=on_done)
        if self._stop.is_set():
            self._phase("stopped")
            self._log("stopped by user")
            return

        done_map = {u: r.cache_path for u, r in results.items() if r.cache_path}
        for url, cpath in downloader.load_manifest().items():
            done_map.setdefault(url, Path(cpath))

        # --- placement phase ---
        self._phase("placing files ...")
        placements, missing = plan_placements(plan, done_map, dest_mode, dest_dir)
        counters = place_files(
            placements, workers=workers, stop=self._stop.is_set
        )
        self._log(
            f"placed: {counters.copied} copied, {counters.failed} failed, "
            f"{missing} without cache"
        )

        # --- video pages (optional) ---
        vid_line = "video pages: yt-dlp not found, links saved only"
        if plan.video_page_urls and find_ytdlp():
            self._phase(f"fetching {len(plan.video_page_urls)} video pages ...")
            vids = download_video_pages(
                sorted(plan.video_page_urls),
                src / "videos",
                workers=min(5, workers),
                quality=quality,
                stop=self._stop.is_set,
            )
            ok = sum(1 for v in vids if v.status == "done")
            bad = sum(1 for v in vids if v.status == "failed")
            vid_line = f"video pages: {ok} done, {bad} failed"
        elif plan.video_page_urls:
            self._log("yt-dlp not found — video page links saved to video_page_links.txt")
        self._log(vid_line)

        with downloader._counters_lock:
            dc = downloader.counters
        elapsed = time.time() - self._t0
        self._phase(
            f"finished in {elapsed:.0f}s: {dc.done} downloaded, "
            f"{dc.exists} cached, {dc.failed} failed, {counters.copied} placed"
        )
        self._log(
            f"FINISHED: {dc.done} downloaded ({dc.bytes / 1e6:.1f} MB), "
            f"{dc.exists} already cached, {dc.failed} failed, "
            f"{counters.copied} files placed"
        )


if __name__ == "__main__":
    VkGrabApp().run()
