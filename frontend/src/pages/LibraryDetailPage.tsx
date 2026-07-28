import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, Music, FileVideo, Film } from "lucide-react";
import { Badge, type BadgeVariant } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { ApiError } from "../api/client";
import { formatBytes, formatDateTime, formatDuration } from "../utils/format";
import { libraryItemFileUrl } from "../features/library/api";
import { useLibraryItemsQuery } from "../features/library/hooks";
import type { LibraryItem as LibraryItemRecord } from "../features/library/schemas";
import {
  useSourceAudioArtifactQuery,
  useStartSourceAudioExtractionMutation,
  useSourceTranscriptionsQuery,
  transcriptFileUrl,
  sourceAudioFileUrl,
  TranscriptPlayer,
} from "../features/mediaProcessing";

function transcriptionStatusBadge(status?: string): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "READY":
      return { variant: "success", label: "Fertig" };
    case "RUNNING":
      return { variant: "info", label: "Läuft" };
    case "QUEUED":
      return { variant: "info", label: "Wartet" };
    case "FAILED":
      return { variant: "error", label: "Fehlgeschlagen" };
    case "CANCELED":
      return { variant: "muted", label: "Abgebrochen" };
    default:
      return { variant: "muted", label: status ?? "Unbekannt" };
  }
}

/**
 * Library item detail page.
 *
 * Shows a single library item (VOD or upload) with a synchronised
 * video + transcript player, audio artifact metadata and a list of
 * all transcriptions.
 */
