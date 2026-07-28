import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, Music, AlertCircle } from "lucide-react";
import { Badge, type BadgeVariant } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { ApiError } from "../api/client";
import { formatDateTime, formatDuration, formatBytes } from "../utils/format";
import { useVodQuery, vodFileUrl } from "../features/vodPipeline";
import {
  useAudioArtifactQuery,
  useStartAudioExtractionMutation,
  useVodTranscriptionsQuery,
  useVodPipelineRunsQuery,
  transcriptFileUrl,
  audioFileUrl,
  ConversationMiningPanel,
} from "../features/mediaProcessing";
import type { KnownVodStatus } from "../features/vodPipeline/schemas";

function vodStatusBadge(status: string): { variant: BadgeVariant; label: string } {
  switch (status as KnownVodStatus) {
    case "READY":
      return { variant: "success", label: "Bereit" };
    case "DOWNLOADING":
      return { variant: "info", label: "Lädt" };
    case "VERIFYING":
      return { variant: "info", label: "Verifiziert" };
    case "QUEUED":
      return { variant: "info", label: "Wartet" };
    case "FAILED":
      return { variant: "error", label: "Fehlgeschlagen" };
    case "CANCELED":
      return { variant: "muted", label: "Abgebrochen" };
    case "DISCOVERED":
      return { variant: "muted", label: "Entdeckt" };
    default:
      return { variant: "muted", label: status };
  }
}

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
 * VOD detail page.
 *
 * Shows a single VOD with its download status, audio artifact, all
 * transcripts and pipeline runs. This is the "VOD detail page with
 * transcript and files" from the spec.
 */
