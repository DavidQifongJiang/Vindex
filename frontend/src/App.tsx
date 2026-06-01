import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileText,
  LoaderCircle,
  Play,
  RefreshCw,
  Search,
  UploadCloud
} from "lucide-react";
import {
  getSegments,
  getTranscript,
  getVideoStatus,
  searchVideo,
  uploadVideo,
  videoFileUrl
} from "./api";
import type { SearchAlgorithm, SearchResult, Segment, VideoStatus } from "./types";

const SAVED_VIDEO_ID_KEY = "vindex:lastVideoId";

function formatTime(seconds: number) {
  const totalSeconds = Math.floor(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}

function statusTone(value?: string | null) {
  if (!value || value === "not_started") return "idle";
  if (["completed", "extracted", "processed"].includes(value)) return "good";
  if (["processing", "uploaded"].includes(value)) return "active";
  if (value === "failed") return "bad";
  return "idle";
}

function StatusPill({ label, value }: { label: string; value?: string | null }) {
  const tone = statusTone(value);
  const Icon = tone === "good" ? CheckCircle2 : tone === "bad" ? CircleAlert : Clock3;

  return (
    <div className={`status-pill ${tone}`}>
      <Icon size={16} aria-hidden="true" />
      <span>{label}</span>
      <strong>{value ?? "waiting"}</strong>
    </div>
  );
}

export function App() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [videoId, setVideoId] = useState("");
  const [status, setStatus] = useState<VideoStatus | null>(null);
  const [transcript, setTranscript] = useState("");
  const [segments, setSegments] = useState<Segment[]>([]);
  const [query, setQuery] = useState("");
  const [algorithm, setAlgorithm] = useState<SearchAlgorithm>("qdrant_embedding");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [message, setMessage] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isSearching, setIsSearching] = useState(false);

  const processed = status?.status === "processed";
  const failed = status?.status === "failed";
  const playerUrl = processed && videoId ? videoFileUrl(videoId) : "";

  const pipeline = useMemo(
    () => [
      { label: "Video", value: status?.status },
      { label: "Audio", value: status?.audio_status },
      { label: "Transcript", value: status?.transcript_status },
      { label: "Segments", value: status?.segments_status },
      { label: "Embedding", value: status?.embedding_status }
    ],
    [status]
  );

  const refreshStatus = useCallback(
    async (targetVideoId = videoId, showSpinner = true) => {
      if (!targetVideoId.trim()) return;

      try {
        if (showSpinner) setIsRefreshing(true);
        const nextStatus = await getVideoStatus(targetVideoId.trim());
        setStatus(nextStatus);
        setMessage("");
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Could not load status");
      } finally {
        if (showSpinner) setIsRefreshing(false);
      }
    },
    [videoId]
  );

  const loadArtifacts = useCallback(async () => {
    if (!videoId || !processed) return;

    try {
      const [transcriptResponse, segmentsResponse] = await Promise.all([
        getTranscript(videoId),
        getSegments(videoId)
      ]);
      setTranscript(transcriptResponse.transcript);
      setSegments(segmentsResponse.segments);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load transcript");
    }
  }, [processed, videoId]);

  useEffect(() => {
    const savedVideoId = localStorage.getItem(SAVED_VIDEO_ID_KEY);
    if (savedVideoId) {
      setVideoId(savedVideoId);
      void getVideoStatus(savedVideoId)
        .then((nextStatus) => setStatus(nextStatus))
        .catch(() => localStorage.removeItem(SAVED_VIDEO_ID_KEY));
    }
  }, []);

  useEffect(() => {
    if (!videoId || !status || processed || failed) return;

    const timer = window.setInterval(() => {
      void refreshStatus(videoId, false);
    }, 4000);

    return () => window.clearInterval(timer);
  }, [failed, processed, refreshStatus, status, videoId]);

  useEffect(() => {
    if (processed && !transcript) {
      void loadArtifacts();
    }
  }, [loadArtifacts, processed, transcript]);

  async function handleUpload() {
    if (!selectedFile) {
      setMessage("Choose a video file first");
      return;
    }

    try {
      setIsUploading(true);
      setStatus(null);
      setTranscript("");
      setSegments([]);
      setResults([]);

      const uploaded = await uploadVideo(selectedFile);
      setVideoId(uploaded.video_id);
      localStorage.setItem(SAVED_VIDEO_ID_KEY, uploaded.video_id);
      setMessage("Upload accepted. Processing has started.");
      await refreshStatus(uploaded.video_id, false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleLoadVideo() {
    if (!videoId.trim()) return;
    localStorage.setItem(SAVED_VIDEO_ID_KEY, videoId.trim());
    setTranscript("");
    setSegments([]);
    setResults([]);
    await refreshStatus(videoId.trim());
  }

  async function handleSearch() {
    if (!videoId || !query.trim()) return;

    try {
      setIsSearching(true);
      const response = await searchVideo(videoId, query.trim(), algorithm);
      setResults(response.results);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Search failed");
    } finally {
      setIsSearching(false);
    }
  }

  function jumpTo(seconds: number) {
    if (!videoRef.current) return;
    videoRef.current.currentTime = seconds;
    void videoRef.current.play();
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Vindex</p>
          <h1>Video Semantic Search</h1>
        </div>
        <div className="environment-pill">
          <span />
          AWS-ready pipeline
        </div>
      </header>

      <section className="workspace">
        <div className="left-column">
          <section className="panel upload-panel">
            <div className="panel-heading">
              <UploadCloud size={20} aria-hidden="true" />
              <h2>Upload</h2>
            </div>

            <label className="drop-zone">
              <input
                type="file"
                accept="video/*"
                onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
              />
              <span>{selectedFile ? selectedFile.name : "Choose video file"}</span>
            </label>

            <button className="primary-button" disabled={isUploading} onClick={handleUpload}>
              {isUploading ? <LoaderCircle className="spin" size={18} /> : <UploadCloud size={18} />}
              {isUploading ? "Uploading" : "Upload video"}
            </button>

            <div className="video-id-row">
              <input
                value={videoId}
                onChange={(event) => setVideoId(event.target.value)}
                placeholder="video_id"
              />
              <button className="icon-button" title="Load status" onClick={handleLoadVideo}>
                <RefreshCw size={18} className={isRefreshing ? "spin" : ""} />
              </button>
            </div>

            {message && <p className="message">{message}</p>}
          </section>

          <section className="panel">
            <div className="panel-heading">
              <Clock3 size={20} aria-hidden="true" />
              <h2>Pipeline</h2>
            </div>
            <div className="status-grid">
              {pipeline.map((item) => (
                <StatusPill key={item.label} label={item.label} value={item.value} />
              ))}
            </div>
            {status?.error_message && <p className="error-message">{status.error_message}</p>}
          </section>
        </div>

        <div className="center-column">
          <section className="player-surface">
            {playerUrl ? (
              <video ref={videoRef} src={playerUrl} controls />
            ) : (
              <div className="empty-player">
                <Play size={42} aria-hidden="true" />
                <span>{status ? `Current status: ${status.status}` : "No processed video loaded"}</span>
              </div>
            )}
          </section>

          <section className="panel search-panel">
            <div className="search-row">
              <Search size={20} aria-hidden="true" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void handleSearch();
                }}
                placeholder="Search inside this video"
              />
              <select
                value={algorithm}
                onChange={(event) => setAlgorithm(event.target.value as SearchAlgorithm)}
              >
                <option value="qdrant_embedding">Semantic</option>
                <option value="exact">Exact</option>
                <option value="overlap">Overlap</option>
                <option value="stopword_overlap">Stopword overlap</option>
              </select>
              <button className="primary-button compact" disabled={isSearching} onClick={handleSearch}>
                {isSearching ? <LoaderCircle className="spin" size={18} /> : <Search size={18} />}
                Search
              </button>
            </div>

            <div className="results-list">
              {results.map((result) => (
                <button
                  className="result-item"
                  key={`${result.start}-${result.end}-${result.text}`}
                  onClick={() => jumpTo(result.start)}
                >
                  <span className="result-time">{formatTime(result.start)}</span>
                  <span className="result-text">{result.text}</span>
                  <span className="result-score">{result.score.toFixed(3)}</span>
                </button>
              ))}
            </div>
          </section>
        </div>

        <aside className="right-column">
          <section className="panel transcript-panel">
            <div className="panel-heading">
              <FileText size={20} aria-hidden="true" />
              <h2>Transcript</h2>
            </div>
            <p>{transcript || "Transcript will appear after processing."}</p>
          </section>

          <section className="panel segments-panel">
            <div className="panel-heading">
              <FileText size={20} aria-hidden="true" />
              <h2>Segments</h2>
            </div>
            <div className="segment-list">
              {segments.map((segment) => (
                <button
                  className="segment-item"
                  key={`${segment.start}-${segment.end}`}
                  onClick={() => jumpTo(segment.start)}
                >
                  <strong>{formatTime(segment.start)}</strong>
                  <span>{segment.text}</span>
                </button>
              ))}
            </div>
          </section>
        </aside>
      </section>
    </main>
  );
}
