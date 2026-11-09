# QuickSave Fallback (quicksave.my)

Adds **quicksave.my** as a **fallback** source for downloading Instagram
Reels. It is used **only** when the primary method fails (error, timeout,
or no video URL returned).

**Fallback order:** primary method → quicksave.my → error

The primary logic is **not** changed — the fallback is wired around it.

## Files

| File | What it is |
|---|---|
| `quicksave_service.py` | Complete Python implementation (`QuickSaveService` + `ReelDownloader` orchestrator). Stdlib only, no dependencies. |
| `quicksaveService.ts` | Complete TypeScript implementation for the Lovable app (`QuickSaveService` + `downloadReel`). |
| `test_quicksave_service.py` | Unit tests (no network needed). Run: `python3 -m unittest test_quicksave_service -v` |

## How it works

1. `POST https://quicksave.my/api/info` with body `{"url": "<reel url>"}` (30s timeout, no cookies).
2. From `formats`, keep only `type == "video"` with a non-empty `url` (audio formats with `null` URL are ignored).
3. Pick the format with the highest `height` — its `url` is the video link.
4. Download the video URL immediately with the same `User-Agent` header. The link is a temporary Instagram CDN URL, so it is **never cached**.
5. If no video format is found, or the request fails (timeout, non-200, invalid JSON, missing fields), a clear error is returned — the app never crashes.
6. The source that succeeded is logged (`source=primary` / `source=quicksave`) for debugging.

## Wiring it into the app

### Python

```python
from quicksave_service import ReelDownloader

# Pass your EXISTING primary downloader — it is not modified.
downloader = ReelDownloader(primary_download=my_existing_primary)

result = downloader.download("https://www.instagram.com/reel/XXXX/")
# result.video_bytes / result.video_url / result.source ...
```

### TypeScript (Lovable)

```ts
import { downloadReel } from "./quicksaveService";

// Pass your EXISTING primary downloader — it is not modified.
const result = await downloadReel(reelUrl, myExistingPrimary);
```

## Runtime config

`../server.json` declares the fallback so the app can read it at runtime:

```json
"fallback": {
  "name": "quicksave",
  "enabled": true,
  "apiUrl": "https://quicksave.my/api/info",
  "origin": "https://quicksave.my",
  "referer": "https://quicksave.my/",
  "userAgent": "Mozilla/5.0 ...",
  "timeoutSeconds": 30,
  "pickHighestVideo": true
}
```

The primary `serverUrl` is untouched. Old app versions ignore the extra
`fallback` key (backward compatible).

## OTA update

`../version.json` was bumped to **4.4.4 (versionCode 47)**. To send the
OTA update to users:

1. Build the new APK with the fallback wired in (see above).
2. Publish it as GitHub release **v4.4.4** (`reel-checker.apk`).
3. Merge this branch into `main` — the app reads `version.json` from
   `main` and prompts users to update.