export function VodDetailPage() {
  const { vodId } = useParams<{ vodId: string }>();
  const vodQuery = useVodQuery(vodId ?? null, { refetchInterval: 5_000 });
  const audioQuery = useAudioArtifactQuery(vodId ?? null);
  const transcriptionsQuery = useVodTranscriptionsQuery(vodId ?? null);
  const pipelineRunsQuery = useVodPipelineRunsQuery(vodId ?? null);
  const startAudioMutation = useStartAudioExtractionMutation();

  const vod = vodQuery.data;
  const audio = audioQuery.data;
  const transcriptions = transcriptionsQuery.data?.transcriptions ?? [];
  const pipelineRuns = pipelineRunsQuery.data?.pipeline_runs ?? [];

  const isReady = vod?.status === "READY";

  if (vodQuery.isLoading) return <LoadingState message="Lade VOD…" />;
  if (vodQuery.error || !vod) {
    return (
      <div className="page">
        <ErrorState
          message={vodQuery.error instanceof ApiError ? vodQuery.error.message : "VOD konnte nicht geladen werden."}
        />
        <Link to="/vod-pipeline" className="back-link">
          <ArrowLeft size={14} /> Zurück zur Pipeline
        </Link>
      </div>
    );
  }

  const status = vodStatusBadge(vod.status);

  return (
    <div className="page">
      <div className="vod-detail__header">
        <Link to="/vod-pipeline" className="back-link">
          <ArrowLeft size={14} /> Zurück zur Pipeline
        </Link>
        <Badge variant={status.variant}>{status.label}</Badge>
      </div>

      <section className="page__section">
        <h2 className="page__section-title">Download</h2>
        <Card className="vod-detail-card">
          <div className="vod-detail-card__rows">
            <div className="vod-detail-card__row">
              <span>Status</span>
              <Badge variant={status.variant}>{status.label}</Badge>
            </div>
            <div className="vod-detail-card__row">
              <span>Twitch Video ID</span>
              <span>{vod.twitch_video_id}</span>
            </div>
            <div className="vod-detail-card__row">
              <span>Dauer</span>
              <span>{vod.duration_seconds ? formatDuration(vod.duration_seconds) : "—"}</span>
            </div>
            {vod.download?.file_name && (
              <div className="vod-detail-card__row">
                <span>Datei</span>
                <span>{vod.download.file_name}</span>
              </div>
            )}
            {vod.download?.file_size_bytes && (
              <div className="vod-detail-card__row">
                <span>Größe</span>
                <span>{formatBytes(vod.download.file_size_bytes)}</span>
              </div>
            )}
            <div className="vod-detail-card__row">
              <span>Erstellt</span>
              <span>{formatDateTime(vod.created_at)}</span>
            </div>
          </div>
          {isReady && vod.download?.file_name && (
            <a href={vodFileUrl(vod.id)} download className="vod-detail-card__download">
              <Download size={14} />
              Video herunterladen
            </a>
          )}
          {vod.error && (
            <div className="vod-detail-card__error">
              <AlertCircle size={14} />
              {vod.error}
            </div>
          )}
        </Card>
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Audio-Artefakt</h2>
        {audioQuery.isLoading && <LoadingState message="Lade Audio-Status…" />}
        {audioQuery.error && !audio && (
          <Card className="vod-detail-card">
            <EmptyState
              title="Kein Audio-Artefakt"
              description="Es wurde noch kein Audio-Artefakt extrahiert."
            />
            {isReady && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => startAudioMutation.mutate({ vodId: vod.id })}
                disabled={startAudioMutation.isPending}
              >
                <Music size={14} />
                Audio extrahieren
              </Button>
            )}
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
            <a href={audioFileUrl(vod.id)} download className="vod-detail-card__download">
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
            description="Starte eine Transkription auf der Transkription-Seite."
          />
        )}
        {transcriptions.length > 0 && (
          <div className="transcription-list">
            {transcriptions.map((t) => {
              const sb = transcriptionStatusBadge(t.status);
              return (
                <Card key={t.id} className="transcription-card">
                  <div className="transcription-card__header">
                    <div className="transcription-card__title">
                      <Badge variant={sb.variant}>{sb.label}</Badge>
                      <span className="transcription-card__vod-title">
                        {t.model} · {t.language ?? "auto"}
                      </span>
                    </div>
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

      <section className="page__section">
        <h2 className="page__section-title">Conversation Mining</h2>
        {transcriptions.some((t) => t.status === "READY") ? (
          <ConversationMiningPanel vodId={vod.id} />
        ) : (
          <EmptyState
            title="Kein Transkript verfügbar"
            description="Conversation Mining benötigt ein fertiges Transkript. Transkribiere zuerst den VOD."
          />
        )}
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Pipeline-Runs</h2>
        {pipelineRunsQuery.isLoading && <LoadingState message="Lade Pipeline-Runs…" />}
        {pipelineRunsQuery.error && (
          <ErrorState
            message={pipelineRunsQuery.error instanceof ApiError ? pipelineRunsQuery.error.message : "Pipeline-Runs konnten nicht geladen werden."}
          />
        )}
        {pipelineRunsQuery.data && pipelineRuns.length === 0 && (
          <EmptyState
            title="Keine Pipeline-Runs"
            description="Starte eine Pipeline auf der VOD Pipeline-Seite."
          />
        )}
        {pipelineRuns.length > 0 && (
          <div className="pipeline-runs-list">
            {pipelineRuns.map((run) => (
              <Card key={run.id} className="pipeline-run-card">
                <div className="pipeline-run-card__header">
                  <div className="pipeline-run-card__title">
                    <Badge variant={run.status === "READY_FOR_CLIP_ANALYSIS" ? "success" : run.status === "FAILED" ? "error" : "info"}>
                      {run.status}
                    </Badge>
                  </div>
                </div>
                <div className="pipeline-run-card__steps">
                  {(run.steps ?? []).map((step, i) => (
                    <div key={i} className="pipeline-step">
                      <span className="pipeline-step__label">{step.type}</span>
                      <Badge variant={step.status === "READY" ? "success" : step.status === "FAILED" ? "error" : "muted"}>
                        {step.status}
                      </Badge>
                    </div>
                  ))}
                </div>
                {run.error && (
                  <div className="pipeline-run-card__error">
                    <AlertCircle size={14} />
                    {run.error}
                  </div>
                )}
                <div className="pipeline-run-card__footer">
                  <span className="pipeline-run-card__meta">
                    {formatDateTime(run.created_at)}
                  </span>
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

