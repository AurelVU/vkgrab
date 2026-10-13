"""Scanner tests: regex extraction, &amp; unescaping, css url(), junk filter."""

from pathlib import Path

from sample_html import SAMPLE_HTML

from vkgrab.core import build_plan, scan_html_file, scan_stats
from vkgrab.util import clean_url, valid_media_url


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_finds_media_urls(tmp_path: Path) -> None:
    html = _write(tmp_path / "page.html", SAMPLE_HTML)
    res = scan_html_file(html)
    urls = res.media_urls
    assert any(u.endswith("photo_01.jpg") for u in urls)
    # &amp; unescaped
    assert "https://sun9-1.userapi.com/impf/abc/DSC_0002.JPEG?size=2560x1707&quality=95&sign=deadbeef&type=album" in urls
    # css url() with &amp; and trailing junk stripped
    assert "https://pp.userapi.com/c846418/v846418123/1a1b2c/style_bg.JPG?size=1280x720&quality=95" in urls
    assert "https://cs9-11.userapi.net/d1/sound/song.m4a?extra=xyz" in urls
    assert "https://cdn.vk.me/vid/clip.mp4" in urls
    # parens in URL are excluded by the regex, so this only captures up to "pic(1"
    assert not any("pic(1" in u for u in urls)


def test_scan_rejects_junk_urls(tmp_path: Path) -> None:
    html = _write(tmp_path / "page.html", SAMPLE_HTML)
    res = scan_html_file(html)
    urls = res.media_urls
    # "https://www.avi" — host last label is a media ext
    assert not any(u.endswith("www.avi") for u in urls)
    # dotless hosts rejected
    assert not valid_media_url("https://localhost/pic.jpg")
    assert not valid_media_url("https://nodots/pic.jpg")
    assert valid_media_url("https://pp.userapi.com/x/pic.jpg")


def test_scan_collects_video_page_links(tmp_path: Path) -> None:
    html = _write(tmp_path / "page.html", SAMPLE_HTML)
    res = scan_html_file(html)
    vp = res.video_page_urls
    assert any("vk.ru/video123456_7890" in u for u in vp)
    assert any("youtu.be/dQw4w9WgXcQ" in u for u in vp)
    # media files must not leak into video page links
    assert not any(u.endswith(".jpg") for u in vp)


def test_scan_dedups_within_file(tmp_path: Path) -> None:
    html = _write(
        tmp_path / "page.html",
        '<img src="https://a.example.com/1.jpg"><img src="https://a.example.com/1.jpg">',
    )
    res = scan_html_file(html)
    assert res.media_urls == ["https://a.example.com/1.jpg"]


def test_build_plan_aggregates(tmp_path: Path) -> None:
    _write(tmp_path / "a.html", '<img src="https://a.example.com/shared.jpg"><img src="https://a.example.com/only_a.png">')
    sub = tmp_path / "sub dir"
    sub.mkdir()
    _write(sub / "b.html", '<img src="https://a.example.com/shared.jpg">')
    plan = build_plan(tmp_path)
    assert len(plan.results) == 2
    assert len(plan.unique_media_urls) == 2
    assert plan.total_references == 3
    assert len(plan.url_to_htmls["https://a.example.com/shared.jpg"]) == 2


def test_scan_skips_cache_and_files_dirs(tmp_path: Path) -> None:
    cache = tmp_path / ".vkgrab_cache"
    cache.mkdir()
    _write(cache / "junk.html", '<img src="https://a.example.com/x.jpg">')
    files_dir = tmp_path / "page_files"
    files_dir.mkdir()
    _write(files_dir / "copy.html", '<img src="https://a.example.com/y.jpg">')
    _write(tmp_path / "real.html", '<img src="https://a.example.com/z.jpg">')
    stats = scan_stats(tmp_path)
    assert stats["html_files"] == 1
    assert stats["unique_media_urls"] == 1


def test_scan_stats_counts_types(tmp_path: Path) -> None:
    _write(
        tmp_path / "page.html",
        '<img src="https://a.example.com/p.jpg"><a href="https://a.example.com/s.mp3">'
        '</a><video src="https://a.example.com/v.mp4"></video>'
        '<a href="https://vk.ru/video1_2">v</a>',
    )
    stats = scan_stats(tmp_path)
    assert stats["media_by_type"] == {"images": 1, "audio": 1, "video": 1}
    assert stats["video_page_links"] == 1


def test_clean_url_strips_trailing_punctuation() -> None:
    assert clean_url("https://a.com/x.jpg&amp;y=1;") == "https://a.com/x.jpg&y=1"
    assert clean_url("https://a.com/x.jpg).") == "https://a.com/x.jpg"
    assert clean_url("https://a.com/x.JPG')") == "https://a.com/x.JPG"


def test_plan_filtered_by_type(tmp_path: Path) -> None:
    from vkgrab.core import build_plan

    _write(
        tmp_path / "page.html",
        '<img src="https://a.example.com/p.jpg"><a href="https://a.example.com/s.mp3">'
        '</a><video src="https://a.example.com/v.mp4"></video>',
    )
    plan = build_plan(tmp_path)
    assert len(plan.unique_media_urls) == 3
    assert len(plan.filtered("all").unique_media_urls) == 3
    assert len(plan.filtered("images").unique_media_urls) == 1
    assert plan.filtered("images").unique_media_urls == ["https://a.example.com/p.jpg"]
    assert len(plan.filtered("audio").unique_media_urls) == 1
    assert len(plan.filtered("video").unique_media_urls) == 1
