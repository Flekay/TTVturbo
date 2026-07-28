import { useState } from "react";
import { AlertCircle, Loader2, Volume2 } from "lucide-react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { formatDuration } from "../../utils/format";
import {
  useAudioDiagnosticsQuery,
  useCreateAudioDiagnosticMutation,
} from "./hooks";
import { audioArtifactUrl } from "./api";
import type { AsrAudioDiagnostic, AsrAudioMetrics, AudioVariant } from "./types";

const VARIANT_LABELS: Record<AudioVariant, string> = {
  "current-asr-input": "Current ASR Input",
  "left-channel": "Linker Kanal",
  "right-channel": "Rechter Kanal",
  "mono-current": "Current Mono",
  "mono-average": "Average Mono",
};

const VARIANT_ORDER: AudioVariant[] = [
  "current-asr-input",
  "left-channel",
  "right-channel",
  "mono-current",
  "mono-average",
];

interface AudioForensicsSectionProps {
  sourceType: string;
  sourceId: string;
}

export function AudioForensicsSection({ sourceType, sourceId }: AudioForensicsSectionProps) {
  const diagnosticsQuery = useAudioDiagnosticsQuery(sourceType, sourceId);
  const createMutation = useCreateAudioDiagnosticMutation();
  const [selectedStream] = useState<number | null>(null);

  const diagnostics = diagnosticsQuery.data?.diagnostics ?? [];
  const latestDiag = diagnostics[0];

  const handleCreate = () => {
    createMutation.mutate({
      source_type: sourceType,
      source_id: sourceId,
      audio_stream_id: selectedStream,
    });
  };

  return (
    <div className="asr-forensics-section">
      <h3 className="asr-section-title">Audio prüfen</h3>
      <p className="asr-section-hint">
        Vergleiche was das ASR-Modell tatsächlich erhält gegen das Original
        und alternative Kanal-/Downmix-Varianten.
      </p>

      <div className="asr-forensics-actions">
        <Button
          onClick={handleCreate}
          disabled={createMutation.isPending}
          variant="primary"
          size="sm"
        >
          {createMutation.isPending ? (
            <>
              <Loader2 size={14} className="asr-spin" />
              Erzeuge …
            </>
          ) : (
            "Audio-Diagnose erstellen"
          )}
        </Button>
        {createMutation.error && (
          <ErrorState message={createMutation.error instanceof Error ? createMutation.error.message : "Diagnose fehlgeschlagen"} />
        )}
      </div>

      {diagnosticsQuery.isLoading && <LoadingState message="Lade Diagnosen …" />}
      {diagnosticsQuery.error && (
        <ErrorState message={diagnosticsQuery.error instanceof Error ? diagnosticsQuery.error.message : "Diagnosen konnten nicht geladen werden"} />
      )}

      {latestDiag && (
        <AudioDiagnosticView diagnostic={latestDiag} />
      )}
    </div>
  );
}

function AudioDiagnosticView({ diagnostic }: { diagnostic: AsrAudioDiagnostic }) {
  const [selectedVariant, setSelectedVariant] = useState<AudioVariant>("current-asr-input");
  const audioStreams = diagnostic.audio_streams ?? [];
  const artifacts = diagnostic.artifacts ?? {};
  const currentArtifact = artifacts[selectedVariant];

  return (
    <div className="asr-diagnostic-view">
      {audioStreams.length > 1 && (
        <Card className="asr-stream-info">
          <h4>Audiostreams ({audioStreams.length})</h4>
          <div className="asr-stream-list">
            {audioStreams.map((stream) => (
              <div key={stream.index} className="asr-stream-entry">
                <Badge variant={diagnostic.audio_stream_id === stream.index ? "success" : "muted"}>
                  Stream {stream.index}
                </Badge>
                <span>{stream.codec}</span>
                <span>{stream.channels}ch</span>
                <span>{stream.sample_rate} Hz</span>
                {stream.language && <span>Sprache: {stream.language}</span>}
                {stream.title && <span>Titel: {stream.title}</span>}
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="asr-variant-tabs">
        {VARIANT_ORDER.map((variant) => (
          <button
            key={variant}
            className={`transcription-mode-tabs__btn ${selectedVariant === variant ? "is-active" : ""}`}
            onClick={() => setSelectedVariant(variant)}
          >
            {VARIANT_LABELS[variant]}
          </button>
        ))}
      </div>

      {currentArtifact && (
        <Card className="asr-variant-card">
          <div className="asr-variant-header">
            <Volume2 size={16} />
            <strong>{VARIANT_LABELS[selectedVariant]}</strong>
          </div>
          {currentArtifact.error ? (
            <div className="asr-variant-error">
              <AlertCircle size={14} />
              {currentArtifact.error}
            </div>
          ) : (
            <>
              <audio
                controls
                preload="none"
                src={audioArtifactUrl(diagnostic.id, selectedVariant)}
                className="asr-form__player"
              />
              {currentArtifact.metrics && (
                <AudioMetricsDisplay metrics={currentArtifact.metrics} />
              )}
            </>
          )}
        </Card>
      )}
    </div>
  );
}

function AudioMetricsDisplay({ metrics }: { metrics: AsrAudioMetrics }) {
  const speechDuration = metrics.speech_duration_seconds;
  return (
    <div className="asr-metrics-grid">
      <MetricRow label="Dauer" value={metrics.duration_seconds != null ? formatDuration(metrics.duration_seconds) : "—"} />
      <MetricRow label="Kanäle" value={metrics.channels?.toString() ?? "—"} />
      <MetricRow label="Sample-Rate" value={metrics.sample_rate != null ? `${metrics.sample_rate} Hz` : "—"} />
      <MetricRow label="Codec" value={metrics.codec ?? "—"} />
      <MetricRow label="Dateigröße" value={metrics.file_size_bytes != null ? `${(metrics.file_size_bytes / 1024).toFixed(0)} KB` : "—"} />
      <MetricRow label="Peak dBFS" value={metrics.peak_dbfs?.toFixed(1) ?? "—"} />
      <MetricRow label="RMS dBFS" value={metrics.rms_dbfs?.toFixed(1) ?? "—"} />
      <MetricRow label="DC Offset" value={metrics.dc_offset?.toFixed(4) ?? "—"} />
      <MetricRow label="Clipping" value={metrics.clipping_ratio != null ? `${(metrics.clipping_ratio * 100).toFixed(2)}%` : "—"} />
      <MetricRow label="Stille" value={metrics.silence_ratio != null ? `${(metrics.silence_ratio * 100).toFixed(1)}%` : "—"} />
      <MetricRow label="Speech Duration" value={speechDuration != null ? formatDuration(speechDuration) : "—"} />
      <MetricRow label="SHA-256" value={metrics.sha256 ? metrics.sha256.substring(0, 12) + "…" : "—"} />
      {metrics.warnings && metrics.warnings.length > 0 && (
        <div className="asr-metrics-warnings">
          {metrics.warnings.map((w, i) => (
            <div key={i} className="asr-metric-warning">
              <AlertCircle size={12} />
              {w}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="asr-metric-row">
      <span className="asr-metric-label">{label}</span>
      <span className="asr-metric-value">{value}</span>
    </div>
  );
}
