import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { Maximize, Minimize, Pause, Play, Volume2, VolumeX } from "lucide-react";
import { formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const SEEK_STEP_SECONDS = 5;
const controlClass = "grid size-8 shrink-0 place-items-center rounded-md text-white hover:bg-white/15 focus-visible:ring-2 focus-visible:ring-white focus-visible:outline-none";

export function VideoPlayer({
  marks,
  onLoadedMetadata,
  onMarkSelect,
  onTimeUpdate,
  onUserControl,
  selectedRange,
  src,
  videoRef
}: {
  marks: { start: number }[];
  onLoadedMetadata: () => void;
  onMarkSelect: (index: number) => void;
  onTimeUpdate: (seconds: number) => void;
  onUserControl: () => void;
  selectedRange: { start: number; end: number } | null;
  src: string;
  videoRef: RefObject<HTMLVideoElement | null>;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    function syncFullscreen() {
      setIsFullscreen(document.fullscreenElement === containerRef.current);
    }
    document.addEventListener("fullscreenchange", syncFullscreen);
    return () => document.removeEventListener("fullscreenchange", syncFullscreen);
  }, []);

  function percent(seconds: number) {
    return duration > 0 ? Math.min(Math.max((seconds / duration) * 100, 0), 100) : 0;
  }

  function togglePlay() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      onUserControl();
      void video.play().catch(() => { /* Playback may require another user gesture. */ });
    } else {
      video.pause();
    }
  }

  function seekTo(seconds: number) {
    const video = videoRef.current;
    if (!video) return;
    onUserControl();
    video.currentTime = Math.min(Math.max(seconds, 0), duration || seconds);
    setCurrentTime(video.currentTime);
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    else void containerRef.current?.requestFullscreen().catch(() => {});
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    const onButton = target.tagName === "BUTTON";
    const onSlider = target.tagName === "INPUT";
    if (event.key === " " && !onButton) {
      event.preventDefault();
      togglePlay();
    } else if (event.key === "ArrowLeft" && !onSlider) {
      event.preventDefault();
      seekTo(currentTime - SEEK_STEP_SECONDS);
    } else if (event.key === "ArrowRight" && !onSlider) {
      event.preventDefault();
      seekTo(currentTime + SEEK_STEP_SECONDS);
    }
  }

  return (
    <div
      aria-label="Video player"
      className={cn("flex flex-col overflow-hidden bg-[#0b1215] text-white focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none", isFullscreen ? "h-full" : "rounded-[10px]")}
      ref={containerRef}
      role="group"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      <video
        className={cn("w-full bg-black", isFullscreen ? "min-h-0 flex-1" : "aspect-video")}
        ref={videoRef}
        src={src}
        onClick={togglePlay}
        onEnded={() => setIsPlaying(false)}
        onLoadedMetadata={(event) => {
          setDuration(event.currentTarget.duration);
          onLoadedMetadata();
          setCurrentTime(event.currentTarget.currentTime);
        }}
        onPause={() => setIsPlaying(false)}
        onPlay={() => setIsPlaying(true)}
        onTimeUpdate={(event) => {
          setCurrentTime(event.currentTarget.currentTime);
          onTimeUpdate(event.currentTarget.currentTime);
        }}
      />

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-2 py-1.5">
        <button aria-label={isPlaying ? "Pause" : "Play"} className={controlClass} type="button" onClick={togglePlay}>
          {isPlaying ? <Pause className="size-4" aria-hidden="true" /> : <Play className="size-4" aria-hidden="true" />}
        </button>

        <div className="relative order-last h-6 w-full min-w-0 rounded-sm has-[input:focus-visible]:ring-2 has-[input:focus-visible]:ring-white sm:order-none sm:w-auto sm:flex-1">
          <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 overflow-hidden rounded-full bg-white/25">
            {selectedRange && (
              <div
                className="absolute inset-y-0 bg-[#f2c14e]/60"
                style={{ left: `${percent(selectedRange.start)}%`, width: `${Math.max(percent(selectedRange.end) - percent(selectedRange.start), 0.5)}%` }}
              />
            )}
            <div className="absolute inset-y-0 left-0 bg-[#7fcbd5]" style={{ width: `${percent(currentTime)}%` }} />
          </div>
          <input
            aria-label="Seek"
            aria-valuetext={`${formatTime(currentTime)} of ${formatTime(duration)}`}
            className="absolute inset-0 z-10 size-full cursor-pointer opacity-0"
            max={duration || 0}
            min={0}
            step={0.1}
            type="range"
            value={Math.min(currentTime, duration || 0)}
            onChange={(event) => seekTo(Number(event.target.value))}
          />
          {duration > 0 && marks.map((mark, index) => (
            <button
              aria-label={`Moment ${index + 1} at ${formatTime(mark.start)}`}
              className="group/mark absolute top-1/2 z-20 grid h-6 w-3 -translate-x-1/2 -translate-y-1/2 place-items-center focus-visible:outline-none"
              key={`${mark.start}-${index}`}
              style={{ left: `${percent(mark.start)}%` }}
              title={formatTime(mark.start)}
              type="button"
              onClick={() => onMarkSelect(index)}
            >
              <span className="h-3 w-[3px] rounded-sm bg-[#f2c14e] group-hover/mark:h-4 group-focus-visible/mark:h-4 group-focus-visible/mark:w-[5px]" />
            </button>
          ))}
        </div>

        <span className="text-xs whitespace-nowrap tabular-nums">{formatTime(currentTime)} / {formatTime(duration)}</span>

        <div className="ml-auto flex items-center gap-1 sm:ml-0">
          <button
            aria-label={isMuted ? "Unmute" : "Mute"}
            className={controlClass}
            type="button"
            onClick={() => {
              if (!videoRef.current) return;
              videoRef.current.muted = !isMuted;
              setIsMuted(!isMuted);
            }}
          >
            {isMuted ? <VolumeX className="size-4" aria-hidden="true" /> : <Volume2 className="size-4" aria-hidden="true" />}
          </button>
          <input
            aria-label="Volume"
            className="hidden h-1 w-16 cursor-pointer accent-[#7fcbd5] md:block"
            max={1}
            min={0}
            step={0.05}
            type="range"
            value={isMuted ? 0 : volume}
            onChange={(event) => {
              if (!videoRef.current) return;
              const nextVolume = Number(event.target.value);
              videoRef.current.volume = nextVolume;
              videoRef.current.muted = nextVolume === 0;
              setVolume(nextVolume);
              setIsMuted(nextVolume === 0);
            }}
          />
          <button aria-label={isFullscreen ? "Exit fullscreen" : "Fullscreen"} className={controlClass} type="button" onClick={toggleFullscreen}>
            {isFullscreen ? <Minimize className="size-4" aria-hidden="true" /> : <Maximize className="size-4" aria-hidden="true" />}
          </button>
        </div>
      </div>
    </div>
  );
}