export function LibraryDetailPage() {
  const { itemId } = useParams<{ itemId: string }>();
  // Load all items and pick the one we need — the library API has no
  // per-item query hook yet, but the list is small and cached by
  // react-query.
  const libraryQuery = useLibraryItemsQuery();
  const item: LibraryItemRecord | undefined = libraryQuery.data?.items.find((it) => it.id === itemId);

  const sourceType = item ? "file_upload" : null;
  const sourceId = item?.id ?? null;

  const audioQuery = useSourceAudioArtifactQuery(sourceType, sourceId);
  const transcriptionsQuery = useSourceTranscriptionsQuery(sourceType, sourceId, { refetchInterval: 3_000 });
  const startAudioMutation = useStartSourceAudioExtractionMutation();

  const audio = audioQuery.data;
  const transcriptions = transcriptionsQuery.data?.transcriptions ?? [];

  // READY transcriptions are eligible for the player.
  const readyTranscriptions = useMemo(
    () => transcriptions.filter((t) => t.status === "READY" && t.files),
    [transcriptions],
  );

  // Default to the newest READY transcription. Let the user switch.
  const [selectedTranscriptionId, setSelectedTranscriptionId] = useState<string | null>(null);
  const activeTranscriptionId = selectedTranscriptionId ?? readyTranscriptions[0]?.id ?? null;
  const activeTranscription = readyTranscriptions.find((t) => t.id === activeTranscriptionId) ?? null;

  if (libraryQuery.isLoading) return <LoadingState message="Lade Bibliothekseintrag…" />;
  if (libraryQuery.error) {
    return (
      <div className="page">
        <ErrorState
          message={libraryQuery.error instanceof ApiError ? libraryQuery.error.message : "Bibliothek konnte nicht geladen werden."}
        />
        <Link to="/library" className="back-link">
          <ArrowLeft size={14} /> Zurück zur Bibliothek
        </Link>
      </div>
    );
  }
  if (!item) {
    return (
      <div className="page">
        <ErrorState message="Bibliothekseintrag nicht gefunden." />
        <Link to="/library" className="back-link">
          <ArrowLeft size={14} /> Zurück zur Bibliothek
        </Link>
      </div>
    );
  }

  const KindIcon = item.source === "vod" ? Film : FileVideo;
  const videoUrl = libraryItemFileUrl(item.id);

  return (
    <div className="page">
      <div className="vod-detail__header">
        <Link to="/library" className="back-link">
          <ArrowLeft size={14} /> Zurück zur Bibliothek
        </Link>
        <Badge variant="success">Bereit</Badge>
      </div>

      <section className="page__section">
        <h2 className="page__section-title">{item.title}</h2>
        <Card className="vod-detail-card">
          <div className="vod-detail-card__rows">
            <div className="vod-detail-card__row">
              <span>Typ</span>
              <span><KindIcon size={12} /> {item.source === "vod" ? "VOD" : "Upload"}</span>
            </div>
            {item.twitch_video_id && (
              <div className="vod-detail-card__row">
                <span>Twitch Video ID</span>
                <span>#{item.twitch_video_id}</span>
              </div>
            )}
            <div className="vod-detail-card__row">
              <span>Datei</span>
              <span>{item.file_name}</span>
            </div>
            {item.duration_seconds != null && (
              <div className="vod-detail-card__row">
                <span>Dauer</span>
                <span>{formatDuration(item.duration_seconds)}</span>
              </div>
            )}
            {item.file_size_bytes != null && (
              <div className="vod-detail-card__row">
                <span>Größe</span>
                <span>{formatBytes(item.file_size_bytes)}</span>
              </div>
            )}
            <div className="vod-detail-card__row">
              <span>Erstellt</span>
              <span>{formatDateTime(item.created_at)}</span>
            </div>
          </div>
          <a href={videoUrl} download className="vod-detail-card__download">
            <Download size={14} />
            Video herunterladen
          </a>
        </Card>
      </section>

      {/* Synchronised video + transcript player. */}
      {activeTranscription && (
        <section className="page__section">
          <div className="transcript-player__section-header">
            <h2 className="page__section-title">Player</h2>
            {readyTranscriptions.length > 1 && (
              <label className="transcript-player__select-wrap">
                <span className="transcript-player__select-label">Transkript:</span>
                <select
                  value={activeTranscriptionId ?? ""}
                  onChange={(e) => setSelectedTranscriptionId(e.target.value || null)}
                  className="transcript-player__select"
                >
                  {readyTranscriptions.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.model} · {t.language ?? "auto"} · {formatDateTime(t.created_at)}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <TranscriptPlayer
            videoUrl={videoUrl}
            transcriptionId={activeTranscription.id}
            transcriptLabel={`${activeTranscription.model} · ${activeTranscription.language ?? "auto"}`}
          />
        </section>
      )}

      <section className="page__section">
        <h2 className="page__section-title">Audio-Artefakt</h2>
        {audioQuery.isLoading && <LoadingState message="Lade Audio-Status…" />}
        {audioQuery.error && !audio && (
          <Card className="vod-detail-card">
            <EmptyState
              title="Kein Audio-Artefakt"
              description="Es wurde noch kein Audio-Artefakt extrahiert."
            />
            <Button
              variant="primary"
              size="sm"
              onClick={() => startAudioMutation.mutate({ sourceType: "file_upload", sourceId: item.id })}
              disabled={startAudioMutation.isPending}
            >
              <Music size={14} />
              Audio extrahieren
            </Button>
          </Card>
        )}
        {audio && (
          <Card className="vod-detail-card">
            <div className="vod-detail-card__rows">
              <div className="vod-detail-card__row">
                <span>Container</span>
                <span>{audio.container}</span>
              </div>
              <div className="vod-detail-card__row">
                <span>Sample Rate</span>
                <span>{audio.sample_rate} Hz</span>
              </div>
              <div className="vod-detail-card__row">
                <span>Channels</span>
                <span>{audio.channels === 1 ? "Mono" : `${audio.channels}`}</span>
              </div>
              <div className="vod-detail-card__row">
                <span>Dauer</span>
                <span>{formatDuration(audio.duration_seconds)}</span>
              </div>
              <div className="vod-detail-card__row">
                <span>Größe</span>
                <span>{formatBytes(audio.file_size_bytes)}</span>
              </div>
              <div className="vod-detail-card__row">
                <span>SHA-256</span>
                <span className="vod-detail-card__sha">{audio.sha256.slice(0, 16)}…</span>
              </div>
            </div>
            <a href={sourceAudioFileUrl("file_upload", item.id)} download className="vod-detail-card__download">
              <Download size={14} />
              Audio herunterladen (FLAC)
            </a>
          </Card>
        )}
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Transkriptionen</h2>
        {transcriptionsQuery.isLoading && <LoadingState message="Lade Transkriptionen…" />}
        {transcriptionsQuery.error && (
          <ErrorState
            message={transcriptionsQuery.error instanceof ApiError ? transcriptionsQuery.error.message : "Transkriptionen konnten nicht geladen werden."}
          />
        )}
        {transcriptionsQuery.data && transcriptions.length === 0 && (
          <EmptyState
            title="Keine Transkriptionen"
            description="Starte eine Transkription auf der Transkription-Seite (Tab „Aus Bibliothek“)."
          />
        )}
        {transcriptions.length > 0 && (
          <div className="transcription-list">
            {transcriptions.map((t) => {
              const sb = transcriptionStatusBadge(t.status);
              const isActive = t.id === activeTranscriptionId;
              return (
                <Card key={t.id} className="transcription-card">
                  <div className="transcription-card__header">
                    <div className="transcription-card__title">
                      <Badge variant={sb.variant}>{sb.label}</Badge>
                      <span className="transcription-card__vod-title">
                        {t.model} · {t.language ?? "auto"}
                      </span>
                    </div>
                    {t.status === "READY" && t.files && readyTranscriptions.length > 1 && (
                      <Button
                        variant={isActive ? "primary" : "secondary"}
                        size="sm"
                        onClick={() => setSelectedTranscriptionId(t.id)}
                      >
                        {isActive ? "Im Player" : "Im Player anzeigen"}
                      </Button>
                    )}
                  </div>
                  <div className="transcription-card__footer">
                    <span className="transcription-card__meta">
                      Dauer: {formatDuration(t.duration_seconds)}
                    </span>
                    <span className="transcription-card__meta">
                      Erstellt: {formatDateTime(t.created_at)}
                    </span>
                  </div>
                  {t.status === "READY" && t.files && (
                    <div className="transcription-card__downloads">
                      <span className="transcription-card__downloads-label">Downloads:</span>
                      {(["txt", "srt", "vtt", "json"] as const).map((ext) => (
                        <a
                          key={ext}
                          href={transcriptFileUrl(t.id, ext)}
                          className="transcription-card__download-link"
                          download
                        >
                          <Download size={12} />
                          {ext.toUpperCase()}
                        </a>
                      ))}
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
