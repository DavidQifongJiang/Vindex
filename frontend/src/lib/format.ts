import type { UserProfile, VideoStatus } from "../types";

export function formatTime(seconds: number) {
  const totalSeconds = Math.floor(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}

export function formatDuration(seconds?: number | null) {
  if (seconds === undefined || seconds === null) return "--";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(seconds >= 10 ? 1 : 2)} s`;
}

export function formatNumber(value?: number | null, digits = 3) {
  if (value === undefined || value === null) return "--";
  return value.toFixed(digits);
}

export function formatInteger(value?: number | null) {
  if (value === undefined || value === null) return "--";
  return new Intl.NumberFormat().format(value);
}

export function formatBytes(value?: number | null) {
  if (value === undefined || value === null) return "--";
  const units = ["B", "KB", "MB", "GB"];
  let nextValue = value;
  let unitIndex = 0;

  while (nextValue >= 1024 && unitIndex < units.length - 1) {
    nextValue /= 1024;
    unitIndex += 1;
  }

  return `${nextValue.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

export function formatDate(value?: string) {
  if (!value) return "--";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

export function statusTone(value?: string | null) {
  if (!value || value === "not_started") return "idle";
  if (["completed", "extracted", "processed"].includes(value)) return "good";
  if (["processing", "uploaded"].includes(value)) return "active";
  if (value === "failed") return "bad";
  return "idle";
}

export function readableStatus(value?: string | null) {
  if (!value) return "waiting";
  return value.replaceAll("_", " ");
}

export function filenameToTitle(filename?: string | null) {
  return (filename || "Untitled video").replace(/\.[^/.]+$/, "").replace(/[-_]+/g, " ");
}

export function displayVideoTitle(video: VideoStatus, index: number) {
  const title = video.title?.trim();
  if (title) return title;

  const withoutExtension = (video.filename || "").replace(/\.[^/.]+$/, "");

  if (!withoutExtension || withoutExtension === video.video_id) {
    return `Video ${index + 1}`;
  }

  if (withoutExtension.includes(video.video_id)) {
    return `Video ${index + 1}`;
  }

  return withoutExtension.replace(/[-_]+/g, " ");
}

export function initials(profile?: UserProfile | null) {
  const source = profile?.name || profile?.email || "V";
  return source
    .split(/\s|@/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "V";
}

// Gives each video a stable placeholder colour until its thumbnail loads.
export function videoHue(videoId: string) {
  let hash = 0;
  for (const character of videoId) {
    hash = (hash * 31 + character.charCodeAt(0)) % 360;
  }
  return hash;
}

export function videoGradient(videoId: string) {
  const hue = videoHue(videoId);
  return `linear-gradient(135deg, hsl(${hue} 34% 20%), hsl(${hue + 30} 30% 34%))`;
}
