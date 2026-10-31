import { useEffect, useState } from "react";
import { LoaderCircle, Play, Trash2 } from "lucide-react";
import { formatDate, displayVideoTitle, videoGradient } from "@/lib/format";
import { cn } from "@/lib/utils";
import { getVideoThumbnailUrl } from "../api";
import type { VideoStatus } from "../types";
import { StatusBadge } from "./StatusBadge";
import { VisibilityIcon } from "./VisibilityIcon";

export function VideoCard({
  canDelete,
  index,
  isDeleting,
  isSelected,
  onDelete,
  onOpen,
  video
}: {
  canDelete: boolean;
  index: number;
  isDeleting: boolean;
  isSelected: boolean;
  onDelete: () => void;
  onOpen: () => void;
  video: VideoStatus;
}) {
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const [thumbnailUrl, setThumbnailUrl] = useState("");
  const canShowThumbnail = Boolean(thumbnailUrl) && !thumbnailFailed;

  useEffect(() => {
    let active = true;
    setThumbnailFailed(false);
    setThumbnailUrl("");

    if (video.status !== "processed" && !video.processed_path) return;

    void getVideoThumbnailUrl(video.video_id)
      .then((response) => {
        if (active) setThumbnailUrl(response.url);
      })
      .catch(() => {
        if (active) setThumbnailFailed(true);
      });

    return () => {
      active = false;
    };
  }, [video.status, video.video_id, video.processed_path]);

  return (
    <article
      className={cn(
        "group relative min-w-0 overflow-hidden rounded-[10px] border bg-card shadow-sm hover:border-input",
        isSelected && "border-primary/50"
      )}
    >
      <button className="block w-full text-left focus-visible:outline-none" type="button" onClick={onOpen}>
        <div
          className="relative grid aspect-video place-items-center overflow-hidden text-white group-has-[button:first-child:focus-visible]:ring-2 group-has-[button:first-child:focus-visible]:ring-ring group-has-[button:first-child:focus-visible]:ring-inset"
          style={{ background: videoGradient(video.video_id) }}
        >
          {canShowThumbnail && (
            <img
              alt=""
              className="absolute inset-0 size-full object-cover"
              src={thumbnailUrl}
              onError={() => setThumbnailFailed(true)}
            />
          )}
          <Play
            className="relative size-[30px] opacity-0 transition-opacity group-hover:opacity-85 group-focus-within:opacity-85"
            aria-hidden="true"
          />
          {video.status !== "processed" && (
            <span className="absolute top-2 left-2">
              <StatusBadge value={video.status} />
            </span>
          )}
        </div>
        <div className="grid gap-1.5 p-3">
          <h3 className="text-sm leading-snug font-semibold [overflow-wrap:anywhere]">{displayVideoTitle(video, index)}</h3>
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span>{formatDate(video.created_at)}</span>
            <VisibilityIcon visibility={video.visibility} />
          </div>
        </div>
      </button>
      {canDelete && (
        <button
          aria-label={`Delete ${displayVideoTitle(video, index)}`}
          className="absolute top-1.5 right-1.5 grid size-[30px] place-items-center rounded-md bg-black/55 text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 hover:bg-[#9d2f1f] focus-visible:ring-2 focus-visible:ring-white focus-visible:outline-none disabled:opacity-100 [@media(hover:none)]:opacity-100"
          disabled={isDeleting}
          title="Delete video"
          type="button"
          onClick={onDelete}
        >
          {isDeleting ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Trash2 className="size-4" aria-hidden="true" />}
        </button>
      )}
    </article>
  );
}
