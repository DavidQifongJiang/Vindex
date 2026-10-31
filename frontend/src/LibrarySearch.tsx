import { useEffect, useRef, useState } from "react";
import { LoaderCircle, TriangleAlert, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/NativeSelect";
import { SearchBar } from "@/components/SearchBar";
import { ApiError, searchLibrary, sendSearchInteraction, withdrawSearchSharing, withdrawAllSearchSharing, getSearchAnalytics } from "./api";
import type { SearchAnalytics } from "./api";
import type { LibrarySearchHit, LibrarySearchResponse } from "./types";

function timestamp(seconds: number) {
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function LibrarySearch({ onActiveChange, onOpen }: { onActiveChange: (active: boolean) => void; onOpen: (hit: LibrarySearchHit) => void }) {
  const [query, setQuery] = useState("");
  const [searchedQuery, setSearchedQuery] = useState("");
  const [response, setResponse] = useState<LibrarySearchResponse | null>(null);
  const [status, setStatus] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"semantic" | "lexical">("semantic");
  const [analyticsOptIn, setAnalyticsOptIn] = useState(false);
  const [shareForReview, setShareForReview] = useState(false);
  const [feedbackStatus, setFeedbackStatus] = useState("");
  const feedbackRetry = useRef<{ searchId: string; event_id: string; event_type: string; feedback: string } | null>(null);
  const [analytics, setAnalytics] = useState<SearchAnalytics | null>(null);
  const [analyticsError, setAnalyticsError] = useState("");
  const viewedSearches = useRef(new Set<string>());
  const displayedSearch = useRef<string | null>(null);
  const active = useRef<AbortController | null>(null);
  const hasOutcome = busy || Boolean(error) || Boolean(response);
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    onActiveChange(hasOutcome);
    return () => onActiveChange(false);
  }, [hasOutcome, onActiveChange]);
  useEffect(() => {
    if (!response?.interaction_enabled || !analyticsOptIn || viewedSearches.current.has(response.request_id)) return;
    viewedSearches.current.add(response.request_id);
    void sendSearchInteraction(response.request_id, { event_id: crypto.randomUUID(),
      event_type: "search.results_viewed", ranks: response.results.map((_, index) => index + 1) }).catch(() => {});
  }, [response, analyticsOptIn]);

  function clear() {
    active.current?.abort();
    active.current = null;
    setBusy(false);
    setQuery("");
    setSearchedQuery("");
    setError("");
    setResponse(null);
    setStatus(0);
    displayedSearch.current = null;
    setFeedbackStatus("");
    feedbackRetry.current = null;
  }

  async function submit() {
    const text = query.trim();
    if (!text) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    setResponse(null);
    displayedSearch.current = null;
    setFeedbackStatus("");
    feedbackRetry.current = null;
    setSearchedQuery(text);
    try {
      const result = await searchLibrary(text, controller.signal, mode, analyticsOptIn, shareForReview);
      if (active.current !== controller || controller.signal.aborted) return;
      setResponse(result.data);
      displayedSearch.current = result.data.request_id;
      setStatus(result.status);
    } catch (failure) {
      if (active.current !== controller || controller.signal.aborted) return;
      const code = failure instanceof ApiError ? failure.status : 0;
      setError(code === 429 ? "Search is busy. Please try again shortly."
        : code === 401 || code === 403 ? "Sign in again to search your videos."
        : code === 404 ? "Library search is not enabled yet. You can still search inside a video."
        : code === 422 ? "Library search currently supports up to 100 videos. You can still search inside a video."
        : "Search is temporarily unavailable. Please try again.");
    } finally {
      if (active.current === controller && !controller.signal.aborted) setBusy(false);
    }
  }

  async function feedback(value: "helpful" | "not_helpful") {
    if (!response?.interaction_enabled || !analyticsOptIn) return;
    const searchId = response.request_id;
    const previous = feedbackRetry.current;
    const pending = previous?.searchId === searchId && previous.feedback === value ? previous :
      { searchId, event_id: crypto.randomUUID(), event_type: "search.feedback_submitted", feedback: value };
    feedbackRetry.current = pending;
    const { searchId: identity, ...body } = pending;
    try {
      await sendSearchInteraction(identity, body);
      if (feedbackRetry.current === pending) { setFeedbackStatus("Feedback saved."); feedbackRetry.current = null; }
    } catch {
      if (feedbackRetry.current === pending) setFeedbackStatus("Feedback was not saved. Select the same option to retry.");
    }
  }

  const notices: string[] = [];
  if (response?.degraded && status === 200) notices.push("Search is temporarily limited. These matches are available, but some may be missing.");
  if (response?.notices.includes("library_indexing_incomplete")) notices.push("Some videos are still being indexed or need attention. Results include only the currently searchable passages.");
  if (response?.notices.includes("video_processing")) notices.push("Some videos are still processing. Try your search again when they are ready.");
  if (response?.notices.includes("indexing_failed")) notices.push("Search preparation failed for some videos. Those videos may need attention before all their passages can be searched.");
  if (response?.notices.includes("search_scope_changed")) notices.push("Your library changed during this search. Search again for updated matches.");

  return <section className="library-search grid min-w-0 gap-3" aria-label="Search my videos">
    <SearchBar
      inputProps={{ id: "library-query", "aria-label": "Search my videos", maxLength: 500, placeholder: "Search your videos by topic or phrase", value: query,
        onChange: (event) => setQuery(event.target.value) }}
      onSubmit={(event) => { event.preventDefault(); void submit(); }}
    >
      <NativeSelect aria-label="Search mode" value={mode} onChange={event => setMode(event.target.value as "semantic" | "lexical")}>
        <option value="semantic">Semantic</option><option value="lexical">Keyword</option>
      </NativeSelect>
      <Button disabled={busy || !query.trim()} type="submit">
        {busy && <LoaderCircle className="animate-spin" aria-hidden="true" />}
        {busy ? "Searching…" : "Search"}
      </Button>
    </SearchBar>
    {import.meta.env.VITE_SEARCH_ANALYTICS_ENABLED === "true" && <div className="grid justify-items-start gap-1 text-xs text-muted-foreground">
      <label className="flex items-start gap-2"><input className="mt-0.5" type="checkbox" checked={analyticsOptIn} onChange={event => { setAnalyticsOptIn(event.target.checked); if (!event.target.checked) setShareForReview(false); }} /> Help measure search performance and interactions for 7 days</label>
      <label className="flex items-start gap-2"><input className="mt-0.5" type="checkbox" disabled={!analyticsOptIn} checked={shareForReview} onChange={event => setShareForReview(event.target.checked)} /> Also share this query with authorized reviewers for 7 days</label>
      <Button size="sm" type="button" variant="link" className="h-auto p-0" onClick={() => { setShareForReview(false); void withdrawAllSearchSharing()
        .then(() => { setFeedbackStatus("All earlier query sharing withdrawn."); setAnalytics(null); })
        .catch(() => setFeedbackStatus("Could not withdraw sharing. Please retry.")); }}>Withdraw all earlier shared queries</Button>
      {!response && feedbackStatus && <p role="status">{feedbackStatus}</p>}
    </div>}
    {hasOutcome && <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="grid min-w-0 flex-[1_1_160px] gap-2 [overflow-wrap:anywhere]" aria-live="polite" aria-atomic="true">
        {busy && <p className="text-muted-foreground">Searching your videos…</p>}
        {error && <p role="alert">{error}</p>}
        {response && <>
          {status === 503 ? <p role="alert">Search is temporarily unavailable. Please try again.</p>
            : status === 409 ? <p>No passages are searchable yet. Try again after search preparation completes.</p>
            : response.results.length === 0 ? <p>{response.coverage.total_videos === 0 ? "Your library is empty. Upload a video to start searching."
              : response.degraded || !response.coverage.complete ? "No matches in the currently available results. Try again when search is fully available."
              : `No matches for “${searchedQuery}”. Try another phrase or topic.`}</p>
            : <p className="text-muted-foreground">{response.results.length} {response.results.length === 1 ? "match" : "matches"} for “{searchedQuery}”</p>}
          {notices.map((notice) => <p className="flex gap-2.5 rounded-lg bg-warn px-3 py-2.5 text-warn-foreground" key={notice}><TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{notice}</p>)}
        </>}
      </div>
      <Button size="sm" type="button" variant="ghost" onClick={clear}><X aria-hidden="true" />Clear</Button>
    </div>}
    {status === 200 && response && <div className="grid">
      {response.results.map((hit, index) => <button type="button" className="grid w-full grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 border-b px-2 py-3.5 text-left last:border-b-0 hover:bg-accent focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-none" key={hit.passage_id} onClick={() => {
        if (response.interaction_enabled && analyticsOptIn) void sendSearchInteraction(response.request_id, {
          event_id: crypto.randomUUID(), event_type: "search.result_opened", rank: index + 1 }).catch(() => {});
        onOpen(hit);
      }}>
        <strong className="font-medium">{hit.title?.trim() || "Untitled video"}</strong>
        <span className="text-[13px] font-medium whitespace-nowrap text-primary tabular-nums">Open at {timestamp(hit.start_seconds)}</span>
        <span className="col-span-full whitespace-pre-wrap text-muted-foreground [overflow-wrap:anywhere]">{hit.text}</span>
      </button>)}
    </div>}
    {response && import.meta.env.VITE_SEARCH_ANALYTICS_ENABLED === "true" && <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>Were these results useful?</span>
      <Button size="sm" type="button" variant="outline" disabled={!analyticsOptIn || !response.interaction_enabled} onClick={() => void feedback("helpful")}>Helpful</Button>
      <Button size="sm" type="button" variant="outline" disabled={!analyticsOptIn || !response.interaction_enabled} onClick={() => void feedback("not_helpful")}>Not helpful</Button>
      <Button size="sm" type="button" variant="ghost" onClick={() => { const id = response.request_id; void withdrawSearchSharing(id)
        .then(() => { if (displayedSearch.current === id) setFeedbackStatus("Query sharing withdrawn."); setAnalytics(null); })
        .catch(() => { if (displayedSearch.current === id) setFeedbackStatus("Could not withdraw sharing. Please retry."); }); }}>Withdraw query sharing</Button>
      <p role="status">{feedbackStatus}</p>
    </div>}
    {import.meta.env.VITE_SEARCH_ANALYTICS_ENABLED === "true" && <details className="grid gap-2 text-xs text-muted-foreground">
      <summary className="font-medium">Developer search analytics</summary>
      <div className="mt-2 grid justify-items-start gap-2">
      <Button size="sm" type="button" variant="outline" onClick={() => { setAnalyticsError(""); void getSearchAnalytics().then(setAnalytics)
        .catch(() => { setAnalytics(null); setAnalyticsError("Analytics unavailable or access not granted."); }); }}>Refresh analytics</Button>
      {analyticsError && <p role="alert">{analyticsError}</p>}
      {analytics && <>
        <p>Last {analytics.retention_days} days · Updated {analytics.latest_update ?? "not yet"}. Opt-in observations only; engagement does not establish relevance.</p>
        <p>Pending events: {analytics.pending_events} · Expired: {analytics.expired_events} · Quarantined: {analytics.quarantined}</p>
        <div className="max-w-full overflow-x-auto"><table className="text-left [&_td]:py-1 [&_td]:pr-4 [&_th]:py-1 [&_th]:pr-4 [&_th]:font-medium"><thead><tr><th>Traffic / mode / configuration</th><th>Searches</th><th>p50 ms</th><th>p95 ms</th><th>Errors</th><th>No results</th></tr></thead>
          <tbody>{Object.entries(analytics.modes).map(([key, value]) => <tr key={key}><td>{key}</td><td>{value.searches}</td><td>{value.p50_ms.toFixed(1)}</td><td>{value.p95_ms.toFixed(1)}</td><td>{value.errors}</td><td>{value.zero_results}</td></tr>)}</tbody></table></div>
        <h4 className="font-semibold">Shared examples needing review</h4>
        {analytics.review.length === 0 ? <p>No shared examples available.</p> : <ul>{analytics.review.map(item => <li key={item.search_id}>{item.query} — {item.mode}, {item.reason.replaceAll("_", " ")}</li>)}</ul>}
        {analytics.review_next_cursor && <Button size="sm" type="button" variant="outline" onClick={() => void getSearchAnalytics(analytics.review_next_cursor!).then(setAnalytics).catch(() => setAnalyticsError("Could not load the next page."))}>Next review page</Button>}
      </>}
      </div>
    </details>}
  </section>;
}
