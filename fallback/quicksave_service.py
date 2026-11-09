"""
QuickSave fallback downloader for Instagram Reels.

Adds quicksave.my as a FALLBACK source, used only when the primary
method fails (error, timeout, or no video URL returned).

Fallback order:  primary method -> quicksave.my -> error

Design notes
------------
* ``QuickSaveService`` is a standalone class that returns the same
  ``DownloadResult`` type as the primary downloader, so it can be
  dropped in without touching the primary logic.
* The quicksave.my video link is a temporary Instagram CDN URL, so the
  video bytes are downloaded immediately and the URL is never cached.
* Only the Python standard library is used (no third-party deps).

Usage
-----
    from quicksave_service import ReelDownloader

    downloader = ReelDownloader(primary_download=my_existing_primary)
    result = downloader.download("https://www.instagram.com/reel/XXXX/")
    # result.video_bytes / result.video_url / result.source ...
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("reels.fallback")


# ---------------------------------------------------------------------------
# Result type (shared with the primary downloader)
# ---------------------------------------------------------------------------
@dataclass
class DownloadResult:
    """Result returned by both the primary downloader and QuickSaveService."""

    video_url: str
    video_bytes: bytes = b""
    title: str = ""
    thumbnail: str = ""
    duration: int = 0
    source: str = "quicksave"  # "primary" or "quicksave"
    height: int = 0
    ext: str = "mp4"
    format_id: str = ""


class QuickSaveError(Exception):
    """Raised when the quicksave.my fallback cannot produce a video."""


class ReelDownloadError(Exception):
    """Raised when every source (primary + fallback) has failed."""


# ---------------------------------------------------------------------------
# QuickSaveService — quicksave.my fallback implementation
# ---------------------------------------------------------------------------
class QuickSaveService:
    """Fetch an Instagram Reel video via the quicksave.my API.

    POST https://quicksave.my/api/info
    Body: {"url": "<instagram reel url>"}
    No cookies needed.
    """

    API_URL = "https://quicksave.my/api/info"
    ORIGIN = "https://quicksave.my"
    REFERER = "https://quicksave.my/"
    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
    )
    TIMEOUT_SECONDS = 30

    def __init__(self, opener: Optional[urllib.request.OpenerDirector] = None):
        # Injectable opener keeps the service testable without network access.
        self._opener = opener or urllib.request.build_opener()

    # -- public API ---------------------------------------------------------
    def download(self, reel_url: str) -> DownloadResult:
        """Return a DownloadResult for ``reel_url`` via quicksave.my.

        Raises QuickSaveError on any failure (timeout, non-200, invalid
        JSON, missing fields, no video format, download failure).
        """
        if not reel_url or not reel_url.strip():
            raise QuickSaveError("Empty Instagram URL.")

        info = self._fetch_info(reel_url.strip())
        best = self._pick_best_video(info)

        video_url = (best.get("url") or "").strip()
        if not video_url:
            raise QuickSaveError("quicksave.my returned no usable video URL.")

        # The CDN URL is temporary: download the bytes right away, never cache.
        video_bytes = self._download_bytes(video_url)

        return DownloadResult(
            video_url=video_url,
            video_bytes=video_bytes,
            title=str(info.get("title") or ""),
            thumbnail=str(info.get("thumbnail") or ""),
            duration=int(info.get("duration") or 0),
            source="quicksave",
            height=int(best.get("height") or 0),
            ext=str(best.get("ext") or "mp4"),
            format_id=str(best.get("id") or ""),
        )

    # -- internals ----------------------------------------------------------
    def _fetch_info(self, reel_url: str) -> Dict[str, Any]:
        """POST the reel URL to quicksave.my and return the parsed JSON."""
        payload = json.dumps({"url": reel_url}).encode("utf-8")
        request = urllib.request.Request(
            self.API_URL,
            data=payload,
            method="POST",
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Origin": self.ORIGIN,
                "Referer": self.REFERER,
                "User-Agent": self.USER_AGENT,
            },
        )
        try:
            with self._opener.open(
                request, timeout=self.TIMEOUT_SECONDS
            ) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise QuickSaveError(
                        f"quicksave.my returned HTTP {status}."
                    )
                raw = response.read()
        except QuickSaveError:
            raise
        except urllib.error.HTTPError as exc:
            raise QuickSaveError(
                f"quicksave.my returned HTTP {exc.code}."
            ) from exc
        except urllib.error.URLError as exc:
            raise QuickSaveError(
                f"quicksave.my request failed: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise QuickSaveError(
                f"quicksave.my request timed out after "
                f"{self.TIMEOUT_SECONDS}s."
            ) from exc
        except Exception as exc:  # never crash the app on unexpected errors
            raise QuickSaveError(f"quicksave.my request failed: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise QuickSaveError(
                "quicksave.my returned invalid JSON."
            ) from exc

        if not isinstance(data, dict):
            raise QuickSaveError("quicksave.my returned an unexpected payload.")
        return data

    @staticmethod
    def _pick_best_video(info: Dict[str, Any]) -> Dict[str, Any]:
        """Pick the highest-height video format with a non-empty URL.

        Audio formats (type == "audio") and formats with a null/empty URL
        are ignored.
        """
        formats = info.get("formats")
        if not isinstance(formats, list) or not formats:
            raise QuickSaveError("quicksave.my response has no formats.")

        videos = [
            f
            for f in formats
            if isinstance(f, dict)
            and f.get("type") == "video"
            and (f.get("url") or "").strip()
        ]
        if not videos:
            raise QuickSaveError(
                "quicksave.my returned no playable video format."
            )

        def height_of(fmt: Dict[str, Any]) -> int:
            try:
                return int(fmt.get("height") or 0)
            except (TypeError, ValueError):
                return 0

        return max(videos, key=height_of)

    def _download_bytes(self, video_url: str) -> bytes:
        """Download the (temporary) CDN video URL immediately."""
        request = urllib.request.Request(
            video_url,
            headers={"User-Agent": self.USER_AGENT},
        )
        try:
            with self._opener.open(
                request, timeout=self.TIMEOUT_SECONDS
            ) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise QuickSaveError(
                        f"Video download returned HTTP {status}."
                    )
                return response.read()
        except QuickSaveError:
            raise
        except urllib.error.HTTPError as exc:
            raise QuickSaveError(
                f"Video download returned HTTP {exc.code}."
            ) from exc
        except urllib.error.URLError as exc:
            raise QuickSaveError(
                f"Video download failed: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise QuickSaveError(
                f"Video download timed out after {self.TIMEOUT_SECONDS}s."
            ) from exc
        except Exception as exc:  # never crash the app on unexpected errors
            raise QuickSaveError(f"Video download failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Orchestrator — primary -> quicksave.my -> error
# ---------------------------------------------------------------------------
PrimaryDownloadFn = Callable[[str], DownloadResult]


class ReelDownloader:
    """Try the primary method first, then the quicksave.my fallback.

    The primary logic is NOT modified: pass your existing primary
    downloader callable in, and this class only adds the fallback
    wiring around it.
    """

    def __init__(
        self,
        primary_download: PrimaryDownloadFn,
        quicksave: Optional[QuickSaveService] = None,
    ):
        self._primary = primary_download
        self._quicksave = quicksave or QuickSaveService()

    def download(self, reel_url: str) -> DownloadResult:
        """Download ``reel_url`` using primary, then fallback, then error."""
        primary_error: Optional[Exception] = None

        # 1) Primary method (unchanged).
        try:
            result = self._primary(reel_url)
            if result is not None and getattr(result, "video_url", ""):
                logger.info(
                    "source=primary succeeded for %s (height=%s)",
                    reel_url,
                    getattr(result, "height", "?"),
                )
                return result
            logger.warning(
                "Primary returned no video URL for %s; trying fallback.",
                reel_url,
            )
        except Exception as exc:  # primary failed -> fall back
            primary_error = exc
            logger.warning(
                "Primary failed for %s (%s); trying fallback.",
                reel_url,
                exc,
            )

        # 2) Fallback: quicksave.my.
        try:
            result = self._quicksave.download(reel_url)
            logger.info(
                "source=quicksave succeeded for %s (height=%s, %d bytes)",
                reel_url,
                result.height,
                len(result.video_bytes),
            )
            return result
        except Exception as exc:
            logger.error("Fallback (quicksave.my) failed for %s: %s", reel_url, exc)
            raise ReelDownloadError(
                f"Could not download reel. "
                f"Primary error: {primary_error or 'no video URL'}. "
                f"Fallback error: {exc}"
            ) from exc
