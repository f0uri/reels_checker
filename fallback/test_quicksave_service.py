"""Unit tests for quicksave_service (no network access needed).

Run:  python3 -m unittest test_quicksave_service -v
"""

import io
import json
import unittest
import urllib.error

from quicksave_service import (
    DownloadResult,
    QuickSaveError,
    QuickSaveService,
    ReelDownloadError,
    ReelDownloader,
)

REEL_URL = "https://www.instagram.com/reel/C1234567890/"


def make_response(payload, status=200):
    """Build a fake HTTP response object like urllib returns."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    resp = io.BytesIO(body)
    resp.status = status
    return resp


class FakeOpener:
    """Fake urllib opener that serves canned responses."""

    def __init__(self, info_payload=None, video_bytes=b"VIDEO", info_status=200):
        self.info_payload = info_payload
        self.video_bytes = video_bytes
        self.info_status = info_status
        self.calls = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.calls.append(url)
        if "quicksave.my/api/info" in url:
            if self.info_status != 200:
                raise urllib.error.HTTPError(url, self.info_status, "err", None, None)
            return make_response(self.info_payload)
        return make_response(self.video_bytes)


GOOD_INFO = {
    "title": "Test reel",
    "thumbnail": "https://example.com/thumb.jpg",
    "duration": 12,
    "uploader": "instagram",
    "platform": "instagram",
    "formats": [
        {"id": "low", "label": "360p", "type": "video", "ext": "mp4",
         "url": "https://cdn.example.com/low.mp4", "height": 360, "codec": "h264"},
        {"id": "rapid_best", "label": "720p", "type": "video", "ext": "mp4",
         "url": "https://cdn.example.com/high.mp4", "height": 720, "codec": "h264"},
        {"id": "rapid_audio", "label": "MP3", "type": "audio", "ext": "mp3",
         "url": None},
    ],
    "_rapidApi": True,
}


class TestQuickSaveService(unittest.TestCase):
    def test_picks_highest_video_and_downloads(self):
        opener = FakeOpener(GOOD_INFO, b"VIDEOBYTES")
        svc = QuickSaveService(opener=opener)
        result = svc.download(REEL_URL)
        self.assertEqual(result.video_url, "https://cdn.example.com/high.mp4")
        self.assertEqual(result.video_bytes, b"VIDEOBYTES")
        self.assertEqual(result.height, 720)
        self.assertEqual(result.source, "quicksave")
        self.assertEqual(result.title, "Test reel")
        self.assertEqual(result.duration, 12)

    def test_ignores_audio_and_null_urls(self):
        info = dict(GOOD_INFO)
        info["formats"] = [
            {"id": "a", "type": "audio", "ext": "mp3", "url": None},
            {"id": "b", "type": "video", "ext": "mp4", "url": "", "height": 1080},
            {"id": "c", "type": "video", "ext": "mp4",
             "url": "https://cdn.example.com/ok.mp4", "height": 480},
        ]
        svc = QuickSaveService(opener=FakeOpener(info))
        result = svc.download(REEL_URL)
        self.assertEqual(result.video_url, "https://cdn.example.com/ok.mp4")
        self.assertEqual(result.height, 480)

    def test_no_video_format_raises(self):
        info = dict(GOOD_INFO)
        info["formats"] = [{"id": "a", "type": "audio", "ext": "mp3", "url": None}]
        svc = QuickSaveService(opener=FakeOpener(info))
        with self.assertRaises(QuickSaveError):
            svc.download(REEL_URL)

    def test_missing_formats_raises(self):
        svc = QuickSaveService(opener=FakeOpener({"title": "x"}))
        with self.assertRaises(QuickSaveError):
            svc.download(REEL_URL)

    def test_non_200_raises(self):
        svc = QuickSaveService(opener=FakeOpener(GOOD_INFO, info_status=500))
        with self.assertRaises(QuickSaveError):
            svc.download(REEL_URL)

    def test_invalid_json_raises(self):
        svc = QuickSaveService(opener=FakeOpener(b"not-json{{{"))
        with self.assertRaises(QuickSaveError):
            svc.download(REEL_URL)

    def test_empty_url_raises(self):
        svc = QuickSaveService(opener=FakeOpener(GOOD_INFO))
        with self.assertRaises(QuickSaveError):
            svc.download("   ")

    def test_network_error_raises(self):
        class Boom:
            def open(self, request, timeout=None):
                raise urllib.error.URLError("connection refused")

        svc = QuickSaveService(opener=Boom())
        with self.assertRaises(QuickSaveError):
            svc.download(REEL_URL)


class TestReelDownloader(unittest.TestCase):
    def test_primary_success_skips_fallback(self):
        primary_result = DownloadResult(
            video_url="https://primary.example.com/v.mp4", source="primary"
        )
        downloader = ReelDownloader(
            primary_download=lambda url: primary_result,
            quicksave=QuickSaveService(opener=FakeOpener(GOOD_INFO)),
        )
        result = downloader.download(REEL_URL)
        self.assertEqual(result.source, "primary")
        self.assertEqual(result.video_url, "https://primary.example.com/v.mp4")

    def test_primary_error_uses_fallback(self):
        def boom(url):
            raise RuntimeError("primary down")

        downloader = ReelDownloader(
            primary_download=boom,
            quicksave=QuickSaveService(opener=FakeOpener(GOOD_INFO)),
        )
        result = downloader.download(REEL_URL)
        self.assertEqual(result.source, "quicksave")
        self.assertEqual(result.video_url, "https://cdn.example.com/high.mp4")

    def test_primary_no_url_uses_fallback(self):
        downloader = ReelDownloader(
            primary_download=lambda url: DownloadResult(video_url=""),
            quicksave=QuickSaveService(opener=FakeOpener(GOOD_INFO)),
        )
        result = downloader.download(REEL_URL)
        self.assertEqual(result.source, "quicksave")

    def test_both_fail_raises_clear_error(self):
        def boom(url):
            raise RuntimeError("primary down")

        downloader = ReelDownloader(
            primary_download=boom,
            quicksave=QuickSaveService(opener=FakeOpener({"title": "x"})),
        )
        with self.assertRaises(ReelDownloadError) as ctx:
            downloader.download(REEL_URL)
        self.assertIn("Primary error", str(ctx.exception))
        self.assertIn("Fallback error", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
