import { useMemo } from "react";
import { ArrowRight, LoaderCircle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatTime } from "@/lib/format";
import { highlight, highlightPattern } from "@/lib/highlight";
import { cn } from "@/lib/utils";
import type { SearchAlgorithm, SearchResult, Segment } from "../types";
import { NativeSelect } from "./NativeSelect";
import { SearchBar } from "./SearchBar";

export function TranscriptPanel({
  activeSegmentStart,
  algorithm,
  isFailed,
  isSearching,
  keywordSearchEnabled,
  onAlgorithmChange,
  onClear,
  onJump,
  onQueryChange,
  onSearch,
  onSelectMoment,
  query,
  results,
  searchable,
  searched,
  segments,
  selectedMomentStart,
  transcript
}: {
  activeSegmentStart: number | null;
  algorithm: SearchAlgorithm;
  isFailed: boolean;
  isSearching: boolean;
  keywordSearchEnabled: boolean;
  onAlgorithmChange: (algorithm: SearchAlgorithm) => void;
  onClear: () => void;
  onJump: (seconds: number) => void;
  onQueryChange: (query: string) => void;
  onSearch: () => void;
  onSelectMoment: (start: number) => void;
  query: string;
  results: SearchResult[];
  searchable: boolean;
  searched: { query: string; algorithm: SearchAlgorithm } | null;
  segments: Segment[];
  selectedMomentStart: number | null;
  transcript: string;
}) {
  const pattern = searched ? highlightPattern(searched.query, searched.algorithm) : "";

  // Search results usually line up with segments. Any that don't still get their own row.
  const rows = useMemo(() => {
    const merged = segments.map((segment) => ({ start: segment.start, text: segment.text, score: undefined as number | undefined }));
    for (const result of results) {
      const match = merged.find((row) => Math.abs(row.start - result.start) < 0.01);
      if (!match) merged.push({ start: result.start, text: result.text, score: result.score });
      else if (match.score === undefined || result.score > match.score) match.score = result.score;
    }
    return merged.sort((a, b) => a.start - b.start);
  }, [results, segments]);

  if (!searchable) {
    return (
      <section className="grid min-w-0 gap-1 lg:sticky lg:top-4">
        <h2 className="text-[15px] font-semibold">Transcript</h2>
        <p className="text-xs text-muted-foreground">
          {isFailed ? "Search and the transcript aren't available because processing failed." : "Search and the transcript will appear after processing."}
        </p>
      </section>
    );
  }

  return (
    <section className="search-panel grid min-w-0 gap-3 lg:sticky lg:top-4" aria-label="Transcript">
      <SearchBar
        inputProps={{
          "aria-label": "Search inside this video",
          placeholder: "Search inside this video",
          value: query,
          onChange: (event) => onQueryChange(event.target.value)
        }}
        onSubmit={(event) => {
          event.preventDefault();
          onSearch();
        }}
      >
        <NativeSelect
          aria-label="Search algorithm"
          value={algorithm}
          onChange={(event) => onAlgorithmChange(event.target.value as SearchAlgorithm)}
        >
          <option value="qdrant_embedding">Semantic</option>
          {keywordSearchEnabled && <option value="elasticsearch_bm25">Keyword</option>}
          {keywordSearchEnabled && <option value="elasticsearch_hybrid">Semantic + keyword</option>}
          <option value="bm25">BM25</option>
          <option value="hybrid">Hybrid (semantic + BM25)</option>
          <option value="exact">Exact</option>
          <option value="overlap">Overlap</option>
          <option value="stopword_overlap">Stopword overlap</option>
        </NativeSelect>
        <Button aria-label="Search" disabled={isSearching} size="icon" type="submit">
          {isSearching ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <ArrowRight aria-hidden="true" />}
        </Button>
      </SearchBar>

      {searched && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-muted-foreground" aria-live="polite">
          <span className="min-w-0 flex-[1_1_160px] [overflow-wrap:anywhere]">
            {results.length
              ? `${results.length} highlighted below`
              : `No matches for “${searched.query}”.`}
          </span>
          <Button size="sm" type="button" variant="ghost" onClick={onClear}>
            <X aria-hidden="true" />
            Clear
          </Button>
        </div>
      )}

      <Tabs defaultValue="segments">
        <TabsList>
          <TabsTrigger value="segments">Segments</TabsTrigger>
          <TabsTrigger value="transcript">Transcript</TabsTrigger>
        </TabsList>
        <TabsContent value="segments">
          <div className="grid max-h-[max(320px,calc(100vh-230px))] gap-0.5 overflow-y-auto">
            {rows.map((row) => {
              const isMatch = row.score !== undefined;
              return (
                <button
                  className={cn(
                    "grid w-full grid-cols-[44px_minmax(0,1fr)] gap-2 rounded-md p-2 text-left hover:bg-accent focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-none",
                    row.start === activeSegmentStart && "bg-selected hover:bg-selected",
                    selectedMomentStart !== null && Math.abs(row.start - selectedMomentStart) < 0.01 && "ring-1 ring-primary ring-inset",
                    searched && !isMatch && "text-muted-foreground"
                  )}
                  key={`${row.start}-${row.text}`}
                  type="button"
                  onClick={() => (isMatch ? onSelectMoment(row.start) : onJump(row.start))}
                >
                  <span className="grid content-start">
                    <strong className="font-medium text-primary tabular-nums">{formatTime(row.start)}</strong>
                    {isMatch && <span className="text-xs text-muted-foreground tabular-nums">{row.score!.toFixed(3)}</span>}
                  </span>
                  <span className="[overflow-wrap:anywhere]">{isMatch ? highlight(row.text, pattern) : row.text}</span>
                </button>
              );
            })}
          </div>
        </TabsContent>
        <TabsContent value="transcript">
          <p className="max-h-[max(320px,calc(100vh-230px))] overflow-y-auto leading-relaxed [overflow-wrap:anywhere]">
            {highlight(transcript, pattern)}
          </p>
        </TabsContent>
      </Tabs>
    </section>
  );
}
