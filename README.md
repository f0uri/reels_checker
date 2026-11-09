# Reels Checker — OTA / Server Config

This repo holds the remote configuration the Reel Checker app reads at
runtime (OTA updates + server switching). The app source code lives
elsewhere (Lovable); this repo only carries the config files.

## Files

| File | Purpose |
|---|---|
| `server.json` | Primary backend server + fallback server config. |
| `version.json` | Latest app version (OTA update prompt + APK download URL). |
| `control.json` | Remote kill-switch / blocked-devices control. |
| `fallback/` | quicksave.my fallback implementation (see `fallback/README.md`). |

## Servers

- **Primary:** `https://profile-peek-down.lovable.app` (unchanged).
- **Fallback:** quicksave.my (`https://quicksave.my/api/info`) — used only
  when the primary fails (error, timeout, or no video URL). Declared in
  `server.json` under `fallback`; implementation in `fallback/`.

Fallback order: **primary → quicksave.my → error**.

## Sending an OTA update

1. Bump `version.json` (`versionName`, `versionCode`, `apkUrl`, `notes`).
2. Publish the matching APK as a GitHub release (e.g. `v4.4.4`).
3. Merge to `main` — the app fetches `version.json` from `main` and
   prompts users to update.
