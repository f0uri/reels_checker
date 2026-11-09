/**
 * QuickSave fallback downloader for Instagram Reels.
 *
 * Adds quicksave.my as a FALLBACK source, used only when the primary
 * method fails (error, timeout, or no video URL returned).
 *
 * Fallback order:  primary method -> quicksave.my -> error
 *
 * Drop this file into the Lovable app (e.g. src/lib/quicksaveService.ts)
 * and wire `downloadReel` around your existing primary downloader.
 * The primary logic itself is not modified.
 *
 * The quicksave.my video link is a temporary Instagram CDN URL, so the
 * video bytes are downloaded immediately and the URL is never cached.
 */

// ---------------------------------------------------------------------------
// Result type (shared with the primary downloader)
// ---------------------------------------------------------------------------
export interface DownloadResult {
  videoUrl: string;
  videoBytes: ArrayBuffer;
  title: string;
  thumbnail: string;
  duration: number;
  /** "primary" | "quicksave" — which source succeeded. */
  source: "primary" | "quicksave";
  height: number;
  ext: string;
  formatId: string;
}

export class QuickSaveError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "QuickSaveError";
  }
}

export class ReelDownloadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReelDownloadError";
  }
}

// ---------------------------------------------------------------------------
// QuickSaveService — quicksave.my fallback implementation
// ---------------------------------------------------------------------------
interface QuickSaveFormat {
  id?: string;
  label?: string;
  type?: string;
  ext?: string;
  url?: string | null;
  height?: number;
  codec?: string;
}

interface QuickSaveInfo {
  title?: string;
  thumbnail?: string;
  duration?: number;
  uploader?: string;
  platform?: string;
  formats?: QuickSaveFormat[];
}

const API_URL = "https://quicksave.my/api/info";
const ORIGIN = "https://quicksave.my";
const REFERER = "https://quicksave.my/";
const USER_AGENT =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36";
const TIMEOUT_MS = 30_000;

export class QuickSaveService {
  /** Fetch a reel via quicksave.my and return a DownloadResult. */
  async download(reelUrl: string): Promise<DownloadResult> {
    if (!reelUrl || !reelUrl.trim()) {
      throw new QuickSaveError("Empty Instagram URL.");
    }

    const info = await this.fetchInfo(reelUrl.trim());
    const best = this.pickBestVideo(info);

    const videoUrl = (best.url ?? "").trim();
    if (!videoUrl) {
      throw new QuickSaveError("quicksave.my returned no usable video URL.");
    }

    // The CDN URL is temporary: download the bytes right away, never cache.
    const videoBytes = await this.downloadBytes(videoUrl);

    return {
      videoUrl,
      videoBytes,
      title: info.title ?? "",
      thumbnail: info.thumbnail ?? "",
      duration: info.duration ?? 0,
      source: "quicksave",
      height: best.height ?? 0,
      ext: best.ext ?? "mp4",
      formatId: best.id ?? "",
    };
  }

  /** POST the reel URL to quicksave.my and return the parsed JSON. */
  private async fetchInfo(reelUrl: string): Promise<QuickSaveInfo> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

    let response: Response;
    try {
      response = await fetch(API_URL, {
        method: "POST",
        headers: {
          accept: "*/*",
          "content-type": "application/json",
          origin: ORIGIN,
          referer: REFERER,
          "user-agent": USER_AGENT,
        },
        body: JSON.stringify({ url: reelUrl }),
        signal: controller.signal,
      });
    } catch (err) {
      if ((err as Error)?.name === "AbortError") {
        throw new QuickSaveError(
          `quicksave.my request timed out after ${TIMEOUT_MS / 1000}s.`
        );
      }
      throw new QuickSaveError(
        `quicksave.my request failed: ${(err as Error)?.message ?? err}`
      );
    } finally {
      clearTimeout(timer);
    }

    if (!response.ok) {
      throw new QuickSaveError(
        `quicksave.my returned HTTP ${response.status}.`
      );
    }

    let data: unknown;
    try {
      data = await response.json();
    } catch {
      throw new QuickSaveError("quicksave.my returned invalid JSON.");
    }

    if (!data || typeof data !== "object") {
      throw new QuickSaveError("quicksave.my returned an unexpected payload.");
    }
    return data as QuickSaveInfo;
  }

  /**
   * Pick the highest-height video format with a non-empty URL.
   * Audio formats and formats with a null/empty URL are ignored.
   */
  private pickBestVideo(info: QuickSaveInfo): QuickSaveFormat {
    const formats = info.formats;
    if (!Array.isArray(formats) || formats.length === 0) {
      throw new QuickSaveError("quicksave.my response has no formats.");
    }

    const videos = formats.filter(
      (f) =>
        f &&
        f.type === "video" &&
        typeof f.url === "string" &&
        f.url.trim().length > 0
    );
    if (videos.length === 0) {
      throw new QuickSaveError(
        "quicksave.my returned no playable video format."
      );
    }

    return videos.reduce((best, current) =>
      (current.height ?? 0) > (best.height ?? 0) ? current : best
    );
  }

  /** Download the (temporary) CDN video URL immediately. */
  private async downloadBytes(videoUrl: string): Promise<ArrayBuffer> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

    try {
      const response = await fetch(videoUrl, {
        headers: { "user-agent": USER_AGENT },
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new QuickSaveError(
          `Video download returned HTTP ${response.status}.`
        );
      }
      return await response.arrayBuffer();
    } catch (err) {
      if (err instanceof QuickSaveError) throw err;
      if ((err as Error)?.name === "AbortError") {
        throw new QuickSaveError(
          `Video download timed out after ${TIMEOUT_MS / 1000}s.`
        );
      }
      throw new QuickSaveError(
        `Video download failed: ${(err as Error)?.message ?? err}`
      );
    } finally {
      clearTimeout(timer);
    }
  }
}

// ---------------------------------------------------------------------------
// Orchestrator — primary -> quicksave.my -> error
// ---------------------------------------------------------------------------
export type PrimaryDownloadFn = (reelUrl: string) => Promise<DownloadResult>;

/**
 * Download a reel using the primary method first, then the quicksave.my
 * fallback, then throw a clear error. The primary logic is not modified:
 * pass your existing primary downloader in.
 */
export async function downloadReel(
  reelUrl: string,
  primaryDownload: PrimaryDownloadFn,
  quicksave: QuickSaveService = new QuickSaveService()
): Promise<DownloadResult> {
  let primaryError: unknown = null;

  // 1) Primary method (unchanged).
  try {
    const result = await primaryDownload(reelUrl);
    if (result && result.videoUrl) {
      console.info(
        `[reels] source=primary succeeded for ${reelUrl} (height=${result.height})`
      );
      return result;
    }
    console.warn(
      `[reels] Primary returned no video URL for ${reelUrl}; trying fallback.`
    );
  } catch (err) {
    primaryError = err;
    console.warn(
      `[reels] Primary failed for ${reelUrl} (${(err as Error)?.message ?? err}); trying fallback.`
    );
  }

  // 2) Fallback: quicksave.my.
  try {
    const result = await quicksave.download(reelUrl);
    console.info(
      `[reels] source=quicksave succeeded for ${reelUrl} ` +
        `(height=${result.height}, ${result.videoBytes.byteLength} bytes)`
    );
    return result;
  } catch (err) {
    console.error(`[reels] Fallback (quicksave.my) failed for ${reelUrl}:`, err);
    throw new ReelDownloadError(
      `Could not download reel. ` +
        `Primary error: ${(primaryError as Error)?.message ?? "no video URL"}. ` +
        `Fallback error: ${(err as Error)?.message ?? err}`
    );
  }
}
