import { useEffect, useMemo, useRef, useState } from "react";
import { Download, Play, Pause } from "lucide-react";
import { transcriptFileUrl } from "../../features/mediaProcessing";

/**
 * Segment shape as written by the transcription worker
 * (see media_processing/transcription_worker.py).
 */
interface TranscriptSegment {
  id: number;
  start: number; // seconds
  end: number;   // seconds
  text: string;
  words?: { start: number; end: number; text: string }[];
}

interface TranscriptJson {
  segments: TranscriptSegment[];
  language?: string | null;
  duration_seconds?: number;
}

interface TranscriptPlayerProps {
  /** URL of the video file to play. */
  videoUrl: string;
  /** Transcription id (used to fetch the JSON transcript). */
  transcriptionId: string;
  /** Optional label shown above the transcript list. */
  transcriptLabel?: string;
}

function formatTimestamp(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${r.toString().padStart(2, "0")}`;
}

/**
 * Synchronised video + transcript player.
 *
 * - Video plays on the left, transcript on the right.
 * - The active segment (matching the current playback time) is
 *   highlighted and auto-scrolled into view.
 * - Clicking a segment seeks the video to that segment's start.
 * - Play/pause toggle in the transcript header for convenience.
 */
export function TranscriptPlayer({
  videoUrl,
  transcriptionId,
  transcriptLabel,
}: TranscriptPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const segmentRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [transcript, setTranscript] = useState<TranscriptJson | null>(null);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [transcriptLoading, setTranscriptLoading] = useState(true);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  // Suppress auto-scroll right after a user click until playback moves
  // past the seek target — otherwise the list fights the user.
  const suppressScrollUntil = useRef(0);

  // Fetch the transcript JSON.
  useEffect(() => {
    let cancelled = false;
    setTranscriptLoading(true);
    setTranscriptError(null);
    fetch(transcriptFileUrl(transcriptionId, "json"))
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();
        if (cancelled) return;
        try {
          const json = JSON.parse(text) as TranscriptJson;
          setTranscript(json);
        } catch {
          setTranscriptError("Transkript konnte nicht gelesen werden.");
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setTranscriptError(err instanceof Error ? err.message : "Transkript konnte nicht geladen werden.");
        }
      })
      .finally(() => {
        if (!cancelled) setTranscriptLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [transcriptionId]);

  const segments = useMemo(() => transcript?.segments ?? [], [transcript]);

  // Find the active segment index for the current time.
  const activeIndex = useMemo(() => {
    if (segments.length === 0) return -1;
    // Binary search would be overkill for typical segment counts; a
    // linear scan is fine and tolerant of unsorted edge cases.
    for (let i = 0; i < segments.length; i++) {
      const s = segments[i];
      if (currentTime >= s.start && currentTime < s.end) return i;
    }
    // If we're past the last segment's end, highlight the last one.
    if (currentTime > 0 && segments.length > 0) {
      const last = segments[segments.length - 1];
      if (currentTime >= last.end) return segments.length - 1;
    }
    return -1;
  }, [segments, currentTime]);

  // Auto-scroll the active segment into view.
  useEffect(() => {
    if (activeIndex < 0) return;
    if (Date.now() < suppressScrollUntil.current) return;
    const el = segmentRefs.current[activeIndex];
    if (el && listRef.current) {
      const list = listRef.current;
      const elTop = el.offsetTop;
      const elHeight = el.offsetHeight;
      const viewHeight = list.clientHeight;
      const target = elTop - viewHeight / 2 + elHeight / 2;
      if (Math.abs(list.scrollTop - target) > 8) {
        list.scrollTo({ top: target, behavior: "smooth" });
      }
    }
  }, [activeIndex]);

  const handleSegmentClick = (seg: TranscriptSegment) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = seg.start;
    setCurrentTime(seg.start);
    suppressScrollUntil.current = Date.now() + 400;
    void video.play();
  };

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) void video.play();
    else video.pause();
  };

  return (
    <div className="transcript-player">
      <div className="transcript-player__video-wrap">
        <video
          ref={videoRef}
          src={videoUrl}
          controls
          preload="metadata"
          className="transcript-player__video"
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onLoadedMetadata={(e) => setCurrentTime(e.currentTarget.currentTime)}
        />
      </div>

      <div className="transcript-player__transcript">
        <div className="transcript-player__transcript-header">
          <button
            type="button"
            className="transcript-player__play-toggle"
            onClick={togglePlay}
            aria-label={isPlaying ? "Pause" : "Abspielen"}
          >
            {isPlaying ? <Pause size={14} /> : <Play size={14} />}
          </button>
          <span className="transcript-player__transcript-title">
            {transcriptLabel ?? "Transkript"}
            {transcript?.language ? ` · ${transcript.language}` : ""}
          </span>
          <a
            href={transcriptFileUrl(transcriptionId, "json")}
            className="transcript-player__download"
            download
            aria-label="Transkript als JSON herunterladen"
            title="JSON herunterladen"
          >
            <Download size={14} />
          </a>
        </div>

        <div className="transcript-player__time-row">
          <span className="transcript-player__time">{formatTimestamp(currentTime)}</span>
          {transcript?.duration_seconds != null && (
            <span className="transcript-player__time transcript-player__time--muted">
              / {formatTimestamp(transcript.duration_seconds)}
            </span>
          )}
        </div>

        <div ref={listRef} className="transcript-player__segments">
          {transcriptLoading && <div className="transcript-player__empty">Lade Transkript …</div>}
          {transcriptError && <div className="transcript-player__empty transcript-player__empty--error">{transcriptError}</div>}
          {!transcriptLoading && !transcriptError && segments.length === 0 && (
            <div className="transcript-player__empty">Keine Segmente im Transkript.</div>
          )}
          {segments.map((seg, i) => {
            const active = i === activeIndex;
            return (
              <button
                key={seg.id}
                ref={(el) => { segmentRefs.current[i] = el; }}
                type="button"
                className={`transcript-player__segment${active ? " is-active" : ""}`}
                onClick={() => handleSegmentClick(seg)}
              >
                <span className="transcript-player__segment-time">{formatTimestamp(seg.start)}</span>
                <span className="transcript-player__segment-text">{seg.text}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
