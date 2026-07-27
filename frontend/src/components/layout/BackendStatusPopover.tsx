import { useState } from "react";
import { useLocation } from "react-router-dom";
import {
  CheckCircle2,
  XCircle,
  CircleDot,
  AlertTriangle,
  Loader2,
  Cpu,
  Wand2,
  Server,
} from "lucide-react";
import { Badge, type BadgeVariant } from "../ui/Badge";
import { Button } from "../ui/Button";
import { useBackendStatus } from "../../hooks/useBackendStatus";
import { useTranscriptionRuntimeQuery, usePreloadTranscriptionModelMutation } from "../../features/mediaProcessing";
import { useVoiceCloneStatusQuery, usePreloadVoiceCloneModelMutation } from "../../hooks/useVoiceClone";
import type { FeatureStatus } from "../../types/status";
import { ApiError } from "../../api/client";

function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}

function featureBadgeVariant(status: FeatureStatus | undefined): BadgeVariant {
  switch (status) {
    case "available":
      return "success";
    case "unavailable":
      return "error";
    case "not_implemented":
      return "muted";
    default:
      return "muted";
  }
}

function featureLabel(status: FeatureStatus | undefined): string {
  switch (status) {
    case "available":
      return "verfügbar";
    case "unavailable":
      return "nicht verfügbar";
    case "not_implemented":
      return "geplant";
    default:
      return "—";
  }
}

function StatusIcon({ ok }: { ok: boolean }) {
  return ok ? (
    <CheckCircle2 size={14} className="status-popover__ok" />
  ) : (
    <XCircle size={14} className="status-popover__bad" />
  );
}

/** Preload button with a warning symbol — shown only when the model is
 * not yet cached and the runtime is importable, i.e. a preload is
 * actually relevant. */
function PreloadButton({
  isPending,
  onPreload,
  label,
}: {
  isPending: boolean;
  onPreload: () => void;
  label: string;
}) {
  return (
    <Button
      variant="secondary"
      size="sm"
      onClick={onPreload}
      disabled={isPending}
      className="status-popover__preload-btn"
    >
      {isPending ? (
        <Loader2 size={14} className="spin" />
      ) : (
        <AlertTriangle size={14} className="status-popover__warn" />
      )}
      {label}
    </Button>
  );
}

function PreloadFeedback({
  isSuccess,
  isError,
  error,
}: {
  isSuccess: boolean;
  isError: boolean;
  error: unknown;
}) {
  if (isSuccess) {
    return (
      <div className="status-popover__ok-msg">
        <CheckCircle2 size={12} /> Modell heruntergeladen.
      </div>
    );
  }
  if (isError) {
    return (
      <div className="status-popover__err-msg">
        <XCircle size={12} />
        {error instanceof ApiError ? error.message : "Download fehlgeschlagen."}
      </div>
    );
  }
  return null;
}

