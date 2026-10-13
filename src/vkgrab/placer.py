"""Placement: put cached files next to their HTML (wget-style) or into a
single target directory. Deterministic names, collision-safe, idempotent.
"""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .core import Plan
from .util import collision_name, sanitize_name


@dataclass
class Placement:
    src: Path
    dst: Path
    url: str


@dataclass
class PlaceCounters:
    copied: int = 0
    skipped: int = 0
    failed: int = 0


def files_dir_for(html_path: Path) -> Path:
    """``page.html`` -> ``page_files/``."""
    return html_path.with_name(html_path.stem + "_files")


def plan_placements(
    plan: Plan,
    done_map: dict[str, Path],
    dest_mode: str = "next-to-html",
    dest_dir: Path | None = None,
) -> tuple[list[Placement], int]:
    """Build the copy task list.

    Returns (placements, missing) where *missing* counts references whose
    cache file is absent (e.g. failed downloads).
    """
    placements: list[Placement] = []
    missing = 0
    taken_by_dir: dict[Path, dict[str, str]] = {}
    planned: set[Path] = set()

    def target_dir(html_path: Path) -> Path:
        if dest_mode == "single-dir":
            assert dest_dir is not None
            return Path(dest_dir)
        return files_dir_for(html_path)

    for res in plan.results:
        if not res.media_urls:
            continue
        for url in res.media_urls:
            cache_path = done_map.get(url)
            if not cache_path or not Path(cache_path).exists():
                missing += 1
                continue
            folder = target_dir(res.html_path)
            taken = taken_by_dir.setdefault(folder, {})
            name = sanitize_name(url)
            if name in taken and taken[name] != url:
                name = collision_name(name, url)
            taken[name] = url
            dst = folder / name
            if dst.exists() or dst in planned:
                continue
            planned.add(dst)
            placements.append(Placement(Path(cache_path), dst, url))
    return placements, missing


def place_files(
    placements: list[Placement],
    workers: int = 8,
    on_done: Callable[[Placement, str], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> PlaceCounters:
    counters = PlaceCounters()

    def do_one(p: Placement) -> tuple[Placement, str]:
        try:
            p.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p.src, p.dst)
            return p, "copied"
        except OSError:
            return p, "failed"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(do_one, p) for p in placements]
        for fut in futures:
            if stop and stop():
                break
            p, status = fut.result()
            if status == "copied":
                counters.copied += 1
            else:
                counters.failed += 1
            if on_done:
                on_done(p, status)
    return counters
