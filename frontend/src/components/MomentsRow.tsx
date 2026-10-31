import { useEffect, useRef } from "react";
import { ChevronLeft, ChevronRight, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatTime, videoGradient } from "@/lib/format";
import { highlight } from "@/lib/highlight";
import { cn } from "@/lib/utils";
import type { SearchResult } from "../types";

function timeRange(moment: SearchResult) {
  return moment.end > moment.start ? `${formatTime(moment.start)} – ${formatTime(moment.end)}` : formatTime(moment.start);
}

export function MomentsRow({
  isFinished,
  moments,
  onKeepWatching,
  onSelect,
  pattern,
  query,
  selectedIndex,
  videoId
}: {
  isFinished: boolean;
  moments: SearchResult[];
  onKeepWatching: () => void;
  onSelect: (index: number) => void;
  pattern: string;
  query: string;
  selectedIndex: number | null;
  videoId: string;
}) {
  const selectedCard = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    selectedCard.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [selectedIndex]);

  return (
    <section className="moments-row grid min-w-0 gap-2.5" aria-label="Matching moments">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="min-w-0 flex-[1_1_180px] font-semibold [overflow-wrap:anywhere]">
          {moments.length} {moments.length === 1 ? "moment" : "moments"} for “{query}”
        </h2>
        <div className="flex items-center gap-2">
          {isFinished && (
            <Button size="sm" type="button" onClick={onKeepWatching}>
              <Play aria-hidden="true" />
              Keep watching
            </Button>
          )}
          <Button
            disabled={selectedIndex === 0}
            size="sm"
            type="button"
            variant="outline"
            onClick={() => onSelect(selectedIndex === null ? 0 : selectedIndex - 1)}
          >
            <ChevronLeft aria-hidden="true" />
            Prev
          </Button>
          <Button
            disabled={selectedIndex === moments.length - 1}
            size="sm"
            type="button"
            variant="outline"
            onClick={() => onSelect(selectedIndex === null ? 0 : selectedIndex + 1)}
          >
            Next
            <ChevronRight aria-hidden="true" />
          </Button>
        </div>
      </div>

      <div className="flex gap-3 overflow-x-auto p-0.5 pb-2">
        {moments.map((moment, index) => (
          <button
            aria-pressed={index === selectedIndex}
            className={cn(
              "flex w-[220px] shrink-0 flex-col justify-start overflow-hidden rounded-[10px] border bg-card text-left shadow-sm hover:border-input focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
              index === selectedIndex && "border-primary ring-1 ring-primary"
            )}
            key={`${moment.start}-${moment.end}-${index}`}
            ref={index === selectedIndex ? selectedCard : undefined}
            type="button"
            onClick={() => onSelect(index)}
          >
            <span className="flex h-12 items-center gap-2 px-3 text-xs font-medium text-white tabular-nums" style={{ background: videoGradient(videoId) }}>
              <Play className="size-3.5 shrink-0" aria-hidden="true" />
              {timeRange(moment)}
            </span>
            <span className="grid gap-1 p-3">
              <span className="line-clamp-3 [overflow-wrap:anywhere]">{highlight(moment.text, pattern)}</span>
              <span className="text-xs text-muted-foreground tabular-nums">Score {moment.score.toFixed(3)}</span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
