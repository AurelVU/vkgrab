"""Command-line interface: ``vkgrab scan|download|tui``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .core import CACHE_DIR_NAME, build_plan, scan_stats
from .downloader import CacheDownloader, DownloadResult
from .placer import PlaceCounters, plan_placements, place_files
from .util import TYPE_GROUPS
from .video import DEFAULT_QUALITY, download_video_pages, find_ytdlp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vkgrab",
        description=(
            "Download media referenced by exported VK (VKontakte) archive "
            "HTML files into a content-addressed cache, then place the files "
            "next to each HTML (wget-style) or into a single directory. "
            "Fully resumable."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser(
        "scan", help="scan a directory of exported HTML files and print link stats as JSON"
    )
    p_scan.add_argument("dir", type=Path, help="directory with exported .html files")

    p_dl = sub.add_parser("download", help="scan and download media (headless)")
    p_dl.add_argument("--src", type=Path, default=Path("."), help="archive root directory")
    p_dl.add_argument(
        "--dest-mode",
        choices=["next-to-html", "single-dir"],
        default="next-to-html",
        help="where to place downloaded files",
    )
    p_dl.add_argument(
        "--dest-dir",
        type=Path,
        default=None,
        help="target directory for --dest-mode single-dir (default: <src>/downloaded_media)",
    )
    p_dl.add_argument(
        "--types",
        choices=sorted(TYPE_GROUPS),
        default="all",
        help="which media types to download",
    )
    p_dl.add_argument("--workers", type=int, default=8, help="parallel download workers (default 8)")
    p_dl.add_argument("--max-retries", type=int, default=4, help="retries per URL (default 4)")
    p_dl.add_argument(
        "--fetch-video-pages",
        action="store_true",
        help="also download video page links via yt-dlp (requires yt-dlp)",
    )
    p_dl.add_argument(
        "--video-quality",
        default=DEFAULT_QUALITY,
        help=f"yt-dlp format selector (default: {DEFAULT_QUALITY!r})",
    )

    sub.add_parser("tui", help="interactive terminal UI")
    return parser


def cmd_scan(args: argparse.Namespace) -> int:
    root = args.dir
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    print(json.dumps(scan_stats(root), indent=2, ensure_ascii=False))
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    src: Path = args.src
    if not src.is_dir():
        print(f"error: not a directory: {src}", file=sys.stderr)
        return 2
    t0 = time.time()
    cache_root = src / CACHE_DIR_NAME
    dest_dir = args.dest_dir or (src / "downloaded_media")

    print(f"[1/4] scanning {src} ...", flush=True)
    plan = build_plan(src)
    plan = plan.filtered(args.types)
    print(
        f"    {len(plan.results)} html files, {len(plan.unique_media_urls)} unique "
        f"media urls ({plan.total_references} references), "
        f"{len(plan.video_page_urls)} video page links",
        flush=True,
    )

    links_path = src / "video_page_links.txt"
    if plan.video_page_urls:
        links_path.write_text(
            "\n".join(sorted(plan.video_page_urls)) + "\n", encoding="utf-8"
        )
        print(f"    video page links -> {links_path}", flush=True)

    downloader = CacheDownloader(
        cache_root, workers=args.workers, max_retries=args.max_retries
    )
    cached = downloader.load_manifest()
    to_download = [u for u in plan.unique_media_urls if u not in cached]
    print(
        f"[2/4] downloading: {len(cached)} already cached, {len(to_download)} to fetch "
        f"({downloader.workers} workers)",
        flush=True,
    )

    last_log = 0.0

    def on_done(res: DownloadResult) -> None:
        nonlocal last_log
        now = time.time()
        with downloader._counters_lock:
            d, e, fl = downloader.counters.done, downloader.counters.exists, downloader.counters.failed
        total = len(plan.unique_media_urls)
        if res.status == "failed":
            print(f"    FAIL {res.url}: {res.error}", flush=True)
        if now - last_log >= 5:
            last_log = now
            print(
                f"    progress {d + e + fl}/{total} "
                f"(done {d}, cached {e}, failed {fl})",
                flush=True,
            )

    results = downloader.download_all(plan.unique_media_urls, on_done=on_done)
    done_map: dict[str, Path] = {}
    for url, res in results.items():
        if res.cache_path:
            done_map[url] = res.cache_path
    for url, cpath in cached.items():
        done_map.setdefault(url, Path(cpath))

    print("[3/4] placing files ...", flush=True)
    placements, missing = plan_placements(plan, done_map, args.dest_mode, dest_dir)
    counters: PlaceCounters = place_files(placements, workers=args.workers)
    print(
        f"    copied {counters.copied}, skipped existing, failed {counters.failed}, "
        f"missing cache {missing}",
        flush=True,
    )

    video_stats = {"done": 0, "failed": 0, "skipped": "yt-dlp not requested"}
    if args.fetch_video_pages and plan.video_page_urls:
        if not find_ytdlp():
            print("    yt-dlp not found on PATH — skipping video pages", flush=True)
        else:
            print(
                f"[4/4] fetching {len(plan.video_page_urls)} video pages via yt-dlp ...",
                flush=True,
            )
            vids = download_video_pages(
                sorted(plan.video_page_urls),
                src / "videos",
                quality=args.video_quality,
            )
            video_stats = {
                "done": sum(1 for v in vids if v.status == "done"),
                "failed": sum(1 for v in vids if v.status == "failed"),
            }
    else:
        print("[4/4] video pages: not requested (see --fetch-video-pages)", flush=True)

    with downloader._counters_lock:
        dc = downloader.counters
    summary = {
        "html_files": len(plan.results),
        "unique_media_urls": len(plan.unique_media_urls),
        "downloaded": dc.done,
        "already_cached": dc.exists,
        "download_failed": dc.failed,
        "bytes_downloaded": dc.bytes,
        "copied_new": counters.copied,
        "copy_failed": counters.failed,
        "video_pages": video_stats,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    print("summary: " + json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if (dc.failed == 0 and counters.failed == 0) else 1


def cmd_tui(args: argparse.Namespace) -> int:
    from .tui import VkGrabApp

    VkGrabApp().run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "download":
        return cmd_download(args)
    if args.command == "tui":
        return cmd_tui(args)
    parser.error(f"unknown command {args.command}")
    return 2
