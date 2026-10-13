"""Video *page* links (vk.com/vk.ru/video..., youtu.be, vkvideo.ru) cannot
be fetched as plain files; they are handed to yt-dlp (optional dependency).

Mirrors vk_video_download.py: ``--no-playlist``, a download archive for
resume, per-URL log files, a failed list, capped quality by default.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DEFAULT_QUALITY = "url360/url240/worst[height<=360]/worst"
DEFAULT_WORKERS = 5
TIMEOUT = 1200
LOG_NAME_BAD = re.compile(r"[^\w.-]+")


def find_ytdlp() -> str | None:
    """Path to a usable yt-dlp executable, or None if unavailable."""
    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if exe:
        return exe
    try:
        import yt_dlp  # type: ignore

        return str(Path(yt_dlp.__file__).with_name("__main__.py"))
    except Exception:
        return None


@dataclass
class VideoResult:
    url: str
    status: str  # "done" | "failed"
    info: str = ""


def _log_name(url: str) -> str:
    return LOG_NAME_BAD.sub("_", url)[:120] + ".log"


def _cred_args() -> list[str]:
    if os.environ.get("VK_USER"):
        return ["--username", os.environ["VK_USER"], "--password", os.environ.get("VK_PASS", "")]
    return []


def download_video_page(
    url: str,
    out_dir: Path,
    quality: str = DEFAULT_QUALITY,
    timeout: int = TIMEOUT,
    ytdlp: str | None = None,
) -> VideoResult:
    out_dir = Path(out_dir)
    log_dir = out_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / "download_archive.txt"
    log_file = log_dir / _log_name(url)
    exe = ytdlp or find_ytdlp()
    if not exe:
        return VideoResult(url, "failed", "yt-dlp not found")
    argv = [
        exe,
        "--no-playlist",
        "--download-archive",
        str(archive),
        "--socket-timeout",
        "30",
        "--retries",
        "3",
        "-f",
        quality,
        "-o",
        str(out_dir / "%(title).100B [%(id)s].%(ext)s"),
        *_cred_args(),
        url,
    ]
    with open(log_file, "w", encoding="utf-8", errors="ignore") as lf:
        try:
            proc = subprocess.run(
                argv,
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return VideoResult(url, "failed", "timeout")
    if proc.returncode == 0:
        return VideoResult(url, "done", "")
    tail = ""
    try:
        with open(log_file, encoding="utf-8", errors="ignore") as f:
            tail = f.readlines()[-1].strip()[:180]
    except (OSError, IndexError):
        pass
    return VideoResult(url, "failed", tail or f"rc={proc.returncode}")


def download_video_pages(
    urls: list[str],
    out_dir: Path,
    workers: int = DEFAULT_WORKERS,
    quality: str = DEFAULT_QUALITY,
    timeout: int = TIMEOUT,
    on_done: Callable[[VideoResult], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> list[VideoResult]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[VideoResult] = []
    failed_path = out_dir / "failed.txt"
    lock = threading.Lock()

    def one(url: str) -> VideoResult:
        res = download_video_page(url, out_dir, quality=quality, timeout=timeout)
        if res.status == "failed":
            with lock, open(failed_path, "a", encoding="utf-8") as f:
                f.write(url + "\t" + res.info + "\n")
        return res

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(one, u): u for u in urls}
        for fut in as_completed(futures):
            if stop and stop():
                break
            res = fut.result()
            results.append(res)
            if on_done:
                on_done(res)
    return results
