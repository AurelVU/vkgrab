"""Content-addressed download cache with an append-only manifest.

Cache layout: ``<cache_root>/<md5[:2]>/<md5[2:4]>/<md5>.<ext>``.
Manifest: ``<cache_root>/manifest.tsv`` with rows
``url<TAB>status<TAB>cache_path<TAB>size`` — append-only, so a crash
mid-run never loses more than one line and re-runs resume by skipping
rows whose status is ``ok``.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from .util import file_ext, url_md5

DEFAULT_WORKERS = 8
DEFAULT_MAX_RETRIES = 4
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 45

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class DownloadResult:
    url: str
    status: str  # "ok" | "exists" | "failed"
    cache_path: Path | None
    size: int
    error: str = ""


@dataclass
class DownloadCounters:
    done: int = 0
    exists: int = 0
    failed: int = 0
    bytes: int = 0


class CacheDownloader:
    def __init__(
        self,
        cache_root: Path,
        workers: int = DEFAULT_WORKERS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        connect_timeout: int = CONNECT_TIMEOUT,
        read_timeout: int = READ_TIMEOUT,
        retry_sleep: float = 1.0,
    ) -> None:
        self.cache_root = Path(cache_root)
        self.workers = max(1, int(workers))
        self.max_retries = max(1, int(max_retries))
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.retry_sleep = retry_sleep
        self.manifest_path = self.cache_root / "manifest.tsv"
        self.failed_path = self.cache_root / "failed_urls.txt"
        self.counters = DownloadCounters()
        self._tls = threading.local()
        self._manifest_lock = threading.Lock()
        self._counters_lock = threading.Lock()
        self._stop = threading.Event()

    # -- cache paths -----------------------------------------------------

    def cache_path_for(self, url: str) -> Path:
        h = url_md5(url)
        return self.cache_root / h[:2] / h[2:4] / f"{h}.{file_ext(url)}"

    # -- manifest --------------------------------------------------------

    def load_manifest(self) -> dict[str, str]:
        """url -> cache_path for every URL recorded as ok."""
        done: dict[str, str] = {}
        if self.manifest_path.exists():
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) >= 3 and parts[1] == "ok":
                        done[parts[0]] = parts[2]
        return done

    def append_manifest(self, url: str, status: str, path: Path | str, size: int) -> None:
        with self._manifest_lock:
            with open(self.manifest_path, "a", encoding="utf-8") as f:
                f.write(f"{url}\t{status}\t{path}\t{size}\n")

    # -- sessions --------------------------------------------------------

    def _session(self) -> requests.Session:
        if not hasattr(self._tls, "session"):
            s = requests.Session()
            s.headers["User-Agent"] = USER_AGENT
            self._tls.session = s
        return self._tls.session

    # -- downloading -----------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def download_one(self, url: str) -> DownloadResult:
        cache_path = self.cache_path_for(url)
        if cache_path.exists() and cache_path.stat().st_size > 0:
            with self._counters_lock:
                self.counters.exists += 1
            return DownloadResult(url, "exists", cache_path, cache_path.stat().st_size)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        session = self._session()
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            if self._stop.is_set():
                return DownloadResult(url, "failed", None, 0, "stopped")
            try:
                with session.get(
                    url,
                    stream=True,
                    timeout=(self.connect_timeout, self.read_timeout),
                    allow_redirects=True,
                ) as resp:
                    resp.raise_for_status()
                    fd, tmp_name = tempfile.mkstemp(
                        prefix=".dl_", dir=str(cache_path.parent)
                    )
                    size = 0
                    try:
                        with os.fdopen(fd, "wb") as out:
                            for chunk in resp.iter_content(chunk_size=256 * 1024):
                                if self._stop.is_set():
                                    raise InterruptedError("stopped")
                                if chunk:
                                    out.write(chunk)
                                    size += len(chunk)
                        if size == 0:
                            raise IOError("empty body")
                        os.replace(tmp_name, str(cache_path))
                    except BaseException:
                        try:
                            os.unlink(tmp_name)
                        except OSError:
                            pass
                        raise
                with self._counters_lock:
                    self.counters.done += 1
                    self.counters.bytes += size
                return DownloadResult(url, "ok", cache_path, size)
            except Exception as exc:  # noqa: BLE001 - record and move on
                last_err = exc
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 3) * self.retry_sleep)
        with self._counters_lock:
            self.counters.failed += 1
        with open(self.failed_path, "a", encoding="utf-8") as f:
            f.write(f"{url}\t{last_err}\n")
        return DownloadResult(url, "failed", None, 0, str(last_err))

    def download_all(
        self,
        urls: list[str],
        on_done: Callable[[DownloadResult], None] | None = None,
    ) -> dict[str, DownloadResult]:
        """Download every URL once into the cache. Skips URLs already ok in
        the manifest unless they are missing on disk."""
        cached = self.load_manifest()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        to_download = []
        results: dict[str, DownloadResult] = {}
        for url in urls:
            cpath = cached.get(url)
            if cpath and Path(cpath).exists() and Path(cpath).stat().st_size > 0:
                res = DownloadResult(url, "exists", Path(cpath), Path(cpath).stat().st_size)
                results[url] = res
                with self._counters_lock:
                    self.counters.exists += 1
            else:
                to_download.append(url)
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self.download_one, u): u for u in to_download}
            for fut in as_completed(futures):
                res = fut.result()
                results[res.url] = res
                if res.status in ("ok", "exists") and res.cache_path:
                    self.append_manifest(res.url, "ok", res.cache_path, res.size)
                if on_done:
                    on_done(res)
        return results
