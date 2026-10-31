import { useMemo, useRef } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { formatBytes, formatDuration, formatInteger, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { VideoMetrics } from "../types";
import { StatusBadge } from "./StatusBadge";

export function DetailsSheet({
  errorMessage,
  isRefreshing,
  metrics,
  onOpenChange,
  onRefresh,
  open,
  pipeline
}: {
  errorMessage?: string | null;
  isRefreshing: boolean;
  metrics: VideoMetrics | null;
  onOpenChange: (open: boolean) => void;
  onRefresh: () => void;
  open: boolean;
  pipeline: { label: string; value?: string | null }[];
}) {
  const returnFocus = useRef<HTMLElement | null>(null);
  const metricGroups = useMemo(
    () => [
      {
        name: "Upload",
        rows: [
          { label: "Upload response", value: formatDuration(metrics?.upload?.upload_response_latency_seconds) },
          { label: "Raw upload", value: formatDuration(metrics?.upload?.s3_raw_upload_seconds) },
          { label: "Uploaded size", value: formatBytes(metrics?.upload?.uploaded_bytes) },
          { label: "Celery enqueue", value: formatDuration(metrics?.upload?.celery_enqueue_seconds) }
        ]
      },
      {
        name: "Processing",
        rows: [
          { label: "Queue wait", value: formatDuration(metrics?.processing?.queue_wait_seconds) },
          { label: "Time searchable", value: formatDuration(metrics?.processing?.time_to_searchable_seconds) },
          { label: "Total processing", value: formatDuration(metrics?.processing?.total_processing_seconds) },
          { label: "FFmpeg preset", value: metrics?.processing?.ffmpeg_preset ?? "-" },
          { label: "Raw download", value: formatDuration(metrics?.processing?.s3_raw_download_seconds) },
          { label: "Video transcode", value: formatDuration(metrics?.processing?.video_transcode_seconds) },
          { label: "Audio extract", value: formatDuration(metrics?.processing?.audio_extraction_seconds) },
          { label: "Whisper model", value: formatDuration(metrics?.processing?.whisper_model_load_or_get_seconds) },
          { label: "Whisper", value: formatDuration(metrics?.processing?.whisper_transcription_seconds) },
          { label: "Embedding model", value: formatDuration(metrics?.processing?.embedding_model_load_or_get_seconds) },
          { label: "Embeddings", value: formatDuration(metrics?.processing?.embedding_generation_seconds) },
          { label: "S3 artifacts", value: formatDuration(metrics?.processing?.s3_artifact_write_seconds) },
          { label: "Thumbnail", value: formatDuration(metrics?.processing?.thumbnail_generation_seconds) },
          { label: "Qdrant upsert", value: formatDuration(metrics?.processing?.qdrant_upsert_seconds) },
          { label: "Segments", value: formatInteger(metrics?.processing?.segment_count) }
        ]
      },
      {
        name: "Search",
        rows: [
          { label: "Search latency", value: formatDuration(metrics?.search?.search_latency_seconds) },
          { label: "Query embed", value: formatDuration(metrics?.search?.query_embedding_seconds) },
          { label: "Qdrant search", value: formatDuration(metrics?.search?.qdrant_search_seconds) },
          { label: "Results", value: formatInteger(metrics?.search?.result_count) },
          { label: "Top score", value: formatNumber(metrics?.search?.top_score) }
        ]
      }
    ],
    [metrics]
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className="w-full overflow-y-auto bg-card sm:max-w-[420px]"
        onOpenAutoFocus={() => { returnFocus.current = document.activeElement as HTMLElement | null; }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (returnFocus.current?.isConnected) returnFocus.current.focus();
        }}
      >
        <SheetHeader className="text-left">
          <SheetTitle>Details</SheetTitle>
          <SheetDescription className="sr-only">Processing pipeline and performance metrics for this video.</SheetDescription>
        </SheetHeader>

        <Button className="mt-3" disabled={isRefreshing} size="sm" variant="outline" onClick={onRefresh}>
          <RefreshCw className={cn(isRefreshing && "animate-spin")} aria-hidden="true" />
          Refresh
        </Button>

        <section className="mt-5">
          <h3 className="font-semibold">Pipeline</h3>
          <div className="mt-2.5 grid gap-1.5">
            {pipeline.map((item) => (
              <div className="flex min-h-7 items-center justify-between gap-3" key={item.label}>
                <span>{item.label}</span>
                <StatusBadge value={item.value} />
              </div>
            ))}
          </div>
          {errorMessage && <p className="mt-3 rounded-lg bg-bad px-3 py-2.5 text-bad-foreground">{errorMessage}</p>}
        </section>

        <section className="mt-5 pb-2">
          <h3 className="font-semibold">Metrics</h3>
          {metricGroups.map((group) => (
            <div key={group.name}>
              <p className="mt-3 text-xs font-semibold text-muted-foreground">{group.name}</p>
              {group.rows.map((row) => (
                <div className="flex items-center justify-between gap-3 border-b py-1.5 last:border-b-0" key={row.label}>
                  <span className="text-muted-foreground">{row.label}</span>
                  <strong className="text-right font-medium tabular-nums [overflow-wrap:anywhere]">{row.value}</strong>
                </div>
              ))}
            </div>
          ))}
        </section>
      </SheetContent>
    </Sheet>
  );
}
