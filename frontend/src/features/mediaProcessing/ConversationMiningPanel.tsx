import { useState } from "react";
import {
  AlertTriangle,
  Cpu,
  Layers,
  Play,
  RefreshCw,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { Badge, type BadgeVariant } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { formatDateTime, formatDuration } from "../../utils/format";
import {
  useMiningRuntimeQuery,
  useVodMiningRunsQuery,
  useStartMiningRunMutation,
  useCancelMiningRunMutation,
  useRetryMiningRunMutation,
  useDeleteMiningRunMutation,
} from "../mediaProcessing";
import type { MiningRun, Conversation } from "../mediaProcessing";

/**
 * Conversation Mining panel.
 *
 * Shows the mining model availability, lets the user start a new
 * mining run for a VOD, and lists the resulting conversations
 * (sections) with their category, signals and transcript excerpt.
 *
 * The panel is read-only: it does not produce final clips or scores.
 * It only surfaces the structured conversation sections that may
 * later be evaluated as clip candidates.
 */

interface ConversationMiningPanelProps {
  /** The VOD/media item id to mine. */
  vodId: string;
  /** Optional callback when a conversation is clicked (e.g. to seek the player). */
  onConversationClick?: (conversation: Conversation) => void;
}

function miningStatusBadge(status: string): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "COMPLETED":
      return { variant: "success", label: "Fertig" };
    case "QUEUED":
      return { variant: "info", label: "Wartet" };
    case "RUNNING":
      return { variant: "info", label: "Läuft" };
    case "FAILED":
      return { variant: "error", label: "Fehlgeschlagen" };
    case "CANCELED":
      return { variant: "muted", label: "Abgebrochen" };
    case "STALE":
      return { variant: "warning", label: "Veraltet" };
    default:
      return { variant: "muted", label: status };
  }
}