function TranscriptionSection() {
  const query = useTranscriptionRuntimeQuery();
  const preload = usePreloadTranscriptionModelMutation();
  const rt = query.data;

  if (query.isLoading) {
    return (
      <div className="status-popover__section">
        <div className="status-popover__section-title">
          <Cpu size={14} /> <span>Transkription</span>
        </div>
        <div className="status-popover__muted">Lade Status …</div>
      </div>
    );
  }
  if (query.error || !rt) {
    return (
      <div className="status-popover__section">
        <div className="status-popover__section-title">
          <Cpu size={14} /> <span>Transkription</span>
        </div>
        <div className="status-popover__muted">
          {query.error instanceof ApiError ? query.error.message : "Status nicht verfügbar."}
        </div>
      </div>
    );
  }

  const modelCached = rt.model_cached === true;
  const importable = rt.faster_whisper_importable !== false;
  const canPreload = importable && !modelCached && !preload.isPending;

  return (
    <div className="status-popover__section">
      <div className="status-popover__section-title">
        <Cpu size={14} /> <span>Transkription</span>
        <Badge variant={rt.available ? "success" : rt.busy ? "info" : "error"}>
          {rt.available ? "Verfügbar" : rt.busy ? "GPU belegt" : "Nicht verfügbar"}
        </Badge>
      </div>
      <div className="status-popover__rows">
        <div className="status-popover__row">
          <span>Modell</span>
          <span>{rt.model}</span>
        </div>
        <div className="status-popover__row">
          <span>Device</span>
          <span>{rt.device}{rt.device_name ? ` (${rt.device_name})` : ""}</span>
        </div>
        <div className="status-popover__row">
          <span>faster-whisper</span>
          <StatusIcon ok={importable} />
        </div>
        <div className="status-popover__row">
          <span>Modell gecacht</span>
          {modelCached ? (
            <StatusIcon ok={true} />
          ) : (
            <span className="status-popover__muted">
              {importable ? "wird beim ersten Lauf heruntergeladen" : "—"}
            </span>
          )}
        </div>
        {rt.device.startsWith("cuda") && (
          <div className="status-popover__row">
            <span>CUDA</span>
            <StatusIcon ok={rt.cuda_available === true} />
          </div>
        )}
      </div>
      {canPreload && (
        <PreloadButton
          isPending={preload.isPending}
          onPreload={() => preload.mutate()}
          label="Modell vorab herunterladen"
        />
      )}
      <PreloadFeedback
        isSuccess={preload.isSuccess}
        isError={preload.isError}
        error={preload.error}
      />
      {rt.reasons.length > 0 && (
        <div className="status-popover__reasons">
          {rt.reasons.map((r, i) => (
            <div key={i} className="status-popover__reason">{r}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function VoiceCloneSection() {
  const query = useVoiceCloneStatusQuery();
  const preload = usePreloadVoiceCloneModelMutation();
  const st = query.data;

  if (query.isLoading) {
    return (
      <div className="status-popover__section">
        <div className="status-popover__section-title">
          <Wand2 size={14} /> <span>Voice Clone</span>
        </div>
        <div className="status-popover__muted">Lade Status …</div>
      </div>
    );
  }
  if (query.error || !st) {
    return (
      <div className="status-popover__section">
        <div className="status-popover__section-title">
          <Wand2 size={14} /> <span>Voice Clone</span>
        </div>
        <div className="status-popover__muted">
          {query.error instanceof ApiError ? query.error.message : "Status nicht verfügbar."}
        </div>
      </div>
    );
  }

  const modelCached = st.model_cached === true;
  const importable = st.qwen_tts_importable === true;
  const canPreload = importable && !modelCached && !preload.isPending;

  return (
    <div className="status-popover__section">
      <div className="status-popover__section-title">
        <Wand2 size={14} /> <span>Voice Clone</span>
        <Badge variant={st.available ? "success" : st.busy ? "info" : "error"}>
          {st.available ? "Verfügbar" : st.busy ? "GPU belegt" : "Nicht verfügbar"}
        </Badge>
      </div>
      <div className="status-popover__rows">
        <div className="status-popover__row">
          <span>Modell</span>
          <span>{st.model_id}</span>
        </div>
        {st.device && (
          <div className="status-popover__row">
            <span>Device</span>
            <span>{st.device}{st.device_name ? ` (${st.device_name})` : ""}</span>
          </div>
        )}
        <div className="status-popover__row">
          <span>qwen_tts</span>
          <StatusIcon ok={importable} />
        </div>
        <div className="status-popover__row">
          <span>Modell gecacht</span>
          {modelCached ? (
            <StatusIcon ok={true} />
          ) : (
            <span className="status-popover__muted">
              {importable ? "wird beim ersten Lauf heruntergeladen" : "—"}
            </span>
          )}
        </div>
        {st.cuda_available !== undefined && (
          <div className="status-popover__row">
            <span>CUDA</span>
            <StatusIcon ok={st.cuda_available === true} />
          </div>
        )}
      </div>
      {canPreload && (
        <PreloadButton
          isPending={preload.isPending}
          onPreload={() => preload.mutate()}
          label="Modell vorab herunterladen"
        />
      )}
      <PreloadFeedback
        isSuccess={preload.isSuccess}
        isError={preload.isError}
        error={preload.error}
      />
      {st.reasons && st.reasons.length > 0 && (
        <div className="status-popover__reasons">
          {st.reasons.map((r, i) => (
            <div key={i} className="status-popover__reason">{r}</div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Simple feature-flag-only section for pages whose service has no
 * ML model / runtime probe (e.g. VOD Downloader, Voice Profiles). */
function FeatureOnlySection({
  icon,
  label,
  status,
}: {
  icon: React.ReactNode;
  label: string;
  status: FeatureStatus | undefined;
}) {
  return (
    <div className="status-popover__section">
      <div className="status-popover__section-title">
        {icon} <span>{label}</span>
        <Badge variant={featureBadgeVariant(status)} title={featureLabel(status)}>
          {featureLabel(status)}
        </Badge>
      </div>
      {status === "unavailable" && (
        <div className="status-popover__muted">
          Dienst ist aktuell nicht verfügbar.
        </div>
      )}
      {status === "not_implemented" && (
        <div className="status-popover__muted">Noch nicht implementiert.</div>
      )}
    </div>
  );
}

/** Map a route path to the service section relevant for that page.
 * Returns null when the page has no associated service status (e.g.
 * Dashboard, Settings) — in that case only the backend header is shown. */
function ServiceSectionForRoute({ pathname }: { pathname: string }) {
  const { data } = useBackendStatus();
  const features = data?.features;

  // Match longest route prefix (detail pages like /vod-pipeline/:id).
  if (pathname === "/transcription" || pathname.startsWith("/transcription/")) {
    return <TranscriptionSection />;
  }
  if (pathname === "/voice-clone" || pathname.startsWith("/voice-clone/")) {
    return <VoiceCloneSection />;
  }
  if (pathname === "/vod-pipeline" || pathname.startsWith("/vod-pipeline/")) {
    return (
      <FeatureOnlySection
        icon={<Cpu size={14} />}
        label="VOD Pipeline"
        status={features?.vod_pipeline}
      />
    );
  }
  if (pathname === "/vod-downloader" || pathname.startsWith("/vod-downloader/")) {
    return (
      <FeatureOnlySection
        icon={<Cpu size={14} />}
        label="VOD Downloader"
        status={features?.vod_downloader}
      />
    );
  }
  if (pathname === "/voice-profiles" || pathname.startsWith("/voice-profiles/")) {
    return (
      <FeatureOnlySection
        icon={<Wand2 size={14} />}
        label="Voice Profiles"
        status={features?.voice_profiles}
      />
    );
  }
  if (pathname === "/twitch-profiles" || pathname.startsWith("/twitch-profiles/")) {
    return (
      <FeatureOnlySection
        icon={<Cpu size={14} />}
        label="Twitch-Profile"
        status={features?.twitch_profiles}
      />
    );
  }
  return null;
}

export function BackendStatusPopover() {
  const { status, data } = useBackendStatus();
  const location = useLocation();
  const [hoverOpen, setHoverOpen] = useState(false);

  const statusClass =
    status === "online" ? "is-online" : status === "offline" ? "is-offline" : "";
  const statusText =
    status === "online" ? "online" : status === "offline" ? "offline" : "verbinde …";

  const open = hoverOpen;
  const serviceSection = <ServiceSectionForRoute pathname={location.pathname} />;
  const hasServiceSection = serviceSection !== null;

  return (
    <div
      className={`status-popover__trigger ${statusClass} ${open ? "is-open" : ""}`}
      onMouseEnter={() => setHoverOpen(true)}
      onMouseLeave={() => setHoverOpen(false)}
      onFocus={() => setHoverOpen(true)}
      onBlur={() => setHoverOpen(false)}
      role="status"
      aria-live="polite"
      tabIndex={0}
    >
      <span className="topbar__status-dot" aria-hidden="true" />
      <span>{statusText}</span>

      {open && (
        <div className="status-popover" role="dialog" aria-label="Backend- und Service-Status">
          <div className="status-popover__header">
            <div className="status-popover__header-title">
              <Server size={14} />
              <span>Backend</span>
              <Badge variant={status === "online" ? "success" : status === "offline" ? "error" : "muted"}>
                {statusText}
              </Badge>
            </div>
            {data && (
              <div className="status-popover__header-meta">
                <span>v{data.version}</span>
                <span>·</span>
                <span>Uptime {formatUptime(data.uptime_seconds)}</span>
              </div>
            )}
          </div>

          {hasServiceSection && (
            <div className="status-popover__runtimes">{serviceSection}</div>
          )}

          {status === "offline" && (
            <div className="status-popover__footer">
              <CircleDot size={12} /> Verbindung zum Backend wird aufgebaut.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
