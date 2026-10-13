"""Unit tests for util helpers: sharding, naming, collisions, filtering."""

from pathlib import Path

from vkgrab.downloader import CacheDownloader
from vkgrab.util import (
    collision_name,
    file_ext,
    sanitize_name,
    url_md5,
    url_type,
    valid_media_url,
)


def test_cache_path_sharding(tmp_path: Path) -> None:
    dl = CacheDownloader(tmp_path / ".vkgrab_cache")
    url = "https://pp.userapi.com/c846418/v846418123/1a1b2d/photo_01.jpg"
    cp = dl.cache_path_for(url)
    h = url_md5(url)
    assert cp == tmp_path / ".vkgrab_cache" / h[:2] / h[2:4] / (h + ".jpg")
    # different urls -> different shard dirs for well-distributed md5 (sanity: stable)
    cp2 = dl.cache_path_for(url + "?x=1")
    assert cp2.name.startswith(url_md5(url + "?x=1"))


def test_file_ext() -> None:
    assert file_ext("https://a.com/x.JPG?size=1") == "jpg"
    assert file_ext("https://a.com/x.mp4") == "mp4"
    assert file_ext("https://a.com/noext") == "bin"
    assert file_ext("https://a.com/x.php?to=b.mp3") == "bin"


def test_url_type() -> None:
    assert url_type("https://a.com/x.webp") == "images"
    assert url_type("https://a.com/x.flac") == "audio"
    assert url_type("https://a.com/x.mkv") == "video"
    assert url_type("https://a.com/x.php") is None


def test_sanitize_name() -> None:
    assert sanitize_name("https://a.com/photos/my%20pic.jpg") == "my pic.jpg"
    assert sanitize_name("https://a.com/bad:name|.jpg") == "bad_name_.jpg"
    assert sanitize_name("https://a.com/") == url_md5("https://a.com/")[:16] + ".bin"
    long_name = "a" * 200 + ".jpg"
    out = sanitize_name("https://a.com/" + long_name)
    assert len(out) <= 120
    assert out.endswith(".jpg")
    # unicode survives
    assert sanitize_name("https://a.com/фото_ждёт_приказа.jpg") == "фото_ждёт_приказа.jpg"


def test_collision_name() -> None:
    url = "https://a.com/pic.jpg"
    name = collision_name("pic.jpg", url)
    assert name == f"pic.{url_md5(url)[:8]}.jpg"
    assert collision_name("noext", url) == f"noext.{url_md5(url)[:8]}"


def test_valid_media_url_edge_cases() -> None:
    assert valid_media_url("https://sub.userapi.com/a.jpg")
    assert not valid_media_url("https://www.avi")
    assert not valid_media_url("https://mp3")
    assert not valid_media_url("notaurl")
    # scheme is not checked here — URL_RE only ever produces http(s) matches;
    # kept identical to the reference implementation on purpose
    assert valid_media_url("ftp://a.com/x.jpg")
