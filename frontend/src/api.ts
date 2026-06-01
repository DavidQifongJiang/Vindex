import type { SearchAlgorithm, SearchResult, Segment, VideoStatus } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : "Request failed";
    throw new Error(message);
  }

  return payload as T;
}

export async function uploadVideo(file: File) {
  const formData = new FormData();
  formData.append("file", file);

  return request<{ video_id: string; filename: string; status: string }>("/videos/upload", {
    method: "POST",
    body: formData
  });
}

export function getVideoStatus(videoId: string) {
  return request<VideoStatus>(`/videos/${videoId}/status`);
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

export function videoFileUrl(videoId: string) {
  return `${API_BASE_URL}/videos/${videoId}/file`;
}
