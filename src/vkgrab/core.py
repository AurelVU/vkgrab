"""Headless scanning and planning: walk HTML files, extract media URLs."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .util import (
    TYPE_GROUPS,
    URL_RE,
    VIDEO_PAGE_RE,
    clean_url,
    url_type,
    valid_media_url,
)

CACHE_DIR_NAME = ".vkgrab_cache"
VIDEO_DIR_NAME = "videos"
SKIP_DIRS = {CACHE_DIR_NAME, VIDEO_DIR_NAME}


@dataclass
class ScanResult:
    html_path: Path
    media_urls: list[str]
    video_page_urls: list[str]


@dataclass
class Plan:
    """Aggregated scan: which HTML files reference which media URLs."""

    results: list[ScanResult] = field(default_factory=list)
    url_to_htmls: dict[str, list[Path]] = field(default_factory=dict)
    video_page_urls: set[str] = field(default_factory=set)

    @property
    def unique_media_urls(self) -> list[str]:
        return list(self.url_to_htmls)

    @property
    def total_references(self) -> int:
        return sum(len(v) for v in self.url_to_htmls.values())

    def filtered(self, types: str) -> "Plan":
        """Return a copy keeping only URLs whose type group is selected."""
        keep_groups = set(TYPE_GROUPS) - {"all"} if types == "all" else {types}
        keep = keep_groups
        results = []
        url_to_htmls: dict[str, list[Path]] = {}
        for res in self.results:
            urls = [u for u in res.media_urls if url_type(u) in keep]
            results.append(ScanResult(res.html_path, urls, res.video_page_urls))
        for url, htmls in self.url_to_htmls.items():
            if url_type(url) in keep:
                url_to_htmls[url] = htmls
        return Plan(results, url_to_htmls, set(self.video_page_urls))


def iter_html_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith("_files")
        )
        for fn in sorted(filenames):
            if fn.lower().endswith(".html"):
                yield Path(dirpath) / fn


def scan_html_file(path: Path) -> ScanResult:
    try:
        raw = path.read_bytes()
    except OSError:
        return ScanResult(path, [], [])
    text = raw.decode("utf-8", errors="ignore")
    media: list[str] = []
    seen: set[str] = set()
    for match in URL_RE.finditer(text):
        url = clean_url(match.group(0))
        if url not in seen and valid_media_url(url):
            seen.add(url)
            media.append(url)
    video_pages = sorted({clean_url(m.group(0)) for m in VIDEO_PAGE_RE.finditer(text)})
    return ScanResult(path, media, video_pages)


def scan_html_files(root: Path, limit: int | None = None) -> list[ScanResult]:
    results: list[ScanResult] = []
    for path in iter_html_files(Path(root)):
        results.append(scan_html_file(path))
        if limit and len(results) >= limit:
            break
    return results


def build_plan(root: Path, limit: int | None = None) -> Plan:
    plan = Plan()
    plan.results = scan_html_files(root, limit=limit)
    for res in plan.results:
        for url in res.media_urls:
            plan.url_to_htmls.setdefault(url, []).append(res.html_path)
        plan.video_page_urls.update(res.video_page_urls)
    return plan


def scan_stats(root: Path) -> dict:
    """JSON-friendly link statistics for the ``vkgrab scan`` command."""
    plan = build_plan(Path(root))
    by_type = {"images": 0, "audio": 0, "video": 0}
    for url in plan.url_to_htmls:
        t = url_type(url)
        if t:
            by_type[t] += 1
    return {
        "root": str(Path(root)),
        "html_files": len(plan.results),
        "unique_media_urls": len(plan.url_to_htmls),
        "total_references": plan.total_references,
        "media_by_type": by_type,
        "video_page_links": len(plan.video_page_urls),
    }
