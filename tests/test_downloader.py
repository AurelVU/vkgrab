"""Downloader tests against a local http.server: 200, 404, resume."""

import functools
import http.server
import threading
from pathlib import Path

import pytest

from vkgrab.downloader import CacheDownloader

PAYLOAD = b"\x89PNG-tiny-payload-12345"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102 - keep test output clean
        pass


@pytest.fixture()
def http_server(tmp_path: Path):
    """Serve tmp_path over HTTP on an ephemeral port."""
    handler = functools.partial(_QuietHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_download_ok(http_server: str, tmp_path: Path) -> None:
    (tmp_path / "tiny.png").write_bytes(PAYLOAD)
    dl = CacheDownloader(tmp_path / ".vkgrab_cache", workers=2, max_retries=2)
    url = f"{http_server}/tiny.png"
    results = dl.download_all([url])
    res = results[url]
    assert res.status == "ok"
    assert res.size == len(PAYLOAD)
    assert res.cache_path is not None and res.cache_path.exists()
    assert res.cache_path.read_bytes() == PAYLOAD
    # sharded layout + manifest row
    assert res.cache_path.parent.parent.parent == tmp_path / ".vkgrab_cache"
    manifest = (tmp_path / ".vkgrab_cache" / "manifest.tsv").read_text(encoding="utf-8")
    assert f"{url}\tok\t" in manifest


def test_download_404(http_server: str, tmp_path: Path) -> None:
    dl = CacheDownloader(tmp_path / ".vkgrab_cache", workers=1, max_retries=2, retry_sleep=0.01)
    url = f"{http_server}/missing.jpg"
    results = dl.download_all([url])
    assert results[url].status == "failed"
    assert dl.counters.failed == 1
    assert not dl.cache_path_for(url).exists()
    failed = (tmp_path / ".vkgrab_cache" / "failed_urls.txt").read_text(encoding="utf-8")
    assert url in failed


def test_resume_skips_cached(http_server: str, tmp_path: Path) -> None:
    (tmp_path / "tiny.png").write_bytes(PAYLOAD)
    cache = tmp_path / ".vkgrab_cache"
    dl = CacheDownloader(cache, workers=2, max_retries=2)
    url = f"{http_server}/tiny.png"
    first = dl.download_all([url])
    assert first[url].status == "ok"

    # fresh downloader, same cache: url already in manifest -> "exists"
    dl2 = CacheDownloader(cache, workers=2, max_retries=2)
    second = dl2.download_all([url])
    assert second[url].status == "exists"
    assert dl2.counters.done == 0
    assert dl2.counters.exists == 1
    # manifest has exactly one ok row for the url
    rows = [
        line
        for line in (cache / "manifest.tsv").read_text(encoding="utf-8").splitlines()
        if line.startswith(url + "\t") and "\tok\t" in line
    ]
    assert len(rows) == 1


def test_stop(http_server: str, tmp_path: Path) -> None:
    (tmp_path / "tiny.png").write_bytes(PAYLOAD)
    dl = CacheDownloader(tmp_path / ".vkgrab_cache", workers=1, max_retries=1)
    dl.stop()
    res = dl.download_one(f"{http_server}/tiny.png")
    assert res.status == "failed"
    assert res.error == "stopped"