function formatTimestamp(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

const CATEGORY_LABELS: Record<string, string> = {
  REACTION: "Reaktion",
  STORY: "Story",
  OPINION: "Meinung",
  EXPLANATION: "Erklärung",
  JOKE: "Witz",
  ARGUMENT: "Argument",
  QUESTION: "Frage",
  GAMEPLAY_EVENT: "Gameplay",
  CHAT_INTERACTION: "Chat",
  OTHER: "Sonstiges",
};

const SIGNAL_LABELS: Record<string, string> = {
  emotion: "Emotion",
  surprise: "Überraschung",
  humor: "Humor",
  controversy: "Kontrovers",
  clear_context: "Klarer Kontext",
  self_contained: "In sich geschlossen",
  strong_opening: "Starker Einstieg",
  strong_ending: "Starkes Ende",
  payoff: "Payoff",
  story_progression: "Story-Fortschritt",
  chat_interaction: "Chat-Interaktion",
  gameplay_context: "Gameplay-Kontext",
};

export function ConversationMiningPanel({ vodId, onConversationClick }: ConversationMiningPanelProps) {
  const runtimeQuery = useMiningRuntimeQuery();
  const runsQuery = useVodMiningRunsQuery(vodId);
  const startMutation = useStartMiningRunMutation();
  const cancelMutation = useCancelMiningRunMutation();
  const retryMutation = useRetryMiningRunMutation();
  const deleteMutation = useDeleteMiningRunMutation();
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);

  const runs = runsQuery.data?.runs ?? [];
  const latestRun = runs[0];
  const activeRun = runs.find((r) => r.status === "QUEUED" || r.status === "RUNNING");
  const runtime = runtimeQuery.data;
  const modelAvailable = runtime?.available ?? false;

  const handleStart = () => {
    startMutation.mutate({ media_item_id: vodId });
  };

  const handleStartForce = () => {
    startMutation.mutate({ media_item_id: vodId, force: true });
  };

  if (runtimeQuery.isLoading) {
    return <LoadingState message="Mining-Status wird geladen …" />;
  }
  if (runtimeQuery.isError) {
    return (
      <ErrorState
        title="Mining-Status konnte nicht geladen werden"
        message={String(runtimeQuery.error)}
        onRetry={() => runtimeQuery.refetch()}
      />
    );
  }

  return (
    <div className="mining-panel">
      {/* Runtime status */}
      <Card
        title={
          <span className="mining-panel__title">
            <Sparkles size={16} /> Conversation Mining
          </span>
        }
        sub={
          <span className="mining-panel__runtime">
            <Cpu size={14} />
            {modelAvailable ? (
              <>
                <Badge variant="success">Verfügbar</Badge>
                <span className="mining-panel__model">{runtime?.model}</span>
                <span className="mining-panel__device">{runtime?.device}</span>
              </>
            ) : (
              <>
                <Badge variant="muted">Nicht verfügbar</Badge>
                <span className="mining-panel__reasons">
                  {runtime?.reasons?.join(", ") ?? "Kein Modell konfiguriert"}
                </span>
              </>
            )}
          </span>
        }
        actions={
          <div className="mining-panel__actions">
            <Button
              variant="primary"
              size="sm"
              onClick={handleStart}
              disabled={!modelAvailable || !!activeRun || startMutation.isPending}
              loading={startMutation.isPending}
              title={!modelAvailable ? "Kein Mining-Modell konfiguriert" : undefined}
            >
              <Play size={14} /> Mining starten
            </Button>
            {latestRun && latestRun.status === "COMPLETED" && (
              <Button
                variant="secondary"
                size="sm"
                onClick={handleStartForce}
                disabled={!modelAvailable || !!activeRun || startMutation.isPending}
                title="Neue Mining-Analyse erzwingen (ignoriert Idempotenz)"
              >
                <RefreshCw size={14} /> Neu analysieren
              </Button>
            )}
          </div>
        }
      >
        {startMutation.isError && (
          <div className="mining-panel__error">
            <AlertTriangle size={14} />
            <span>Start fehlgeschlagen: {String(startMutation.error)}</span>
          </div>
        )}
      </Card>

      {/* Runs list */}
      {runsQuery.isLoading ? (
        <LoadingState message="Mining-Läufe werden geladen …" />
      ) : runsQuery.isError ? (
        <ErrorState
          title="Mining-Läufe konnten nicht geladen werden"
          message={String(runsQuery.error)}
          onRetry={() => runsQuery.refetch()}
        />
      ) : runs.length === 0 ? (
        <EmptyState
          title="Keine Mining-Läufe"
          description="Starte eine Conversation-Mining-Analyse, um strukturierte Gesprächsabschnitte zu erkennen."
          icon={<Layers />}
        />
      ) : (
        <div className="mining-runs">
          {runs.map((run) => (
            <MiningRunCard
              key={run.id}
              run={run}
              expanded={expandedRunId === run.id}
              onToggle={() => setExpandedRunId((prev) => (prev === run.id ? null : run.id))}
              onCancel={() => cancelMutation.mutate(run.id)}
              onRetry={() => retryMutation.mutate(run.id)}
              onDelete={() => deleteMutation.mutate(run.id)}
              onConversationClick={onConversationClick}
              cancelPending={cancelMutation.isPending}
              retryPending={retryMutation.isPending}
              deletePending={deleteMutation.isPending}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface MiningRunCardProps {
  run: MiningRun;
  expanded: boolean;
  onToggle: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onDelete: () => void;
  onConversationClick?: (conversation: Conversation) => void;
  cancelPending: boolean;
  retryPending: boolean;
  deletePending: boolean;
}

function MiningRunCard({
  run,
  expanded,
  onToggle,
  onCancel,
  onRetry,
  onDelete,
  onConversationClick,
  cancelPending,
  retryPending,
  deletePending,
}: MiningRunCardProps) {
  const status = miningStatusBadge(run.status);
  const isActive = run.status === "QUEUED" || run.status === "RUNNING";
  const isTerminal = run.status === "COMPLETED" || run.status === "FAILED" || run.status === "CANCELED" || run.status === "STALE";
  const conversations = run.conversations ?? [];
  const blocks = run.blocks ?? [];
  const doneBlocks = blocks.filter((b) => b.status === "COMPLETED" || b.status === "FAILED" || b.status === "CANCELED").length;
  const progress = run.progress ?? 0;

  return (
    <Card className="mining-run-card">
      <div className="mining-run-card__head">
        <Badge variant={status.variant}>{status.label}</Badge>
        <span className="mining-run-card__meta">
          {run.created_at && <span>{formatDateTime(run.created_at)}</span>}
          {run.model?.model_id && <span>· {run.model.model_id}</span>}
          {conversations.length > 0 && <span>· {conversations.length} Abschnitte</span>}
        </span>
        <div className="mining-run-card__actions">
          {isActive && (
            <Button variant="secondary" size="sm" onClick={onCancel} disabled={cancelPending}>
              <X size={14} /> Abbrechen
            </Button>
          )}
          {(run.status === "FAILED" || run.status === "CANCELED" || run.status === "STALE") && (
            <Button variant="secondary" size="sm" onClick={onRetry} disabled={retryPending}>
              <RefreshCw size={14} /> Erneut
            </Button>
          )}
          {isTerminal && (
            <Button variant="danger" size="sm" onClick={onDelete} disabled={deletePending}>
              <Trash2 size={14} /> Löschen
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={onToggle} aria-expanded={expanded}>
            {expanded ? "Details ausblenden" : "Details"}
          </Button>
        </div>
      </div>

      {isActive && blocks.length > 0 && (
        <div className="mining-run-card__progress">
          <div className="vp-progress-bar">
            <div className="vp-progress-bar__fill" style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} />
          </div>
          <div className="vp-progress-meta">
            <span>Block {doneBlocks} von {blocks.length}</span>
            <span>{progress.toFixed(0)}%</span>
          </div>
        </div>
      )}

      {run.error && (
        <div className="mining-run-card__error">
          <AlertTriangle size={14} />
          <span>{run.error}</span>
        </div>
      )}

      {run.status === "STALE" && (
        <div className="mining-run-card__stale">
          <AlertTriangle size={14} />
          <span>Das Transkript wurde seit dieser Analyse geändert. Bitte neu analysieren.</span>
        </div>
      )}

      {/* Conversations list (always visible when completed) */}
      {run.status === "COMPLETED" && conversations.length > 0 && (
        <div className="mining-conversations">
          {conversations.map((conv) => (
            <ConversationItem
              key={conv.id}
              conversation={conv}
              onClick={onConversationClick}
            />
          ))}
        </div>
      )}
      {run.status === "COMPLETED" && conversations.length === 0 && (
        <div className="mining-run-card__empty">
          Keine Gesprächsabschnitte erkannt.
        </div>
      )}

      {/* Expanded details: blocks */}
      {expanded && blocks.length > 0 && (
        <div className="mining-run-card__blocks">
          <div className="mining-run-card__blocks-title">Blöcke ({blocks.length})</div>
          <div className="mining-block-list">
            {blocks.map((block) => (
              <div key={block.block_id} className="mining-block">
                <span className="mining-block__id">{block.block_id}</span>
                <span className="mining-block__time">
                  {formatTimestamp(block.start)} – {formatTimestamp(block.end)}
                </span>
                <Badge variant={blockStatusVariant(block.status)}>{block.status}</Badge>
                {block.result_count != null && (
                  <span className="mining-block__count">{block.result_count} Treffer</span>
                )}
                {block.error && <span className="mining-block__error">{block.error}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function blockStatusVariant(status: string): BadgeVariant {
  switch (status) {
    case "COMPLETED":
      return "success";
    case "RUNNING":
      return "info";
    case "QUEUED":
      return "muted";
    case "FAILED":
      return "error";
    case "CANCELED":
      return "muted";
    default:
      return "muted";
  }
}

interface ConversationItemProps {
  conversation: Conversation;
  onClick?: (conversation: Conversation) => void;
}

function ConversationItem({ conversation, onClick }: ConversationItemProps) {
  const categoryLabel = CATEGORY_LABELS[conversation.category] ?? conversation.category;
  const duration = conversation.end - conversation.start;
  const signals = conversation.signals ?? [];
  const ctx = conversation.context;
  const clickable = !!onClick;

  return (
    <button
      type="button"
      className={`mining-conversation${clickable ? " is-clickable" : ""}`}
      onClick={() => onClick?.(conversation)}
      disabled={!clickable}
    >
      <div className="mining-conversation__head">
        <span className="mining-conversation__time">
          {formatTimestamp(conversation.start)} – {formatTimestamp(conversation.end)}
        </span>
        <span className="mining-conversation__duration">{formatDuration(duration)}</span>
        <Badge variant="info">{categoryLabel}</Badge>
        <span className="mining-conversation__confidence">
          {(conversation.confidence * 100).toFixed(0)}%
        </span>
      </div>
      {conversation.title && (
        <div className="mining-conversation__title">{conversation.title}</div>
      )}
      {conversation.summary && (
        <div className="mining-conversation__summary">{conversation.summary}</div>
      )}
      {signals.length > 0 && (
        <div className="mining-conversation__signals">
          {signals.map((s) => (
            <span key={s} className="mining-signal" title={s}>
              {SIGNAL_LABELS[s] ?? s}
            </span>
          ))}
        </div>
      )}
      {ctx && (ctx.requires_previous_context || ctx.requires_following_context) && (
        <div className="mining-conversation__context">
          {ctx.requires_previous_context && <span>⚠ benötigt Vorwissen</span>}
          {ctx.requires_following_context && <span>⚠ benötigt Nachwissen</span>}
        </div>
      )}
      {conversation.transcript_excerpt && (
        <div className="mining-conversation__excerpt">
          "{conversation.transcript_excerpt}"
        </div>
      )}
    </button>
  );
}
