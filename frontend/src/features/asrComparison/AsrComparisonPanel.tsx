import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Play, Trash2, X } from "lucide-react";
import { Badge, type BadgeVariant } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { ApiError } from "../../api/client";
import { formatBytes, formatDuration } from "../../utils/format";
import { libraryItemFileUrl } from "../library/api";
import { useLibraryItemsQuery } from "../library/hooks";
import {
  useAsrPresetsQuery,
  useAsrStatusQuery,
  useCreateAsrBenchmarkMutation,
  useAsrBenchmarkQuery,
  useStartAsrBenchmarkMutation,
  useCancelAsrBenchmarkMutation,
  useDeleteAsrBenchmarkMutation,
  useSelectDefaultFromBenchmarkMutation,
  useAsrRunQuery,
} from "./hooks";
import type { AsrBenchmark, AsrRunDetail, AsrRunDetail as AsrRunDetailType } from "./types";

const ALL_PRESET_IDS = [
  "legacy-current",
  "multilingual-large-v3-quality",
  "multilingual-large-v3-no-vad",
  "multilingual-large-v3-turbo",
] as const;

const PRESET_LABELS: Record<string, string> = {
  "legacy-current": "Aktuelle Konfiguration",
  "multilingual-large-v3-quality": "Large v3 Multilingual",
  "multilingual-large-v3-no-vad": "Large v3 Multilingual ohne VAD – Diagnose",
  "multilingual-large-v3-turbo": "Large v3 Turbo Multilingual",
};

function benchmarkStatusBadge(status: string): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "READY":
      return { variant: "success", label: "Fertig" };
    case "RUNNING":
      return { variant: "info", label: "Läuft" };
    case "QUEUED":
      return { variant: "info", label: "Warteschlange" };
    case "PARTIALLY_FAILED":
      return { variant: "warning", label: "Teilweise fehlgeschlagen" };
    case "FAILED":
      return { variant: "error", label: "Fehlgeschlagen" };
    case "CANCELED":
      return { variant: "muted", label: "Abgebrochen" };
    default:
      return { variant: "muted", label: status };
  }
}

function isActiveBenchmark(status: string): boolean {
  return status === "RUNNING" || status === "QUEUED";
}

function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return Number(n).toFixed(digits);
}

function fmtPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(Number(n) * 100).toFixed(1)}%`;
}

interface RunCardProps {
  run: AsrBenchmark["runs"][number];
  benchmarkId: string;
  defaultPresetId: string;
  onSeek: (seconds: number) => void;
}

function RunCard({ run, benchmarkId, defaultPresetId, onSeek }: RunCardProps) {
  const [open, setOpen] = useState(false);
  const runQuery = useAsrRunQuery(open ? benchmarkId : null, open ? run.preset_id : null);
  const selectMutation = useSelectDefaultFromBenchmarkMutation();
  const [confirmDefault, setConfirmDefault] = useState(false);

  const isDefault = run.preset_id === defaultPresetId;
  const isEligible = run.preset_id !== "multilingual-large-v3-no-vad";
  const status = benchmarkStatusBadge(run.status);

  return (
    <Card className="asr-run-card">
      <div className="asr-run-card__header">
        <div className="asr-run-card__title">
          <Badge variant={status.variant}>{status.label}</Badge>
          <strong>{PRESET_LABELS[run.preset_id] ?? run.preset_id}</strong>
          {isDefault && <Badge variant="success">Standard</Badge>}
        </div>
        <Button variant="ghost" size="sm" onClick={() => setOpen((o) => !o)}>
          {open ? "Schließen" : "Details"}
        </Button>
      </div>

      <div className="asr-run-card__meta">
        <span>Modell: {run.model ?? "—"}</span>
        <span>Laufzeit: {run.runtime_seconds != null ? `${fmtNum(run.runtime_seconds, 1)}s` : "—"}</span>
        <span>Ladezeit: {run.model_load_seconds != null ? `${fmtNum(run.model_load_seconds, 1)}s` : "—"}</span>
        <span>Peak-VRAM: {run.peak_vram_mb != null ? `${fmtNum(run.peak_vram_mb, 0)} MB` : "—"}</span>
        <span>Sprache: {run.detected_language ?? "—"}</span>
        {run.language_probability != null && <span> ({fmtPct(run.language_probability)})</span>}
      </div>

      <div className="asr-run-card__metrics">
        <span>WER: {run.metrics_available ? fmtPct(run.wer) : "—"}</span>
        <span>CER: {run.metrics_available ? fmtPct(run.cer) : "—"}</span>
        <span>Sub: {run.substitutions ?? "—"}</span>
        <span>Del: {run.deletions ?? "—"}</span>
        <span>Ins: {run.insertions ?? "—"}</span>
      </div>

      {run.transcript_text && (
        <div className="asr-run-card__transcript">
          <span className="asr-run-card__transcript-label">Transkript:</span>
          <span>{run.transcript_text}</span>
        </div>
      )}

      {run.error && (
        <div className="asr-run-card__error">
          <AlertCircle size={14} />
          {run.error}
        </div>
      )}

      {run.hallucination_flag_count != null && run.hallucination_flag_count > 0 && (
        <div className="asr-run-card__flags">
          <AlertCircle size={14} />
          {run.hallucination_flag_count} Halluzinationswarnung(en)
        </div>
      )}
      {run.missing_speech_flag_count != null && run.missing_speech_flag_count > 0 && (
        <div className="asr-run-card__flags">
          <AlertCircle size={14} />
          {run.missing_speech_flag_count} mögliche Auslassung(en)
        </div>
      )}

      {open && runQuery.isLoading && <LoadingState message="Lade Run-Details …" />}
      {open && runQuery.error && (
        <ErrorState
          message={runQuery.error instanceof ApiError ? runQuery.error.message : "Run konnte nicht geladen werden."}
        />
      )}
      {open && runQuery.data && (
        <RunDetail
          run={runQuery.data}
          onSeek={onSeek}
          isEligible={isEligible}
          isDefault={isDefault}
          onSelectDefault={() => setConfirmDefault(true)}
        />
      )}

      <ConfirmDialog
        open={confirmDefault}
        onOpenChange={(o) => {
          if (!o) setConfirmDefault(false);
        }}
        title="Als Standard verwenden"
        description="Dies betrifft nur neue Transkriptionen. Bestehende Transkripte werden nicht geändert."
        confirmLabel="Als Standard setzen"
        cancelLabel="Abbrechen"
        destructive={false}
        busy={selectMutation.isPending}
        onConfirm={() => {
          selectMutation.mutate(
            { benchmarkId, presetId: run.preset_id },
            { onSuccess: () => setConfirmDefault(false) },
          );
        }}
        onCancel={() => setConfirmDefault(false)}
      />
    </Card>
  );
}

function RunDetail({
  run,
  onSeek,
  isEligible,
  isDefault,
  onSelectDefault,
}: {
  run: AsrRunDetail;
  onSeek: (seconds: number) => void;
  isEligible: boolean;
  isDefault: boolean;
  onSelectDefault: () => void;
}) {
  const segments = run.segments ?? [];
  const vad = run.vad_diagnosis;
  const metrics = run.metrics;
  const hflags = run.hallucination_flags ?? [];
  const mflags = run.missing_speech_flags ?? [];
  const totalDur = vad?.audio_duration_seconds ?? run.audio_duration_seconds ?? 0;

  return (
    <div className="asr-run-detail">
      <div className="asr-run-detail__params">
        <span>Compute Type: {run.preset?.compute_type ?? "—"}</span>
        <span>Multilingual: {run.preset?.multilingual ? "an" : "aus"}</span>
        <span>VAD: {run.preset?.vad_filter ? "an" : "aus"}</span>
        <span>Spracheinstellung: {run.preset?.language ?? "auto"}</span>
        {run.faster_whisper_version && <span>faster-whisper: {run.faster_whisper_version}</span>}
      </div>

      {vad && (
        <div className="asr-run-detail__vad">
          <h4>VAD-Diagnose</h4>
          {vad.computed ? (
            <>
              <span>Audiodauer: {fmtNum(vad.audio_duration_seconds, 1)}s</span>
              <span>Sprachdauer: {fmtNum(vad.duration_after_vad_seconds, 1)}s</span>
              <span>Entfernt: {fmtNum(vad.removed_by_vad_seconds, 1)}s</span>
              <span>Speech Regions: {(vad.speech_regions ?? []).length}</span>
            </>
          ) : (
            <span>VAD nicht aktiviert für diesen Run.</span>
          )}
        </div>
      )}

      <Timeline
        totalDuration={totalDur}
        speechRegions={vad?.speech_regions ?? []}
        segments={segments}
        onSeek={onSeek}
      />

      <div className="asr-run-detail__segments">
        <h4>Segmentansicht</h4>
        {segments.length === 0 && <span>Keine Segmente.</span>}
        {segments.map((s) => (
          <button
            key={s.id}
            type="button"
            className="asr-run-detail__segment"
            onClick={() => onSeek(s.start)}
          >
            <span className="asr-run-detail__segment-time">
              {formatDuration(s.start)} – {formatDuration(s.end)}
            </span>
            <span className="asr-run-detail__segment-text">{s.text}</span>
            {s.no_speech_probability != null && (
              <span className="asr-run-detail__segment-prob" title="no_speech_probability">
                nsp:{fmtPct(s.no_speech_probability)}
              </span>
            )}
          </button>
        ))}
      </div>

      {metrics.available && metrics.word_diff && metrics.word_diff.length > 0 && (
        <div className="asr-run-detail__diff">
          <h4>Wort-Diff</h4>
          <div className="asr-diff">
            {metrics.word_diff.map((op, i) => (
              <span key={i} className={`asr-diff__op asr-diff__op--${op.type}`}>
                {op.type === "delete" && <span className="asr-diff__del">[-{(op.ref ?? []).join(" ")}-]</span>}
                {op.type === "insert" && <span className="asr-diff__ins">[+{(op.hyp ?? []).join(" ")}+]</span>}
                {op.type === "replace" && (
                  <span className="asr-diff__sub">
                    [{(op.ref ?? []).join(" ")}→{(op.hyp ?? []).join(" ")}]
                  </span>
                )}
                {op.type === "equal" && <span className="asr-diff__eq">{(op.ref ?? []).join(" ")}</span>}
              </span>
            ))}
          </div>
          <p className="asr-diff__legend">
            <span className="asr-diff__del">[-fehlend-]</span> fehlende Wörter ·{" "}
            <span className="asr-diff__ins">[+zusätzlich+]</span> eingefügte Wörter ·{" "}
            <span className="asr-diff__sub">[alt→neu]</span> falsche Wörter
          </p>
        </div>
      )}

      {(hflags.length > 0 || mflags.length > 0) && (
        <div className="asr-run-detail__flags">
          <h4>Diagnose-Hinweise</h4>
          {hflags.map((f, i) => (
            <div key={`h${i}`} className={`asr-flag asr-flag--${f.severity}`}>
              <AlertCircle size={12} />
              <strong>{f.type}</strong> {f.message}
            </div>
          ))}
          {mflags.map((f, i) => (
            <div key={`m${i}`} className={`asr-flag asr-flag--${f.severity}`}>
              <AlertCircle size={12} />
              <strong>{f.type}</strong> {f.message}
            </div>
          ))}
        </div>
      )}

      {isEligible && !isDefault && (
        <div className="asr-run-detail__select-default">
          <Button variant="secondary" size="sm" onClick={onSelectDefault}>
            <CheckCircle2 size={14} />
            Als Standard verwenden
          </Button>
        </div>
      )}
      {!isEligible && (
        <p className="asr-run-detail__muted">
          Dieses diagnostische Preset darf nicht als Produktionsstandard gewählt werden.
        </p>
      )}
    </div>
  );
}

function Timeline({
  totalDuration,
  speechRegions,
  segments,
  onSeek,
}: {
  totalDuration: number;
  speechRegions: { start: number; end: number }[];
  segments: AsrRunDetailType["segments"];
  onSeek: (seconds: number) => void;
}) {
  const dur = Math.max(totalDuration || 0, 0.1);
  const pct = (s: number) => `${Math.min(100, Math.max(0, (s / dur) * 100))}%`;
  return (
    <div className="asr-timeline" aria-label="Audio-Timeline">
      <div className="asr-timeline__track">
        {speechRegions.map((r, i) => (
          <div
            key={`vad${i}`}
            className="asr-timeline__vad"
            style={{ left: pct(r.start), width: pct(r.end - r.start) }}
            title={`VAD-Sprache ${r.start.toFixed(1)}s–${r.end.toFixed(1)}s`}
          />
        ))}
        {segments.map((s) => (
          <button
            key={s.id}
            type="button"
            className="asr-timeline__seg"
            style={{ left: pct(s.start), width: pct(Math.max(s.end - s.start, 0.1)) }}
            title={`${formatDuration(s.start)}–${formatDuration(s.end)}: ${s.text}`}
            onClick={() => onSeek(s.start)}
          />
        ))}
      </div>
      <div className="asr-timeline__legend">
        <span className="asr-timeline__legend-vad">VAD-Sprache</span>
        <span className="asr-timeline__legend-seg">Transkriptsegment</span>
        <span className="asr-timeline__legend-silence">Stille</span>
      </div>
    </div>
  );
}

export function AsrComparisonPanel() {
  const presetsQuery = useAsrPresetsQuery();
  const statusQuery = useAsrStatusQuery();
  const libraryQuery = useLibraryItemsQuery();

  const [selectedItemId, setSelectedItemId] = useState<string>("");
  const [referenceText, setReferenceText] = useState<string>("");
  const [hotwords, setHotwords] = useState<string>("");
  const [selectedPresets, setSelectedPresets] = useState<string[]>([...ALL_PRESET_IDS]);
  const [activeBenchmarkId, setActiveBenchmarkId] = useState<string | null>(null);
  const [seekSeconds, setSeekSeconds] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const playerRef = useRef<HTMLVideoElement | null>(null);

  // When a segment/timeline marker is clicked, seek the preview player.
  useEffect(() => {
    if (seekSeconds == null) return;
    const el = playerRef.current;
    if (!el) return;
    try {
      el.currentTime = seekSeconds;
      void el.play().catch(() => {
        /* autoplay may be blocked; user can press play manually */
      });
    } catch {
      /* ignore — seeking before metadata loaded throws */
    }
  }, [seekSeconds]);

  const createMutation = useCreateAsrBenchmarkMutation();
  const startMutation = useStartAsrBenchmarkMutation();
  const cancelMutation = useCancelAsrBenchmarkMutation();
  const deleteMutation = useDeleteAsrBenchmarkMutation();

  const libraryItems = useMemo(() => {
    const all = libraryQuery.data?.items ?? [];
    return all.filter((it) => it.file_exists !== false);
  }, [libraryQuery.data?.items]);

  const selectedItem = useMemo(
    () => libraryItems.find((it) => it.id === selectedItemId) ?? null,
    [libraryItems, selectedItemId],
  );

  const isActive = activeBenchmarkId
    ? isActiveBenchmark(
        (statusQuery.data?.running ? "RUNNING" : "READY"),
      )
    : false;

  const benchmarkQuery = useAsrBenchmarkQuery(activeBenchmarkId, {
    refetchInterval: activeBenchmarkId && isActive ? 3_000 : undefined,
  });

  const activeBenchmark: AsrBenchmark | null = benchmarkQuery.data ?? null;
  const isBenchmarkActive =
    activeBenchmark != null && isActiveBenchmark(activeBenchmark.status);

  const handleCreate = () => {
    if (!selectedItemId || selectedPresets.length === 0) return;
    createMutation.mutate(
      {
        source_type: "file_upload",
        source_id: selectedItemId,
        preset_ids: selectedPresets,
        reference_text: referenceText.trim() || undefined,
        hotwords: hotwords.trim() || undefined,
      },
      {
        onSuccess: (rec) => {
          setActiveBenchmarkId(rec.id);
          startMutation.mutate(rec.id);
        },
      },
    );
  };

  const handleSeek = (seconds: number) => {
    // Toggle through a fresh object so the effect re-runs even when the
    // same timestamp is clicked twice in a row.
    setSeekSeconds(seconds);
    if (seekSeconds === seconds) {
      // Force re-trigger by clearing then setting on next tick.
      setSeekSeconds(null);
      window.setTimeout(() => setSeekSeconds(seconds), 0);
    }
  };

  const togglePreset = (pid: string) => {
    setSelectedPresets((cur) =>
      cur.includes(pid) ? cur.filter((p) => p !== pid) : [...cur, pid],
    );
  };

  const startError = createMutation.error ?? startMutation.error ?? null;

  return (
    <div className="asr-comparison">
      <section className="page__section">
        <h2 className="page__section-title">ASR Vergleich – Quelle</h2>
        <Card className="asr-form-card">
          <div className="asr-form">
            <label className="asr-form__field">
              <span className="asr-form__label">Bibliothekseintrag</span>
              {libraryQuery.isLoading ? (
                <span className="asr-form__muted">Lade Bibliothek …</span>
              ) : libraryItems.length === 0 ? (
                <span className="asr-form__muted">Keine Dateien in der Bibliothek.</span>
              ) : (
                <select
                  value={selectedItemId}
                  onChange={(e) => setSelectedItemId(e.target.value)}
                  className="asr-form__select"
                  disabled={createMutation.isPending || startMutation.isPending}
                >
                  <option value="">— Eintrag wählen —</option>
                  {libraryItems.map((it) => (
                    <option key={it.id} value={it.id}>
                      {it.title || it.file_name}
                      {it.duration_seconds != null ? ` (${formatDuration(it.duration_seconds)})` : ""}
                      {it.file_size_bytes != null ? ` · ${formatBytes(it.file_size_bytes)}` : ""}
                    </option>
                  ))}
                </select>
              )}
            </label>

            <label className="asr-form__field">
              <span className="asr-form__label">Ground Truth (optional)</span>
              <textarea
                className="asr-form__textarea"
                placeholder="Was wurde tatsächlich gesagt?"
                value={referenceText}
                onChange={(e) => setReferenceText(e.target.value)}
                rows={3}
                disabled={createMutation.isPending}
              />
            </label>

            <label className="asr-form__field">
              <span className="asr-form__label">Hotwords (optional)</span>
              <input
                type="text"
                className="asr-form__input"
                placeholder="z.B. Twitch Discord Jungle Flash Gank"
                value={hotwords}
                onChange={(e) => setHotwords(e.target.value)}
                disabled={createMutation.isPending}
              />
            </label>
          </div>

          <div className="asr-form__presets">
            <span className="asr-form__label">Presets</span>
            <div className="asr-form__preset-list">
              {ALL_PRESET_IDS.map((pid) => (
                <label key={pid} className="asr-form__preset">
                  <input
                    type="checkbox"
                    checked={selectedPresets.includes(pid)}
                    onChange={() => togglePreset(pid)}
                    disabled={createMutation.isPending}
                  />
                  {PRESET_LABELS[pid] ?? pid}
                </label>
              ))}
            </div>
          </div>

          {selectedItem && (
            <div className="asr-form__preview">
              <strong>{selectedItem.title || selectedItem.file_name}</strong>
              {selectedItem.duration_seconds != null && ` · ${formatDuration(selectedItem.duration_seconds)}`}
              {selectedItem.file_size_bytes != null && ` · ${formatBytes(selectedItem.file_size_bytes)}`}
              <video
                ref={playerRef}
                src={libraryItemFileUrl(selectedItem.id)}
                controls
                preload="metadata"
                className="asr-form__player"
              />
            </div>
          )}

          {startError && (
            <ErrorState
              message={startError instanceof ApiError ? startError.message : "Benchmark konnte nicht gestartet werden."}
            />
          )}

          <div className="asr-form__actions">
            <Button
              variant="primary"
              onClick={handleCreate}
              disabled={
                !selectedItemId ||
                selectedPresets.length === 0 ||
                createMutation.isPending ||
                startMutation.isPending ||
                isBenchmarkActive
              }
              loading={createMutation.isPending || startMutation.isPending}
            >
              <Play size={14} />
              Benchmark starten
            </Button>
            {activeBenchmark && isBenchmarkActive && (
              <Button
                variant="secondary"
                onClick={() => cancelMutation.mutate(activeBenchmark.id)}
                disabled={cancelMutation.isPending}
              >
                <X size={14} />
                Abbrechen
              </Button>
            )}
            {activeBenchmark && !isBenchmarkActive && (
              <Button
                variant="danger"
                size="sm"
                onClick={() => setDeleteTarget(activeBenchmark.id)}
                disabled={deleteMutation.isPending}
              >
                <Trash2 size={14} />
                Benchmark löschen
              </Button>
            )}
          </div>
        </Card>
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Ergebnisvergleich</h2>
        {presetsQuery.isLoading && <LoadingState message="Lade Presets …" />}
        {presetsQuery.error && (
          <ErrorState
            message={presetsQuery.error instanceof ApiError ? presetsQuery.error.message : "Presets konnten nicht geladen werden."}
          />
        )}
        {!activeBenchmark && presetsQuery.data && (
          <EmptyState
            title="Kein Benchmark ausgewählt"
            description="Wähle einen Clip und starte einen Benchmark, um die Ergebnisse der vier Presets zu vergleichen."
          />
        )}
        {activeBenchmark && (
          <div className="asr-benchmark">
            <div className="asr-benchmark__header">
              <Badge variant={benchmarkStatusBadge(activeBenchmark.status).variant}>
                {benchmarkStatusBadge(activeBenchmark.status).label}
              </Badge>
              <span>Clip: {activeBenchmark.source_id}</span>
              {activeBenchmark.reference_text && (
                <span>Referenz: {activeBenchmark.reference_text}</span>
              )}
              {activeBenchmark.hotwords && <span>Hotwords: {activeBenchmark.hotwords}</span>}
            </div>
            <div className="asr-run-list">
              {activeBenchmark.runs.map((run) => (
                <RunCard
                  key={run.preset_id}
                  run={run}
                  benchmarkId={activeBenchmark.id}
                  defaultPresetId={statusQuery.data?.default_preset_id ?? "multilingual-large-v3-quality"}
                  onSeek={handleSeek}
                />
              ))}
            </div>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="Benchmark löschen"
        description="Möchtest du diesen Benchmark und alle zugehörigen Run-Daten unwiderruflich löschen?"
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        busy={deleteMutation.isPending}
        onConfirm={() => {
          if (deleteTarget) {
            deleteMutation.mutate(deleteTarget, {
              onSuccess: () => {
                setActiveBenchmarkId(null);
                setDeleteTarget(null);
              },
            });
          }
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
