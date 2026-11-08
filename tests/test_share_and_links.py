import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easyremix import share  # noqa: E402
from easyremix.downloader import normalize_url  # noqa: E402


@pytest.mark.parametrize("pasted,expected", [
    ("https://www.tiktok.com/@user/video/7300000000000000000",
     "https://www.tiktok.com/@user/video/7300000000000000000"),
    ("tiktok.com/@user/video/123", "https://tiktok.com/@user/video/123"),
    ("vm.tiktok.com/ZMabc123/", "https://vm.tiktok.com/ZMabc123/"),
    ("Check out this video! https://vm.tiktok.com/ZMabc123/ #fyp", "https://vm.tiktok.com/ZMabc123/"),
    ("youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"),
    ("  https://www.youtube.com/watch?v=abc  ", "https://www.youtube.com/watch?v=abc"),
    ("www.instagram.com/reel/xyz", "https://www.instagram.com/reel/xyz"),
])
def test_links_are_recognised(pasted, expected):
    assert normalize_url(pasted) == expected


@pytest.mark.parametrize("pasted", ["", "hello world", "my song", "@user"])
def test_non_links_are_rejected(pasted):
    assert normalize_url(pasted) is None


def test_old_temporary_shares_are_deleted(tmp_path):
    folder = share.share_dir(str(tmp_path))
    old, new = os.path.join(folder, "old.mp4"), os.path.join(folder, "new.mp4")
    for p in (old, new):
        open(p, "wb").write(b"x")
    now = time.time()
    os.utime(old, (now - 7 * 3600, now - 7 * 3600))
    assert share.clean_share_dir(str(tmp_path), max_age_hours=6, now=now) == 1
    assert not os.path.exists(old) and os.path.exists(new)


def test_share_folder_is_temporary_not_user_files(tmp_path):
    import tempfile

    assert share.share_dir().startswith(tempfile.gettempdir())
    assert "Music" not in share.share_dir() and "Download" not in share.share_dir()


def test_every_app_has_a_web_fallback():
    for name, (_app, web) in share.SHARE_APPS.items():
        assert web.startswith("https://"), name


def test_file_uri():
    assert share.file_uri("/tmp/a b.mp4") == "file:///tmp/a%20b.mp4"
