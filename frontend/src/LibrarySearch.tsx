import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Search } from "lucide-react";
import { ApiError, searchLibrary, sendSearchInteraction, withdrawSearchSharing, withdrawAllSearchSharing, getSearchAnalytics } from "./api";
import type { SearchAnalytics } from "./api";
import type { LibrarySearchHit, LibrarySearchResponse } from "./types";

function timestamp(seconds: number) {
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function LibrarySearch({ onOpen }: { onOpen: (hit: LibrarySearchHit) => void }) {
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
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    if (!response?.interaction_enabled || !analyticsOptIn || viewedSearches.current.has(response.request_id)) return;
    viewedSearches.current.add(response.request_id);
    void sendSearchInteraction(response.request_id, { event_id: crypto.randomUUID(),
      event_type: "search.results_viewed", ranks: response.results.map((_, index) => index + 1) }).catch(() => {});
  }, [response, analyticsOptIn]);

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

  return <section className="panel library-search" aria-labelledby="library-search-heading">
    <h3 id="library-search-heading">Search my videos</h3>
    <p>Find a passage across your uploads, then open it at the matching moment.</p>
    <form className="library-search-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label className="sr-only" htmlFor="library-query">Search my videos</label>
      <input id="library-query" type="search" value={query} maxLength={500}
        placeholder="Try a topic, phrase, or technical term" onChange={(event) => setQuery(event.target.value)} />
      <label>Search mode <select value={mode} onChange={event => setMode(event.target.value as "semantic" | "lexical")}>
        <option value="semantic">Semantic</option><option value="lexical">Keyword</option>
      </select></label>
      <button className="primary-button compact" disabled={busy || !query.trim()} type="submit">
        {busy ? <LoaderCircle className="spin" size={18} aria-hidden="true" /> : <Search size={18} aria-hidden="true" />}
        {busy ? "Searching…" : "Search"}
      </button>
    </form>
    {import.meta.env.VITE_SEARCH_ANALYTICS_ENABLED === "true" && <div className="search-consent">
      <label><input type="checkbox" checked={analyticsOptIn} onChange={event => { setAnalyticsOptIn(event.target.checked); if (!event.target.checked) setShareForReview(false); }} /> Help measure search performance and interactions for 7 days</label>
      <label><input type="checkbox" disabled={!analyticsOptIn} checked={shareForReview} onChange={event => setShareForReview(event.target.checked)} /> Also share this query with authorized reviewers for 7 days</label>
      <button type="button" onClick={() => { setShareForReview(false); void withdrawAllSearchSharing()
        .then(() => { setFeedbackStatus("All earlier query sharing withdrawn."); setAnalytics(null); })
        .catch(() => setFeedbackStatus("Could not withdraw sharing. Please retry.")); }}>Withdraw all earlier shared queries</button>
      {!response && feedbackStatus && <p role="status">{feedbackStatus}</p>}
    </div>}
    <div aria-live="polite" aria-atomic="true">
      {busy && <p>Searching your videos…</p>}
      {error && <p role="alert">{error}</p>}
      {response && <>
        {notices.map((notice) => <p className="library-search-notice" key={notice}>{notice}</p>)}
        {status === 503 ? <p role="alert">Search is temporarily unavailable. Please try again.</p>
          : status === 409 ? <p>No passages are searchable yet. Try again after search preparation completes.</p>
          : response.results.length === 0 ? <p>{response.coverage.total_videos === 0 ? "Your library is empty. Upload a video to start searching."
            : response.degraded || !response.coverage.complete ? "No matches in the currently available results. Try again when search is fully available."
            : `No matches for “${searchedQuery}”. Try another phrase or topic.`}</p>
          : <p>{response.results.length} {response.results.length === 1 ? "match" : "matches"} for “{searchedQuery}”</p>}
      </>}
    </div>
    {status === 200 && response && <div className="library-search-results">
      {response.results.map((hit, index) => <button type="button" className="library-search-result" key={hit.passage_id} onClick={() => {
        if (response.interaction_enabled && analyticsOptIn) void sendSearchInteraction(response.request_id, {
          event_id: crypto.randomUUID(), event_type: "search.result_opened", rank: index + 1 }).catch(() => {});
        onOpen(hit);
      }}>
        <strong>{hit.title?.trim() || "Untitled video"}</strong>
        <span className="result-time">Open at {timestamp(hit.start_seconds)}</span>
        <span className="library-passage">{hit.text}</span>
      </button>)}
    </div>}
    {response && import.meta.env.VITE_SEARCH_ANALYTICS_ENABLED === "true" && <div className="search-feedback">
      <span>Were these results useful?</span>
      <button type="button" disabled={!analyticsOptIn || !response.interaction_enabled} onClick={() => void feedback("helpful")}>Helpful</button>
      <button type="button" disabled={!analyticsOptIn || !response.interaction_enabled} onClick={() => void feedback("not_helpful")}>Not helpful</button>
      <button type="button" onClick={() => { const id = response.request_id; void withdrawSearchSharing(id)
        .then(() => { if (displayedSearch.current === id) setFeedbackStatus("Query sharing withdrawn."); setAnalytics(null); })
        .catch(() => { if (displayedSearch.current === id) setFeedbackStatus("Could not withdraw sharing. Please retry."); }); }}>Withdraw query sharing</button>
      <p role="status">{feedbackStatus}</p>
    </div>}
    {import.meta.env.VITE_SEARCH_ANALYTICS_ENABLED === "true" && <details className="search-analytics">
      <summary>Developer search analytics</summary>
      <button type="button" onClick={() => { setAnalyticsError(""); void getSearchAnalytics().then(setAnalytics)
        .catch(() => { setAnalytics(null); setAnalyticsError("Analytics unavailable or access not granted."); }); }}>Refresh analytics</button>
      {analyticsError && <p role="alert">{analyticsError}</p>}
      {analytics && <>
        <p>Last {analytics.retention_days} days · Updated {analytics.latest_update ?? "not yet"}. Opt-in observations only; engagement does not establish relevance.</p>
        <p>Pending events: {analytics.pending_events} · Expired: {analytics.expired_events} · Quarantined: {analytics.quarantined}</p>
        <div style={{ overflowX: "auto" }}><table><thead><tr><th>Traffic / mode / configuration</th><th>Searches</th><th>p50 ms</th><th>p95 ms</th><th>Errors</th><th>No results</th></tr></thead>
          <tbody>{Object.entries(analytics.modes).map(([key, value]) => <tr key={key}><td>{key}</td><td>{value.searches}</td><td>{value.p50_ms.toFixed(1)}</td><td>{value.p95_ms.toFixed(1)}</td><td>{value.errors}</td><td>{value.zero_results}</td></tr>)}</tbody></table></div>
        <h4>Shared examples needing review</h4>
        {analytics.review.length === 0 ? <p>No shared examples available.</p> : <ul>{analytics.review.map(item => <li key={item.search_id}>{item.query} — {item.mode}, {item.reason.replaceAll("_", " ")}</li>)}</ul>}
        {analytics.review_next_cursor && <button type="button" onClick={() => void getSearchAnalytics(analytics.review_next_cursor!).then(setAnalytics).catch(() => setAnalyticsError("Could not load the next page."))}>Next review page</button>}
      </>}
    </details>}
  </section>;
}
