import type {
  LibrarySearchResponse,
  SearchAlgorithm,
  SearchResult,
  Segment,
  UserProfile,
  VideoMetrics,
  VideoStatus
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
let authTokenProvider: () => string | null = () => null;
export class ApiError extends Error {
  constructor(public status: number, public payload: unknown, message: string) {
    super(message);
  }
}
const uploadKeys = new WeakMap<File, Map<string, string>>();

export function setAuthTokenProvider(provider: () => string | null) {
  authTokenProvider = provider;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  const token = authTokenProvider();

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers
  });
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : "Request failed";
    throw new ApiError(response.status, payload, message);
  }

  return payload as T;
}

export function getMe() {
  return request<UserProfile>("/me");
}

export async function searchLibrary(query: string, signal: AbortSignal, mode: "semantic" | "lexical" = "semantic", analyticsOptIn = false, shareForReview = false): Promise<{ status: number; data: LibrarySearchResponse }> {
  try {
    const data = await request<LibrarySearchResponse>("/search", {
      method: "POST", signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, scope: "my_library", mode, limit: 10, analytics_opt_in: analyticsOptIn, share_for_review: shareForReview })
    });
    return { status: 200, data };
  } catch (error) {
    if (error instanceof ApiError && (error.status === 409 || error.status === 503)) {
      const data = error.payload as LibrarySearchResponse | null;
      if (data && Array.isArray(data.results) && Array.isArray(data.notices) && data.coverage) {
        return { status: error.status, data };
      }
    }
    throw error;
  }
}

export function sendSearchInteraction(searchId: string, body: { event_id: string; event_type: string; rank?: number; ranks?: number[]; feedback?: string }) {
  return request(`/search/${encodeURIComponent(searchId)}/interactions`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  });
}

export function withdrawSearchSharing(searchId: string) {
  return request(`/search/${encodeURIComponent(searchId)}/sharing`, { method: "DELETE" });
}

export function withdrawAllSearchSharing() { return request("/search-sharing", { method: "DELETE" }); }

export type SearchAnalytics = {
  retention_days: number; pending_events: number; pending_bytes: number; expired_events: number;
  quarantined: number; latest_update: string | null;
  review_next_cursor?: string | null;
  modes: Record<string, { searches: number; errors: number; zero_results: number; degraded: number;
    viewed: number; opened: number; helpful: number; not_helpful: number; p50_ms: number; p95_ms: number }>;
  review: { search_id: string; query: string; mode: string; reason: string }[];
};

export function getSearchAnalytics(cursor?: string) { return request<SearchAnalytics>("/search-analytics" + (cursor ? `?cursor=${encodeURIComponent(cursor)}` : "")); }

type UploadAccepted = {
  video_id: string;
  title: string;
  visibility: "private" | "public";
  filename: string;
  status: string;
};

type CreateUploadResponse = {
  video_id: string;
  idempotency_key: string;
  upload: { method: "POST"; url: string; fields: Record<string, string> } | null;
  completed: UploadAccepted | null;
};

// "direct" sends the file from the browser straight to S3 (requires bucket CORS);
// "api" streams it through the API, which only suits local development.
const UPLOAD_MODE = import.meta.env.VITE_UPLOAD_MODE === "direct" ? "direct" : "api";

async function uploadDirect(file: File, title: string, visibility: "private" | "public", key: string) {
  const created = await request<CreateUploadResponse>("/videos/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": key },
    body: JSON.stringify({
      filename: file.name,
      title,
      visibility,
      size_bytes: file.size,
      content_type: file.type || "video/mp4"
    })
  });
  if (created.completed) return created.completed;
  if (!created.upload) throw new Error("Upload could not be started");

  const form = new FormData();
  for (const [name, value] of Object.entries(created.upload.fields)) form.append(name, value);
  form.append("file", file); // S3 requires the file to be the last form field.
  const stored = await fetch(created.upload.url, { method: "POST", body: form });
  if (!stored.ok) throw new ApiError(stored.status, null, "The file could not be uploaded to storage");

  return request<UploadAccepted>(`/videos/uploads/${encodeURIComponent(created.video_id)}/complete`, {
    method: "POST",
    headers: { "Idempotency-Key": key }
  });
}

export async function uploadVideo(file: File, title: string, visibility: "private" | "public") {
  let keys = uploadKeys.get(file);
  if (!keys) {
    keys = new Map();
    uploadKeys.set(file, keys);
  }
  const metadata = JSON.stringify([title, visibility]);
  const key = keys.get(metadata) ?? crypto.randomUUID();
  keys.set(metadata, key);
  if (UPLOAD_MODE === "direct") return uploadDirect(file, title, visibility, key);
  const formData = new FormData();
  formData.append("file", file);
  formData.append("title", title);
  formData.append("visibility", visibility);

  return request<UploadAccepted>("/videos/upload", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: formData
  });
}

export function getVideoStatus(videoId: string) {
  return request<VideoStatus>(`/videos/${videoId}/status`);
}

export function deleteVideo(videoId: string) {
  return request<{ video_id: string; status: "deleted" }>(`/videos/${videoId}`, {
    method: "DELETE"
  });
}

export function listVideos() {
  return request<{ videos: VideoStatus[] }>("/videos");
}

export function listPublicVideos() {
  return request<{ videos: VideoStatus[] }>("/public/videos");
}

export function getVideoMetrics(videoId: string) {
  return request<VideoMetrics>(`/videos/${videoId}/metrics`);
}

export function getTranscript(videoId: string) {
  return request<{ video_id: string; transcript: string }>(`/videos/${videoId}/transcripts`);
}

export function getSegments(videoId: string) {
  return request<{ video_id: string; segments: Segment[] }>(`/videos/${videoId}/segments`);
}

export function searchVideo(videoId: string, query: string, algorithm: SearchAlgorithm) {
  return request<{ video_id: string; query: string; algorithm: string; results: SearchResult[] }>(
    `/videos/${videoId}/search`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, algorithm })
    }
  );
}

export function getVideoFileUrl(videoId: string) {
  return request<{ video_id: string; url: string }>(`/videos/${videoId}/file-url`);
}

export function getVideoThumbnailUrl(videoId: string) {
  return request<{ video_id: string; url: string }>(`/videos/${videoId}/thumbnail-url`);
}
