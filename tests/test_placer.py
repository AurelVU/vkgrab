"""Placement tests: next-to-html, single-dir, collisions, idempotence."""

from pathlib import Path

from vkgrab.core import Plan, ScanResult
from vkgrab.placer import files_dir_for, plan_placements, place_files
from vkgrab.util import url_md5


def _plan(tmp_path: Path) -> Plan:
    html1 = tmp_path / "page one.html"
    html2 = tmp_path / "sub" / "page2.html"
    html2.parent.mkdir(exist_ok=True)
    html1.write_text("", encoding="utf-8")
    html2.write_text("", encoding="utf-8")
    return Plan(
        results=[
            ScanResult(
                html1,
                ["https://a.com/shared.jpg", "https://b.com/pic.jpg"],
                [],
            ),
            ScanResult(
                html2,
                ["https://a.com/shared.jpg", "https://c.com/pic.jpg"],
                [],
            ),
        ],
        url_to_htmls={
            "https://a.com/shared.jpg": [html1, html2],
            "https://b.com/pic.jpg": [html1],
            "https://c.com/pic.jpg": [html2],
        },
    )


def _fake_cache(tmp_path: Path, urls: list[str]) -> dict[str, Path]:
    cache = tmp_path / ".vkgrab_cache"
    cache.mkdir(exist_ok=True)
    done = {}
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        p = cache / name
        p.write_bytes(b"data:" + name.encode())
        done[url] = p
    return done


def test_files_dir_for() -> None:
    assert files_dir_for(Path("/x/My Page.html")) == Path("/x/My Page_files")


def test_plan_next_to_html(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    done = _fake_cache(tmp_path, list(plan.url_to_htmls))
    placements, missing = plan_placements(plan, done, "next-to-html")
    assert missing == 0
    dsts = [p.dst for p in placements]
    assert (tmp_path / "page one_files" / "shared.jpg") in dsts
    assert (tmp_path / "sub" / "page2_files" / "shared.jpg") in dsts
    # collision: b.com/pic.jpg and c.com/pic.jpg both named pic.jpg but in
    # different folders here, so no hash suffix needed
    assert (tmp_path / "page one_files" / "pic.jpg") in dsts
    assert (tmp_path / "sub" / "page2_files" / "pic.jpg") in dsts


def test_collision_same_folder_gets_hash(tmp_path: Path) -> None:
    html = tmp_path / "p.html"
    html.write_text("", encoding="utf-8")
    plan = Plan(
        results=[ScanResult(html, ["https://a.com/pic.jpg", "https://b.com/pic.jpg"], [])],
        url_to_htmls={
            "https://a.com/pic.jpg": [html],
            "https://b.com/pic.jpg": [html],
        },
    )
    done = _fake_cache(tmp_path, ["https://a.com/pic.jpg", "https://b.com/pic.jpg"])
    # make cache files distinguishable so copy works
    placements, missing = plan_placements(plan, done, "single-dir", tmp_path / "out")
    assert missing == 0
    names = sorted(p.dst.name for p in placements)
    assert len(names) == 2
    assert len({p.dst.name for p in placements}) == 2
    assert all(p.dst.name.startswith("pic") for p in placements)


def test_plan_single_dir(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    done = _fake_cache(tmp_path, list(plan.url_to_htmls))
    placements, _ = plan_placements(plan, done, "single-dir", tmp_path / "out")
    folders = {p.dst.parent for p in placements}
    assert folders == {tmp_path / "out"}
    # shared.jpg is referenced by two htmls but lands once; the two different
    # pic.jpg URLs collide and one gets a hash suffix
    names = sorted(p.dst.name for p in placements)
    assert names == sorted(
        ["pic.jpg", "shared.jpg", "pic." + url_md5("https://c.com/pic.jpg")[:8] + ".jpg"]
    )


def test_plan_missing_cache_counted(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    done = _fake_cache(tmp_path, ["https://a.com/shared.jpg"])
    placements, missing = plan_placements(plan, done, "next-to-html")
    assert missing == 2
    assert len(placements) == 2


def test_place_files_copies_and_is_idempotent(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    done = _fake_cache(tmp_path, list(plan.url_to_htmls))
    placements, _ = plan_placements(plan, done, "next-to-html")
    counters = place_files(placements, workers=2)
    assert counters.copied == len(placements)
    assert counters.failed == 0
    target = tmp_path / "page one_files" / "shared.jpg"
    assert target.read_bytes().startswith(b"data:")
    # second run: nothing to copy
    placements2, _ = plan_placements(plan, done, "next-to-html")
    assert placements2 == []
    counters2 = place_files(placements2, workers=2)
    assert counters2.copied == 0
