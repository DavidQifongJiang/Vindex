import type {
  SearchAlgorithm,
  SearchResult,
  Segment,
  UserProfile,
  VideoMetrics,
  VideoStatus
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
let authTokenProvider: () => string | null = () => null;

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
    throw new Error(message);
  }

  return payload as T;
}

export function getMe() {
  return request<UserProfile>("/me");
}

export async function uploadVideo(file: File, title: string, visibility: "private" | "public") {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("title", title);
  formData.append("visibility", visibility);

  return request<{
    video_id: string;
    title: string;
    visibility: "private" | "public";
    filename: string;
    status: string;
  }>("/videos/upload", {
    method: "POST",
    body: formData
  });
}

export function getVideoStatus(videoId: string) {
  return request<VideoStatus>(`/videos/${videoId}/status`);
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
