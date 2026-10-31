import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, CircleAlert, LoaderCircle, PanelRight, Play, RefreshCw, Trash2, UploadCloud, Video } from "lucide-react";
import { AppSidebar } from "@/components/AppSidebar";
import { DeleteVideoDialog } from "@/components/DeleteVideoDialog";
import { DetailsSheet } from "@/components/DetailsSheet";
import { MomentsRow } from "@/components/MomentsRow";
import { StatusBadge } from "@/components/StatusBadge";
import { TranscriptPanel } from "@/components/TranscriptPanel";
import { UploadDialog } from "@/components/UploadDialog";
import { VideoCard } from "@/components/VideoCard";
import { VideoPlayer } from "@/components/VideoPlayer";
import { VisibilityIcon } from "@/components/VisibilityIcon";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { displayVideoTitle, filenameToTitle, formatDate, readableStatus, videoGradient } from "@/lib/format";
import { highlightPattern } from "@/lib/highlight";
import { cn } from "@/lib/utils";
import { LibrarySearch } from "./LibrarySearch";
import {
  deleteVideo,
  getMe,
  getSegments,
  getTranscript,
  getVideoFileUrl,
  getVideoMetrics,
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
  handleAuthCallback
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

export function App() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const pendingSeek = useRef<{ videoId: string; seconds: number } | null>(null);
  const selectedVideoIdRef = useRef("");
  const momentStopTime = useRef<number | null>(null);
  const videoListRequest = useRef(0);
  const videoSearchRequest = useRef(0);
  const [authSession, setAuthSession] = useState<AuthSession | null>(null);
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  const [isAuthLoading, setIsAuthLoading] = useState(true);
  const [viewMode, setViewMode] = useState<ViewMode>("library");
  const [feedMode, setFeedMode] = useState<FeedMode>("library");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [videoTitle, setVideoTitle] = useState("");
  const [videoVisibility, setVideoVisibility] = useState<VideoVisibility>("private");
  const [videos, setVideos] = useState<VideoStatus[]>([]);
  const [videosLoaded, setVideosLoaded] = useState(false);
  const [videoId, setVideoId] = useState("");
  const [status, setStatus] = useState<VideoStatus | null>(null);
  const [playerUrl, setPlayerUrl] = useState("");
  const [metrics, setMetrics] = useState<VideoMetrics | null>(null);
  const [transcript, setTranscript] = useState("");
  const [segments, setSegments] = useState<Segment[]>([]);
  const [query, setQuery] = useState("");
  const [algorithm, setAlgorithm] = useState<SearchAlgorithm>("qdrant_embedding");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searched, setSearched] = useState<{ query: string; algorithm: SearchAlgorithm } | null>(null);
  const [selectedMoment, setSelectedMoment] = useState<number | null>(null);
  const [isMomentFinished, setIsMomentFinished] = useState(false);
  const [activeSegmentStart, setActiveSegmentStart] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [isDetailsOpen, setIsDetailsOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ video: VideoStatus; index: number } | null>(null);
  const [libraryResultsActive, setLibraryResultsActive] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
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
  const libraryFeedActive = feedMode === "library";
  const showLibrarySearch = libraryFeedActive && isSignedIn && import.meta.env.VITE_LIBRARY_SEARCH_ENABLED === "true";

  const moments = useMemo(() => [...results].sort((a, b) => a.start - b.start), [results]);
  const activeMoment = selectedMoment === null ? null : moments[selectedMoment] ?? null;

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

  const refreshMetrics = useCallback(
    async (targetVideoId = videoId) => {
      if (!targetVideoId.trim()) return;

      try {
        const nextMetrics = await getVideoMetrics(targetVideoId.trim());
        if (selectedVideoIdRef.current !== targetVideoId.trim()) return;
        setMetrics(nextMetrics);
      } catch {
        setMetrics(null);
      }
    },
    [videoId]
  );

  const refreshVideos = useCallback(
    async (showSpinner = false) => {
      const requestId = ++videoListRequest.current;
      try {
        if (showSpinner) setIsLoadingVideos(true);
        if (feedMode === "library" && !isSignedIn) {
          setVideos([]);
          return;
        }
        const response = feedMode === "public" ? await listPublicVideos() : await listVideos();
        if (videoListRequest.current !== requestId) return;
        setVideos(response.videos);
      } catch (error) {
        if (videoListRequest.current !== requestId) return;
        setMessage(error instanceof Error ? error.message : "Could not load videos");
      } finally {
        if (videoListRequest.current === requestId) {
          setVideosLoaded(true);
          setIsLoadingVideos(false);
        }
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
        if (selectedVideoIdRef.current !== targetVideoId.trim()) return;
        setStatus(nextStatus);
        void refreshMetrics(targetVideoId.trim());
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
      if (selectedVideoIdRef.current !== videoId) return;
      setTranscript(transcriptResponse.transcript);
      setSegments(segmentsResponse.segments);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load transcript");
    }
  }, [searchable, videoId]);

  const resetMoment = useCallback(() => {
    momentStopTime.current = null;
    setSelectedMoment(null);
    setIsMomentFinished(false);
  }, []);

  const clearVideoSearch = useCallback(() => {
    videoSearchRequest.current += 1;
    setIsSearching(false);
    setQuery("");
    setResults([]);
    setSearched(null);
    resetMoment();
  }, [resetMoment]);

  const loadVideoById = useCallback(
    async (targetVideoId: string, nextView: ViewMode = "watch", seconds?: number) => {
      if (!targetVideoId.trim()) return;

      const nextVideoId = targetVideoId.trim();
      selectedVideoIdRef.current = nextVideoId;
      pendingSeek.current = seconds === undefined ? null : { videoId: nextVideoId, seconds };
      setStatus(null);
      setPlayerUrl("");
      setViewMode(nextView);
      setVideoId(nextVideoId);
      localStorage.setItem(SAVED_VIDEO_ID_KEY, nextVideoId);
      setTranscript("");
      setSegments([]);
      clearVideoSearch();
      setActiveSegmentStart(null);
      await refreshStatus(nextVideoId);
      await refreshMetrics(nextVideoId);
    },
    [refreshMetrics, refreshStatus, clearVideoSearch]
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
      selectedVideoIdRef.current = savedVideoId;
      setVideoId(savedVideoId);
      void Promise.all([getVideoStatus(savedVideoId), getVideoMetrics(savedVideoId)])
        .then(([nextStatus, nextMetrics]) => {
          if (selectedVideoIdRef.current !== savedVideoId) return;
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

    if (!playable || !videoId || status?.video_id !== videoId) return;

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
  }, [playable, videoId, status?.video_id]);

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
      setMessage("");

      const cleanTitle = videoTitle.trim() || filenameToTitle(selectedFile.name);
      const uploaded = await uploadVideo(selectedFile, cleanTitle, videoVisibility);
      setStatus(null);
      setMetrics(null);
      setTranscript("");
      setSegments([]);
      clearVideoSearch();
      setActiveSegmentStart(null);
      setViewMode("watch");
      setVideoId(uploaded.video_id);
      selectedVideoIdRef.current = uploaded.video_id;
      pendingSeek.current = null;
      setVideoTitle("");
      setVideoVisibility("private");
      setSelectedFile(null);
      setIsUploadOpen(false);
      localStorage.setItem(SAVED_VIDEO_ID_KEY, uploaded.video_id);
      setMessage("Upload accepted. Processing has started.");
      await refreshVideos(false);
      await refreshStatus(uploaded.video_id, false);
      await refreshMetrics(uploaded.video_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleSearch() {
    if (!videoId) return;

    const cleanQuery = query.trim();
    if (!cleanQuery) {
      clearVideoSearch();
      return;
    }

    if (!searchable) {
      setMessage("Search is not ready yet.");
      return;
    }

    const requestId = ++videoSearchRequest.current;
    try {
      setIsSearching(true);
      const response = await searchVideo(videoId, cleanQuery, algorithm);
      if (selectedVideoIdRef.current !== videoId || videoSearchRequest.current !== requestId) return;
      setResults(response.results);
      setSearched({ query: cleanQuery, algorithm });
      resetMoment();
      await refreshMetrics(videoId);
      if (selectedVideoIdRef.current !== videoId || videoSearchRequest.current !== requestId) return;
      setMessage("");
    } catch (error) {
      if (selectedVideoIdRef.current !== videoId || videoSearchRequest.current !== requestId) return;
      setMessage(error instanceof Error ? error.message : "Search failed");
    } finally {
      if (videoSearchRequest.current === requestId) setIsSearching(false);
    }
  }

  async function handleDeleteVideo(targetVideo: VideoStatus, index: number) {
    const targetTitle = displayVideoTitle(targetVideo, index);

    try {
      setDeletingVideoId(targetVideo.video_id);
      await deleteVideo(targetVideo.video_id);
      setVideos((currentVideos) =>
        currentVideos.filter((video) => video.video_id !== targetVideo.video_id)
      );

      if (targetVideo.video_id === videoId) {
        selectedVideoIdRef.current = "";
        pendingSeek.current = null;
        localStorage.removeItem(SAVED_VIDEO_ID_KEY);
        setViewMode("library");
        setVideoId("");
        setStatus(null);
        setMetrics(null);
        setTranscript("");
        setSegments([]);
        clearVideoSearch();
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
    resetMoment();
    if (!videoRef.current) return;
    videoRef.current.currentTime = seconds;
    void videoRef.current.play().catch(() => { /* Playback may require another user gesture. */ });
  }

  // Plays one matched range, then pauses at its end unless the viewer takes over.
  function playMoment(index: number) {
    const moment = moments[index];
    if (!moment) return;
    setSelectedMoment(index);
    setIsMomentFinished(false);
    momentStopTime.current = moment.end > moment.start ? moment.end : null;
    if (!videoRef.current) return;
    videoRef.current.currentTime = moment.start;
    void videoRef.current.play().catch(() => { /* Playback may require another user gesture. */ });
  }

  function cancelMomentStop() {
    momentStopTime.current = null;
    setIsMomentFinished(false);
  }

  function handleTimeUpdate(time: number) {
    if (momentStopTime.current !== null && time >= momentStopTime.current) {
      momentStopTime.current = null;
      videoRef.current?.pause();
      setIsMomentFinished(true);
    }
    let nextStart: number | null = null;
    for (const segment of segments) {
      if (segment.start <= time) nextStart = segment.start;
    }
    setActiveSegmentStart(nextStart);
  }

  function renderLibrary() {
    const showSkeleton = isLoadingVideos || isAuthLoading || !videosLoaded;
    const hideGrid = showLibrarySearch && libraryResultsActive;

    return (
      <div className="mx-auto grid max-w-[1400px] gap-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl leading-tight font-semibold tracking-tight md:text-2xl">
              {libraryFeedActive ? "My videos" : "Public videos"}
            </h1>
            <p className="text-xs text-muted-foreground">
              {libraryFeedActive
                ? `${videos.length} ${videos.length === 1 ? "video" : "videos"} in your library`
                : "Videos that people have shared publicly"}
            </p>
          </div>
          <Button disabled={isLoadingVideos} variant="outline" onClick={() => void refreshVideos(true)}>
            <RefreshCw className={cn(isLoadingVideos && "animate-spin")} aria-hidden="true" />
            Refresh
          </Button>
        </div>

        {showLibrarySearch && (
          <LibrarySearch
            key={currentUser?.user_id || "local"}
            onActiveChange={setLibraryResultsActive}
            onOpen={(hit) => void loadVideoById(hit.video_id, "watch", hit.start_seconds)}
          />
        )}

        {!hideGrid && (showSkeleton ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,250px),1fr))] gap-4" aria-busy="true" aria-label="Loading videos">
            {[0, 1, 2, 3].map((item) => (
              <div className="overflow-hidden rounded-[10px] border bg-card" key={item}>
                <Skeleton className="aspect-video rounded-none" />
                <div className="grid gap-2 p-3">
                  <Skeleton className="h-3 w-4/5" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              </div>
            ))}
          </div>
        ) : videos.length === 0 ? (
          <div className="grid justify-items-center gap-2 rounded-[10px] border border-dashed border-input px-5 py-12 text-center">
            <Video className="size-7 text-muted-foreground" aria-hidden="true" />
            <h3 className="text-[15px] font-semibold">
              {!libraryFeedActive ? "No public videos yet" : isSignedIn ? "No videos yet" : "Sign in to see your videos"}
            </h3>
            <p className="max-w-[42ch] text-muted-foreground">
              {!libraryFeedActive
                ? "Public uploads will appear here when someone marks a video public."
                : isSignedIn
                  ? "Upload a video and Vindex will create a searchable transcript."
                  : "You can still watch public videos in Public explore."}
            </p>
            {libraryFeedActive && isSignedIn && (
              <Button className="mt-1" onClick={() => setIsUploadOpen(true)}>
                <UploadCloud aria-hidden="true" />
                Upload video
              </Button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,250px),1fr))] gap-4">
            {videos.map((video, index) => (
              <VideoCard
                canDelete={Boolean(currentUser?.user_id && video.owner_user_id === currentUser.user_id)}
                index={index}
                isDeleting={deletingVideoId === video.video_id}
                isSelected={video.video_id === videoId}
                key={video.video_id}
                video={video}
                onDelete={() => setDeleteTarget({ video, index })}
                onOpen={() => void loadVideoById(video.video_id)}
              />
            ))}
          </div>
        ))}
      </div>
    );
  }

  function renderWatchView() {
    return (
      <div className="mx-auto grid max-w-[1400px] gap-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-[1_1_300px] items-center gap-2">
            <Button aria-label="Library" size="icon" title="Back to library" variant="outline" onClick={() => setViewMode("library")}>
              <ArrowLeft aria-hidden="true" />
            </Button>
            <div className="min-w-0 flex-1">
              <h1 className="text-xl leading-tight font-semibold tracking-tight [overflow-wrap:anywhere] md:text-2xl">{selectedTitle}</h1>
              {status && (
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  {!processed && <StatusBadge value={status.status} />}
                  <span>{formatDate(status.created_at)}</span>
                  <VisibilityIcon visibility={status.visibility} />
                </div>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={!status} variant="outline" onClick={() => setIsDetailsOpen(true)}>
              <PanelRight aria-hidden="true" />
              Details
            </Button>
            {status && selectedVideoIsOwned && (
              <Button
                disabled={deletingVideoId === status.video_id}
                variant="outline"
                onClick={() => setDeleteTarget({ video: status, index: selectedVideoIndex >= 0 ? selectedVideoIndex : 0 })}
              >
                {deletingVideoId === status.video_id ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}
                Delete
              </Button>
            )}
          </div>
        </div>

        <div className="grid items-start gap-7 lg:grid-cols-[minmax(0,1fr)_minmax(320px,420px)]">
          <div className="grid min-w-0 gap-3">
            {playerUrl ? (
              <VideoPlayer
                key={playerUrl}
                marks={moments}
                selectedRange={activeMoment}
                src={playerUrl}
                videoRef={videoRef}
                onLoadedMetadata={() => {
                  const target = pendingSeek.current;
                  if (target?.videoId === videoId && videoRef.current) {
                    videoRef.current.currentTime = target.seconds;
                    pendingSeek.current = null;
                  }
                }}
                onMarkSelect={playMoment}
                onTimeUpdate={handleTimeUpdate}
                onUserControl={cancelMomentStop}
              />
            ) : (
              <section className="overflow-hidden rounded-[10px] bg-[#0b1215] text-white" aria-label="Video player">
                <div className="grid aspect-video place-items-center p-4 text-center" style={{ background: videoGradient(videoId) }}>
                  <div className="grid justify-items-center gap-2">
                    {failed ? <CircleAlert className="size-9" aria-hidden="true" /> : status && !playable ? <LoaderCircle className="size-9 animate-spin" aria-hidden="true" /> : <Play className="size-9" aria-hidden="true" />}
                    <span>{status ? `Current status: ${readableStatus(status.status)}` : "No video loaded"}</span>
                  </div>
                </div>
              </section>
            )}

            {searched && moments.length > 0 && (
              <MomentsRow
                isFinished={isMomentFinished}
                moments={moments}
                pattern={highlightPattern(searched.query, searched.algorithm)}
                query={searched.query}
                selectedIndex={selectedMoment}
                videoId={videoId}
                onKeepWatching={() => {
                  cancelMomentStop();
                  void videoRef.current?.play().catch(() => { /* Playback may require another user gesture. */ });
                }}
                onSelect={playMoment}
              />
            )}

            {status && !searchable && (
              <div className="flex flex-wrap gap-1.5">
                {pipeline.map((item) => <StatusBadge key={item.label} label={item.label} value={item.value} />)}
              </div>
            )}
            {status?.error_message && (
              <p className="flex gap-2.5 rounded-lg bg-bad px-3 py-2.5 text-bad-foreground">
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                {status.error_message}
              </p>
            )}
          </div>

          <TranscriptPanel
            activeSegmentStart={activeSegmentStart}
            algorithm={algorithm}
            isFailed={failed}
            isSearching={isSearching}
            keywordSearchEnabled={Boolean(status?.keyword_search_enabled)}
            query={query}
            results={results}
            searchable={searchable}
            searched={searched}
            segments={segments}
            selectedMomentStart={activeMoment?.start ?? null}
            transcript={transcript}
            onAlgorithmChange={setAlgorithm}
            onClear={clearVideoSearch}
            onJump={jumpTo}
            onQueryChange={setQuery}
            onSearch={() => void handleSearch()}
            onSelectMoment={(start) => {
              const index = moments.findIndex((moment) => Math.abs(moment.start - start) < 0.01);
              if (index >= 0) playMoment(index);
            }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="grid min-h-screen grid-cols-[minmax(0,1fr)] md:grid-cols-[252px_minmax(0,1fr)]">
      <AppSidebar
        activeFeed={viewMode === "library" ? feedMode : null}
        currentUser={currentUser}
        hasSession={Boolean(authSession)}
        isAuthLoading={isAuthLoading}
        isSignedIn={isSignedIn}
        onSelectFeed={(feed) => {
          if (feed !== feedMode) {
            videoListRequest.current += 1;
            setVideos([]);
            setVideosLoaded(false);
            setIsLoadingVideos(false);
          }
          setFeedMode(feed);
          setViewMode("library");
        }}
        onUpload={() => {
          setMessage("");
          setIsUploadOpen(true);
        }}
      />

      <main className="min-w-0 px-4 pt-4 pb-10 md:px-6 md:pt-6 md:pb-12">
        {message && !isUploadOpen && (
          <p className="mx-auto mb-4 max-w-[1400px] rounded-lg border bg-card px-3 py-2.5 [overflow-wrap:anywhere]" role="status">{message}</p>
        )}
        {/* Kept mounted so library search results are still there when coming back from a video. */}
        <div hidden={viewMode !== "library"}>{renderLibrary()}</div>
        {viewMode === "watch" && renderWatchView()}
      </main>

      <UploadDialog
        file={selectedFile}
        isUploading={isUploading}
        message={message}
        open={isUploadOpen}
        title={videoTitle}
        visibility={videoVisibility}
        onFileChange={setSelectedFile}
        onOpenChange={setIsUploadOpen}
        onTitleChange={setVideoTitle}
        onUpload={() => void handleUpload()}
        onVisibilityChange={setVideoVisibility}
      />
      <DetailsSheet
        errorMessage={status?.error_message}
        isRefreshing={isRefreshing}
        metrics={metrics}
        open={isDetailsOpen && viewMode === "watch"}
        pipeline={pipeline}
        onOpenChange={setIsDetailsOpen}
        onRefresh={() => void refreshStatus(videoId, true)}
      />
      <DeleteVideoDialog
        title={deleteTarget ? displayVideoTitle(deleteTarget.video, deleteTarget.index) : null}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) void handleDeleteVideo(deleteTarget.video, deleteTarget.index);
          setDeleteTarget(null);
        }}
      />
    </div>
  );
}
