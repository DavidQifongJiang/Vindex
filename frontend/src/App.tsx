import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileText,
  LoaderCircle,
  Play,
  RefreshCw,
  Search,
  Trash2,
  UploadCloud,
  Video
} from "lucide-react";
import {
  deleteVideo,
  getMe,
  getSegments,
  getTranscript,
  getVideoFileUrl,
  getVideoMetrics,
  getVideoThumbnailUrl,
  getVideoStatus,
  listPublicVideos,
  listVideos,
  searchVideo,
  setAuthTokenProvider,
  uploadVideo
} from "./api";
import {
  AUTH_ENABLED,
  type AuthSession,
  handleAuthCallback,
  signIn,
  signOut
} from "./auth";
import type {
  SearchAlgorithm,
  SearchResult,
  Segment,
  UserProfile,
  VideoMetrics,
  VideoStatus
} from "./types";

const SAVED_VIDEO_ID_KEY = "vindex:lastVideoId";

type ViewMode = "library" | "watch";
type FeedMode = "library" | "public";
type VideoVisibility = "private" | "public";

function formatTime(seconds: number) {
  const totalSeconds = Math.floor(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}

function formatDuration(seconds?: number | null) {
  if (seconds === undefined || seconds === null) return "--";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(seconds >= 10 ? 1 : 2)} s`;
}

function formatNumber(value?: number | null, digits = 3) {
  if (value === undefined || value === null) return "--";
  return value.toFixed(digits);
}

function formatInteger(value?: number | null) {
  if (value === undefined || value === null) return "--";
  return new Intl.NumberFormat().format(value);
}

function formatBytes(value?: number | null) {
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

function formatDate(value?: string) {
  if (!value) return "--";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

function statusTone(value?: string | null) {
  if (!value || value === "not_started") return "idle";
  if (["completed", "extracted", "processed"].includes(value)) return "good";
  if (["processing", "uploaded"].includes(value)) return "active";
  if (value === "failed") return "bad";
  return "idle";
}

function readableStatus(value?: string | null) {
  if (!value) return "waiting";
  return value.replaceAll("_", " ");
}

function filenameToTitle(filename?: string | null) {
  return (filename || "Untitled video").replace(/\.[^/.]+$/, "").replace(/[-_]+/g, " ");
}

function displayVideoTitle(video: VideoStatus, index: number) {
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

function initials(profile?: UserProfile | null) {
  const source = profile?.name || profile?.email || "V";
  return source
    .split(/\s|@/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "V";
}

function StatusPill({ label, value }: { label: string; value?: string | null }) {
  const tone = statusTone(value);
  const Icon = tone === "good" ? CheckCircle2 : tone === "bad" ? CircleAlert : Clock3;

  return (
    <div className={`status-pill ${tone}`}>
      <Icon size={16} aria-hidden="true" />
      <span>{label}</span>
      <strong>{readableStatus(value)}</strong>
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function VideoCard({
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
  }, [video.status, video.video_id]);

  return (
    <article className={`video-card ${isSelected ? "selected" : ""}`}>
      <button className="video-card-open" onClick={onOpen}>
        <div className="thumbnail-frame">
          <div className="thumbnail-fallback">
            <Play size={34} aria-hidden="true" />
          </div>
          {canShowThumbnail && (
            <img
              alt=""
              src={thumbnailUrl}
              onError={() => setThumbnailFailed(true)}
            />
          )}
          <span className={`thumbnail-status ${statusTone(video.status)}`}>
            {readableStatus(video.status)}
          </span>
        </div>
        <div className="video-card-body">
          <h3>{displayVideoTitle(video, index)}</h3>
          <div className="video-card-meta">
            <p>{formatDate(video.created_at)}</p>
            <span className={`visibility-badge ${video.visibility ?? "private"}`}>
              {video.visibility ?? "private"}
            </span>
          </div>
        </div>
      </button>
      {canDelete && (
        <button
          className="card-delete-button"
          disabled={isDeleting}
          title="Delete video"
          onClick={onDelete}
        >
          {isDeleting ? <LoaderCircle className="spin" size={17} /> : <Trash2 size={17} />}
        </button>
      )}
    </article>
  );
}

export function App() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [authSession, setAuthSession] = useState<AuthSession | null>(null);
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  const [isAuthLoading, setIsAuthLoading] = useState(true);
  const [viewMode, setViewMode] = useState<ViewMode>("library");
  const [feedMode, setFeedMode] = useState<FeedMode>("library");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [videoTitle, setVideoTitle] = useState("");
  const [videoVisibility, setVideoVisibility] = useState<VideoVisibility>("private");
  const [videos, setVideos] = useState<VideoStatus[]>([]);
  const [videoId, setVideoId] = useState("");
  const [status, setStatus] = useState<VideoStatus | null>(null);
  const [playerUrl, setPlayerUrl] = useState("");
  const [metrics, setMetrics] = useState<VideoMetrics | null>(null);
  const [transcript, setTranscript] = useState("");
  const [segments, setSegments] = useState<Segment[]>([]);
  const [query, setQuery] = useState("");
  const [algorithm, setAlgorithm] = useState<SearchAlgorithm>("qdrant_embedding");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [message, setMessage] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [isLoadingMetrics, setIsLoadingMetrics] = useState(false);
  const [isLoadingVideos, setIsLoadingVideos] = useState(false);
  const [deletingVideoId, setDeletingVideoId] = useState("");

  const processed = status?.status === "processed";
  const playable = Boolean(status?.processed_path);
  const searchable = Boolean(
    status?.embedding_status === "completed" &&
    status?.segments_status === "completed" &&
    status?.transcript_status === "completed"
  );
  const failed = status?.status === "failed";
  const isSignedIn = !AUTH_ENABLED || Boolean(authSession);

  const selectedVideoIndex = videos.findIndex((video) => video.video_id === videoId);
  const selectedTitle = status
    ? displayVideoTitle(status, selectedVideoIndex >= 0 ? selectedVideoIndex : 0)
    : "Video";
  const selectedVideoIsOwned = Boolean(
    currentUser?.user_id && status?.owner_user_id === currentUser.user_id
  );

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

  const metricRows = useMemo(
    () => [
      {
        label: "Upload response",
        value: formatDuration(metrics?.upload?.upload_response_latency_seconds)
      },
      {
        label: "Raw upload",
        value: formatDuration(metrics?.upload?.s3_raw_upload_seconds)
      },
      {
        label: "Uploaded size",
        value: formatBytes(metrics?.upload?.uploaded_bytes)
      },
      {
        label: "Celery enqueue",
        value: formatDuration(metrics?.upload?.celery_enqueue_seconds)
      },
      {
        label: "Queue wait",
        value: formatDuration(metrics?.processing?.queue_wait_seconds)
      },
      {
        label: "Time searchable",
        value: formatDuration(metrics?.processing?.time_to_searchable_seconds)
      },
      {
        label: "Total processing",
        value: formatDuration(metrics?.processing?.total_processing_seconds)
      },
      {
        label: "FFmpeg preset",
        value: metrics?.processing?.ffmpeg_preset ?? "-"
      },
      {
        label: "Raw download",
        value: formatDuration(metrics?.processing?.s3_raw_download_seconds)
      },
      {
        label: "Video transcode",
        value: formatDuration(metrics?.processing?.video_transcode_seconds)
      },
      {
        label: "Audio extract",
        value: formatDuration(metrics?.processing?.audio_extraction_seconds)
      },
      {
        label: "Whisper model",
        value: formatDuration(metrics?.processing?.whisper_model_load_or_get_seconds)
      },
      {
        label: "Whisper",
        value: formatDuration(metrics?.processing?.whisper_transcription_seconds)
      },
      {
        label: "Embedding model",
        value: formatDuration(metrics?.processing?.embedding_model_load_or_get_seconds)
      },
      {
        label: "Embeddings",
        value: formatDuration(metrics?.processing?.embedding_generation_seconds)
      },
      {
        label: "S3 artifacts",
        value: formatDuration(metrics?.processing?.s3_artifact_write_seconds)
      },
      {
        label: "Thumbnail",
        value: formatDuration(metrics?.processing?.thumbnail_generation_seconds)
      },
      {
        label: "Qdrant upsert",
        value: formatDuration(metrics?.processing?.qdrant_upsert_seconds)
      },
      {
        label: "Segments",
        value: formatInteger(metrics?.processing?.segment_count)
      },
      {
        label: "Search latency",
        value: formatDuration(metrics?.search?.search_latency_seconds)
      },
      {
        label: "Query embed",
        value: formatDuration(metrics?.search?.query_embedding_seconds)
      },
      {
        label: "Qdrant search",
        value: formatDuration(metrics?.search?.qdrant_search_seconds)
      },
      {
        label: "Results",
        value: formatInteger(metrics?.search?.result_count)
      },
      {
        label: "Top score",
        value: formatNumber(metrics?.search?.top_score)
      }
    ],
    [metrics]
  );

  const refreshMetrics = useCallback(
    async (targetVideoId = videoId, showSpinner = false) => {
      if (!targetVideoId.trim()) return;

      try {
        if (showSpinner) setIsLoadingMetrics(true);
        const nextMetrics = await getVideoMetrics(targetVideoId.trim());
        setMetrics(nextMetrics);
      } catch {
        setMetrics(null);
      } finally {
        if (showSpinner) setIsLoadingMetrics(false);
      }
    },
    [videoId]
  );

  const refreshVideos = useCallback(
    async (showSpinner = false) => {
      try {
        if (showSpinner) setIsLoadingVideos(true);
        if (feedMode === "library" && !isSignedIn) {
          setVideos([]);
          return;
        }
        const response = feedMode === "public" ? await listPublicVideos() : await listVideos();
        setVideos(response.videos);
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Could not load videos");
      } finally {
        if (showSpinner) setIsLoadingVideos(false);
      }
    },
    [feedMode, isSignedIn]
  );

  const refreshStatus = useCallback(
    async (targetVideoId = videoId, showSpinner = true) => {
      if (!targetVideoId.trim()) return;

      try {
        if (showSpinner) setIsRefreshing(true);
        const nextStatus = await getVideoStatus(targetVideoId.trim());
        setStatus(nextStatus);
        void refreshMetrics(targetVideoId.trim(), false);
        setMessage("");
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Could not load status");
      } finally {
        if (showSpinner) setIsRefreshing(false);
      }
    },
    [refreshMetrics, videoId]
  );

  const loadArtifacts = useCallback(async () => {
    if (!videoId || !searchable) return;

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
  }, [searchable, videoId]);

  const loadVideoById = useCallback(
    async (targetVideoId: string, nextView: ViewMode = "watch") => {
      if (!targetVideoId.trim()) return;

      const nextVideoId = targetVideoId.trim();
      setViewMode(nextView);
      setVideoId(nextVideoId);
      localStorage.setItem(SAVED_VIDEO_ID_KEY, nextVideoId);
      setTranscript("");
      setSegments([]);
      setResults([]);
      await refreshStatus(nextVideoId);
      await refreshMetrics(nextVideoId, true);
    },
    [refreshMetrics, refreshStatus]
  );

  useEffect(() => {
    let active = true;

    async function initializeAuth() {
      try {
        const session = await handleAuthCallback();
        if (!active) return;

        setAuthSession(session);
        setAuthTokenProvider(() => session?.idToken ?? null);

        if (AUTH_ENABLED && !session) {
          setCurrentUser(null);
          return;
        }

        const me = await getMe();
        if (!active) return;
        setCurrentUser(me);
      } catch (error) {
        if (!active) return;
        setMessage(error instanceof Error ? error.message : "Could not initialize auth");
      } finally {
        if (active) setIsAuthLoading(false);
      }
    }

    void initializeAuth();

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (isAuthLoading) return;
    void refreshVideos(false);

    const savedVideoId = localStorage.getItem(SAVED_VIDEO_ID_KEY);
    if (savedVideoId && isSignedIn) {
      setVideoId(savedVideoId);
      void Promise.all([getVideoStatus(savedVideoId), getVideoMetrics(savedVideoId)])
        .then(([nextStatus, nextMetrics]) => {
          setStatus(nextStatus);
          setMetrics(nextMetrics);
        })
        .catch(() => localStorage.removeItem(SAVED_VIDEO_ID_KEY));
    }
  }, [isAuthLoading, isSignedIn, refreshVideos]);

  useEffect(() => {
    if (!videoId || !status || processed || failed) return;

    const timer = window.setInterval(() => {
      void refreshStatus(videoId, false);
      void refreshVideos(false);
    }, 4000);

    return () => window.clearInterval(timer);
  }, [failed, processed, refreshStatus, refreshVideos, status, videoId]);

  useEffect(() => {
    if (searchable && !transcript) {
      void loadArtifacts();
    }
  }, [loadArtifacts, searchable, transcript]);

  useEffect(() => {
    let active = true;
    setPlayerUrl("");

    if (!playable || !videoId) return;

    void getVideoFileUrl(videoId)
      .then((response) => {
        if (active) setPlayerUrl(response.url);
      })
      .catch((error) => {
        if (active) {
          setMessage(error instanceof Error ? error.message : "Could not load video file");
        }
      });

    return () => {
      active = false;
    };
  }, [playable, videoId]);

  async function handleUpload() {
    if (!isSignedIn) {
      setMessage("Sign in before uploading private videos");
      return;
    }

    if (!selectedFile) {
      setMessage("Choose a video file first");
      return;
    }

    try {
      setIsUploading(true);
      setStatus(null);
      setMetrics(null);
      setTranscript("");
      setSegments([]);
      setResults([]);

      const cleanTitle = videoTitle.trim() || filenameToTitle(selectedFile.name);
      const uploaded = await uploadVideo(selectedFile, cleanTitle, videoVisibility);
      setViewMode("watch");
      setVideoId(uploaded.video_id);
      setVideoTitle("");
      setVideoVisibility("private");
      setSelectedFile(null);
      localStorage.setItem(SAVED_VIDEO_ID_KEY, uploaded.video_id);
      setMessage("Upload accepted. Processing has started.");
      await refreshVideos(false);
      await refreshStatus(uploaded.video_id, false);
      await refreshMetrics(uploaded.video_id, false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleSearch() {
    if (!videoId || !query.trim()) return;

    if (!searchable) {
      setMessage("Search is not ready yet.");
      return;
    }

    try {
      setIsSearching(true);
      const response = await searchVideo(videoId, query.trim(), algorithm);
      setResults(response.results);
      await refreshMetrics(videoId, false);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Search failed");
    } finally {
      setIsSearching(false);
    }
  }

  async function handleDeleteVideo(targetVideo: VideoStatus, index: number) {
    const targetTitle = displayVideoTitle(targetVideo, index);
    const confirmed = window.confirm(
      `Delete "${targetTitle}"? This removes the database row, stored files, metrics, transcript, and search vectors.`
    );

    if (!confirmed) return;

    try {
      setDeletingVideoId(targetVideo.video_id);
      await deleteVideo(targetVideo.video_id);
      setVideos((currentVideos) =>
        currentVideos.filter((video) => video.video_id !== targetVideo.video_id)
      );

      if (targetVideo.video_id === videoId) {
        localStorage.removeItem(SAVED_VIDEO_ID_KEY);
        setViewMode("library");
        setVideoId("");
        setStatus(null);
        setMetrics(null);
        setTranscript("");
        setSegments([]);
        setResults([]);
        setPlayerUrl("");
      }

      setMessage(`Deleted "${targetTitle}".`);
      await refreshVideos(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Delete failed");
    } finally {
      setDeletingVideoId("");
    }
  }

  function jumpTo(seconds: number) {
    if (!videoRef.current) return;
    videoRef.current.currentTime = seconds;
    void videoRef.current.play();
  }

  function renderUploadCard() {
    return (
      <section className="panel upload-panel">
        <div className="panel-heading">
          <UploadCloud size={20} aria-hidden="true" />
          <h2>Upload</h2>
        </div>

        <label className="drop-zone">
          <input
            type="file"
            accept="video/*"
            onChange={(event) => {
              const nextFile = event.target.files?.[0] ?? null;
              setSelectedFile(nextFile);
              if (nextFile && !videoTitle.trim()) {
                setVideoTitle(filenameToTitle(nextFile.name));
              }
            }}
          />
          <span>{selectedFile ? selectedFile.name : "Choose video file"}</span>
        </label>

        <div className="upload-fields">
          <label className="form-field">
            <span>Title</span>
            <input
              value={videoTitle}
              onChange={(event) => setVideoTitle(event.target.value)}
              placeholder="Name this video"
            />
          </label>

          <label className="form-field">
            <span>Visibility</span>
            <select
              value={videoVisibility}
              onChange={(event) => setVideoVisibility(event.target.value as VideoVisibility)}
            >
              <option value="private">Private</option>
              <option value="public">Public</option>
            </select>
          </label>
        </div>

        <button className="primary-button" disabled={isUploading || !isSignedIn} onClick={handleUpload}>
          {isUploading ? <LoaderCircle className="spin" size={18} /> : <UploadCloud size={18} />}
          {isUploading ? "Uploading" : "Upload video"}
        </button>

        {message && <p className="message">{message}</p>}
      </section>
    );
  }

  function renderLibrary() {
    return (
      <section className="library-view">
        <div className="library-header">
          <div>
            <p className="eyebrow">{feedMode === "public" ? "Explore" : "Library"}</p>
            <h2>{feedMode === "public" ? "Public videos" : "My videos"}</h2>
          </div>
          <div className="library-actions">
            <div className="feed-tabs" aria-label="Video feed">
              <button
                className={feedMode === "library" ? "active" : ""}
                onClick={() => setFeedMode("library")}
              >
                My library
              </button>
              <button
                className={feedMode === "public" ? "active" : ""}
                onClick={() => setFeedMode("public")}
              >
                Public explore
              </button>
            </div>
            <button
              className="secondary-button"
              disabled={isLoadingVideos}
              onClick={() => void refreshVideos(true)}
            >
              <RefreshCw size={17} className={isLoadingVideos ? "spin" : ""} />
              Refresh
            </button>
          </div>
        </div>

        <div className="library-layout">
          {renderUploadCard()}

          <div className="video-grid">
            {videos.length === 0 ? (
              <div className="empty-library">
                <Video size={36} aria-hidden="true" />
                <h3>{feedMode === "public" ? "No public videos yet" : "No videos yet"}</h3>
                <p>
                  {feedMode === "public"
                    ? "Public uploads will appear here when someone marks a video public."
                    : "Upload a video and Vindex will create a searchable transcript."}
                </p>
              </div>
            ) : (
              videos.map((video, index) => (
                <VideoCard
                  canDelete={Boolean(currentUser?.user_id && video.owner_user_id === currentUser.user_id)}
                  index={index}
                  isDeleting={deletingVideoId === video.video_id}
                  isSelected={video.video_id === videoId}
                  key={video.video_id}
                  video={video}
                  onDelete={() => void handleDeleteVideo(video, index)}
                  onOpen={() => void loadVideoById(video.video_id)}
                />
              ))
            )}
          </div>
        </div>
      </section>
    );
  }

  function renderWatchView() {
    return (
      <section className="watch-view">
        <div className="watch-top">
          <button className="secondary-button" onClick={() => setViewMode("library")}>
            <ArrowLeft size={17} />
            Library
          </button>
          <div>
            <p className="eyebrow">Now watching</p>
            <h2>{selectedTitle}</h2>
          </div>
          <div className="watch-actions">
            {status && selectedVideoIsOwned && (
              <button
                className="danger-button"
                disabled={deletingVideoId === status.video_id}
                onClick={() => void handleDeleteVideo(status, selectedVideoIndex >= 0 ? selectedVideoIndex : 0)}
              >
                {deletingVideoId === status.video_id ? (
                  <LoaderCircle className="spin" size={17} />
                ) : (
                  <Trash2 size={17} />
                )}
                Delete
              </button>
            )}
            <button
              className="secondary-button"
              disabled={!videoId || isRefreshing}
              onClick={() => void refreshStatus(videoId, true)}
            >
              <RefreshCw size={17} className={isRefreshing ? "spin" : ""} />
              Status
            </button>
          </div>
        </div>

        <section className="workspace">
          <div className="left-column">
            {renderUploadCard()}

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

            <section className="panel metrics-panel">
              <div className="panel-heading split-heading">
                <div>
                  <Activity size={20} aria-hidden="true" />
                  <h2>Metrics</h2>
                </div>
                <button
                  className="mini-icon-button"
                  title="Refresh metrics"
                  disabled={!videoId || isLoadingMetrics}
                  onClick={() => void refreshMetrics(videoId, true)}
                >
                  <RefreshCw size={16} className={isLoadingMetrics ? "spin" : ""} />
                </button>
              </div>
              <div className="metrics-grid">
                {metricRows.map((item) => (
                  <MetricRow key={item.label} label={item.label} value={item.value} />
                ))}
              </div>
            </section>
          </div>

          <div className="center-column">
            <section className="player-surface">
              {playerUrl ? (
                <video ref={videoRef} src={playerUrl} controls />
              ) : (
                <div className="empty-player">
                  <Play size={42} aria-hidden="true" />
                  <span>{status ? `Current status: ${readableStatus(status.status)}` : "No video loaded"}</span>
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
                <button className="primary-button compact" disabled={isSearching || !searchable} onClick={handleSearch}>
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
      </section>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Vindex</p>
          <h1>Video Semantic Search</h1>
        </div>
        <div className="topbar-actions">
          <div className="profile-pill">
            {currentUser?.picture_url ? (
              <img alt="" src={currentUser.picture_url} />
            ) : (
              <span className="profile-initials">{initials(currentUser)}</span>
            )}
            <div>
              <strong>{currentUser?.name || currentUser?.email || "Guest"}</strong>
              <small>{AUTH_ENABLED ? "Cognito profile" : "Local dev profile"}</small>
            </div>
          </div>
          {AUTH_ENABLED && (
            authSession ? (
              <button className="secondary-button" onClick={signOut}>
                Sign out
              </button>
            ) : (
              <button className="secondary-button" disabled={isAuthLoading} onClick={() => void signIn()}>
                Sign in
              </button>
            )
          )}
          <div className="environment-pill">
            <span />
            AWS-ready pipeline
          </div>
        </div>
      </header>

      {viewMode === "library" ? renderLibrary() : renderWatchView()}
    </main>
  );
}
