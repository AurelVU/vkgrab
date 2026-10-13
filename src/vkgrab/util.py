"""Shared helpers: URL regexes, cleaning/validation, naming, hashing.

This is a port of the proven logic from vk_media_download.py.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
AUDIO_EXTS = {"mp3", "ogg", "m4a", "flac", "wav"}
VIDEO_EXTS = {"mp4", "webm", "avi", "mov", "mkv", "flv"}
MEDIA_EXTS = IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS

TYPE_GROUPS = {
    "images": IMAGE_EXTS,
    "audio": AUDIO_EXTS,
    "video": VIDEO_EXTS,
    "all": MEDIA_EXTS,
}

VIDEO_PAGE_HOSTS = (
    "youtu.be",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "vk.ru",
    "vk.com",
    "m.vk.com",
    "vkvideo.ru",
    "www.vkvideo.ru",
)

URL_RE = re.compile(
    r"https?://[^\s\"'<>()\]]+?\.(" + "|".join(sorted(MEDIA_EXTS)) + r")"
    r"(?:\?[^\s\"'<>()\]]*)?",
    re.IGNORECASE,
)

VIDEO_PAGE_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtu\.be|youtube\.com|vkvideo\.ru)/"
    r"[^\s\"'<>()\]]+|https?://(?:www\.|m\.)?vk\.(?:ru|com)/video"
    r"[^\s\"'<>()\]]*",
    re.IGNORECASE,
)

_NAME_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_TRAILING_JUNK = ';,.)]}\'"'


def clean_url(url: str) -> str:
    """Unescape HTML entities and strip punctuation leaked from HTML/CSS context."""
    return url.replace("&amp;", "&").replace("&AMP;", "&").rstrip(_TRAILING_JUNK)


def valid_media_url(url: str) -> bool:
    """Reject URLs whose host is missing/dotless or ends in a media extension
    (filters user-typed garbage like ``https://www.avi``)."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        return False
    if host.rsplit(".", 1)[-1] in MEDIA_EXTS:
        return False
    return True


def file_ext(url: str) -> str:
    """Extension of the URL path, lowercased, if it is a known media ext."""
    path = urllib.parse.urlparse(url).path
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return ext if ext in MEDIA_EXTS else "bin"


def url_type(url: str) -> str | None:
    """Which --types group a media URL belongs to: images/audio/video."""
    ext = file_ext(url)
    if ext in IMAGE_EXTS:
        return "images"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def url_md5(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def sanitize_name(url: str, max_len: int = 120) -> str:
    """Human-readable file name derived from the URL path."""
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
    base = path.rsplit("/", 1)[-1] if "/" in path else path
    base = _NAME_BAD_CHARS.sub("_", base).strip(" .")
    if len(base) > max_len:
        stem, dot, ext = base.rpartition(".")
        if dot:
            keep = max_len - len(ext) - 1
            base = stem[: max(keep, 1)] + "." + ext
        else:
            base = base[:max_len]
    if not base:
        base = url_md5(url)[:16] + "." + file_ext(url)
    return base


def collision_name(name: str, url: str) -> str:
    """Append .hash8(url) before the extension to disambiguate same-name
    files from different URLs."""
    stem, dot, ext = name.rpartition(".")
    tag = url_md5(url)[:8]
    if dot:
        return f"{stem}.{tag}.{ext}"
    return f"{name}.{tag}"
