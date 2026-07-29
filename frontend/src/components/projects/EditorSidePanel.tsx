import { Check, GitCommitHorizontal, History, Loader2, Send, Sparkles } from "lucide-react";
import { useMemo, useRef, useState, type CSSProperties, type WheelEvent } from "react";
import type { EditCommit } from "../../features/projects/api";

interface EditorSidePanelProps {
  checkoutCommitId: string;
  commits: EditCommit[];
  totalCommits: number;
  commitsLoading: boolean;
  hasMoreCommits: boolean;
  loadingMoreCommits: boolean;
  onExecuteCommand: (command: string) => Promise<string>;
  onCheckoutCommit: (commitId: string) => Promise<void> | void;
  onLoadMoreCommits: () => Promise<unknown> | void;
}

type Tab = "language" | "history";

interface CommandEntry {
  id: string;
  command: string;
  result: string;
  error?: boolean;
}

interface GraphSegment {
  fromLane: number;
  toLane: number;
  fromY: number;
  toY: number;
  color: string;
  dashed?: boolean;
}

interface CommitGraphRow {
  commit: EditCommit;
  lane: number;
  color: string;
  segments: GraphSegment[];
}

interface CommitGraphLayout {
  rows: CommitGraphRow[];
  width: number;
}

const EXAMPLES = [
  "Zentriere den ausgewählten Clip",
  "Mach den Clip 20% kleiner",
  "Verschiebe ihn 10% nach rechts",
  "Teile den Clip am Abspielkopf",
  "Video und Audio trennen",
  "Stummschalten",
];

const GRAPH_COLORS = ["#7b61ff", "#c4609e", "#b59a39", "#69ad67", "#4da5a8", "#5686d8"];
const GRAPH_ROW_HEIGHT = 58;
const GRAPH_CENTER_Y = GRAPH_ROW_HEIGHT / 2;
const GRAPH_LANE_GAP = 14;
const GRAPH_PADDING_X = 10;

function laneColor(lane: number): string {
  return GRAPH_COLORS[lane % GRAPH_COLORS.length];
}

function graphX(lane: number): number {
  return GRAPH_PADDING_X + lane * GRAPH_LANE_GAP;
}

/**
 * Builds a compact Git-style lane projection from the paginated commit list.
 * Parent IDs are enough to retain branch and merge paths without exposing the
 * editor's internal branch/variant model in the UI.
 */
export function buildCommitGraph(commits: EditCommit[]): CommitGraphLayout {
  let lanes: string[] = [];
  let maxLaneCount = 1;

  const rows = commits.map((commit): CommitGraphRow => {
    let before = [...lanes];
    let lane = before.indexOf(commit.id);
    const startsNewLane = lane < 0;

    if (startsNewLane) {
      lane = before.length;
      before.push(commit.id);
    }

    const parents = [...new Set(commit.parent_ids ?? [])];
    let after = [...before];

    if (parents.length === 0) {
      after.splice(lane, 1);
    } else {
      const firstParent = parents[0];
      const existingFirstParentLane = after.findIndex((id, index) => index !== lane && id === firstParent);

      if (existingFirstParentLane >= 0) {
        after.splice(lane, 1);
      } else {
        after[lane] = firstParent;
      }

      let insertAt = Math.min(lane + 1, after.length);
      for (const parentId of parents.slice(1)) {
        if (after.includes(parentId)) continue;
        after.splice(insertAt, 0, parentId);
        insertAt += 1;
      }
    }

    const segments: GraphSegment[] = [];

    before.forEach((targetId, beforeLane) => {
      if (targetId === commit.id) return;
      const afterLane = after.indexOf(targetId);
      if (afterLane < 0) return;
      segments.push({
        fromLane: beforeLane,
        toLane: afterLane,
        fromY: 0,
        toY: GRAPH_ROW_HEIGHT,
        color: laneColor(beforeLane),
      });
    });

    segments.push({
      fromLane: lane,
      toLane: lane,
      fromY: 0,
      toY: GRAPH_CENTER_Y,
      color: laneColor(lane),
      dashed: startsNewLane,
    });

    parents.forEach((parentId, parentIndex) => {
      const parentLane = after.indexOf(parentId);
      if (parentLane < 0) return;
      segments.push({
        fromLane: lane,
        toLane: parentLane,
        fromY: GRAPH_CENTER_Y,
        toY: GRAPH_ROW_HEIGHT,
        color: parentIndex === 0 ? laneColor(lane) : laneColor(parentLane),
      });
    });

    lanes = after;
    maxLaneCount = Math.max(maxLaneCount, before.length, after.length, lane + 1);

    return { commit, lane, color: laneColor(lane), segments };
  });

  return {
    rows,
    width: Math.max(42, GRAPH_PADDING_X * 2 + (maxLaneCount - 1) * GRAPH_LANE_GAP + 8),
  };
}

function formatCommitDate(value: string): string {
  return new Intl.DateTimeFormat("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function EditorSidePanel({
  checkoutCommitId,
  commits,
  totalCommits,
  commitsLoading,
  hasMoreCommits,
  loadingMoreCommits,
  onExecuteCommand,
  onCheckoutCommit,
  onLoadMoreCommits,
}: EditorSidePanelProps) {
  const [tab, setTab] = useState<Tab>("language");
  const [command, setCommand] = useState("");
  const [busy, setBusy] = useState(false);
  const [entries, setEntries] = useState<CommandEntry[]>([]);
  const loadMoreRequestedRef = useRef(false);
  const graph = useMemo(() => buildCommitGraph(commits), [commits]);

  async function submit(value = command) {
    const normalized = value.trim();
    if (!normalized || busy) return;
    setBusy(true);
    try {
      const result = await onExecuteCommand(normalized);
      setEntries((current) => [...current, { id: `${Date.now()}-${Math.random()}`, command: normalized, result }]);
      setCommand("");
    } catch (error) {
      setEntries((current) => [...current, {
        id: `${Date.now()}-${Math.random()}`,
        command: normalized,
        result: error instanceof Error ? error.message : "Befehl konnte nicht ausgeführt werden.",
        error: true,
      }]);
    } finally {
      setBusy(false);
    }
  }

  function loadMoreWhenNearBottom(element: HTMLElement) {
    if (!hasMoreCommits || loadingMoreCommits || loadMoreRequestedRef.current) return;
    const remaining = element.scrollHeight - element.scrollTop - element.clientHeight;
    if (remaining > 96) return;
    loadMoreRequestedRef.current = true;
    void Promise.resolve(onLoadMoreCommits())
      .catch(() => undefined)
      .finally(() => {
        loadMoreRequestedRef.current = false;
      });
  }

  function handleHistoryWheel(event: WheelEvent<HTMLDivElement>) {
    if (event.deltaY <= 0) return;
    loadMoreWhenNearBottom(event.currentTarget);
  }

  return (
    <aside className="editor-side-panel">
      <div className="editor-side-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "language"} className={tab === "language" ? "is-active" : ""} onClick={() => setTab("language")}><Sparkles size={15} /> Natural Language</button>
        <button type="button" role="tab" aria-selected={tab === "history"} className={tab === "history" ? "is-active" : ""} onClick={() => setTab("history")}><History size={15} /> Versionen</button>
      </div>

      {tab === "language" ? (
        <div className="editor-language-panel">
          <div className="editor-language-log">
            {entries.length === 0 ? (
              <div className="editor-language-empty">
                <Sparkles size={24} />
                <strong>Bearbeiten mit Text</strong>
                <span>Position, Größe, Rotation, Ton, Schnitt und Timeline-Befehle werden direkt als neue Projektversion gespeichert.</span>
              </div>
            ) : entries.map((entry) => (
              <article key={entry.id} className={entry.error ? "is-error" : ""}>
                <p>{entry.command}</p>
                <span>{entry.error ? null : <Check size={12} />}{entry.result}</span>
              </article>
            ))}
          </div>

          <div className="editor-language-examples">
            {EXAMPLES.map((example) => <button type="button" key={example} onClick={() => setCommand(example)}>{example}</button>)}
          </div>

          <div className="editor-language-input">
            <div className="editor-language-input__field">
              <textarea
                className="input"
                rows={4}
                value={command}
                onChange={(event) => setCommand(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submit();
                  }
                }}
                placeholder="z. B. Verschiebe den Clip 10% nach links …"
              />
              <button
                type="button"
                className="editor-language-input__send"
                onClick={() => void submit()}
                disabled={busy || !command.trim()}
                aria-label="Befehl anwenden"
              >
                {busy ? <Loader2 size={15} className="spin" /> : <Send size={15} />}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div
          className="editor-history-panel"
          aria-label="Versionsverlauf"
          onScroll={(event) => loadMoreWhenNearBottom(event.currentTarget)}
          onWheel={handleHistoryWheel}
        >
          <header className="editor-history-header">
            <div><GitCommitHorizontal size={15} /><strong>Änderungsverlauf</strong></div>
            <span>{commits.length} / {totalCommits}</span>
          </header>

          <div className="editor-history-list">
            {graph.rows.map(({ commit, lane, color, segments }) => {
              const isCurrent = commit.id === checkoutCommitId;
              const style = { "--history-color": color } as CSSProperties;
              return (
                <article key={commit.id} className={isCurrent ? "is-current" : ""} style={style}>
                  <svg
                    className="editor-history-graph"
                    width={graph.width}
                    height={GRAPH_ROW_HEIGHT}
                    viewBox={`0 0 ${graph.width} ${GRAPH_ROW_HEIGHT}`}
                    aria-hidden="true"
                  >
                    {segments.map((segment, index) => (
                      <path
                        key={`${commit.id}-${index}`}
                        d={`M ${graphX(segment.fromLane)} ${segment.fromY} L ${graphX(segment.toLane)} ${segment.toY}`}
                        stroke={segment.color}
                        strokeWidth="2"
                        strokeDasharray={segment.dashed ? "3 4" : undefined}
                        fill="none"
                        vectorEffect="non-scaling-stroke"
                      />
                    ))}
                    <circle cx={graphX(lane)} cy={GRAPH_CENTER_Y} r={isCurrent ? 6 : 5} fill="#11151d" stroke={color} strokeWidth={isCurrent ? 3 : 2} />
                    {isCurrent ? <circle cx={graphX(lane)} cy={GRAPH_CENTER_Y} r="2" fill={color} /> : null}
                  </svg>

                  <button
                    type="button"
                    className="editor-history-entry"
                    aria-current={isCurrent ? "true" : undefined}
                    onClick={() => void onCheckoutCommit(commit.id)}
                  >
                    <strong>{commit.message}</strong>
                    <span>
                      <time dateTime={commit.created_at}>{formatCommitDate(commit.created_at)}</time>
                      <code>{commit.id.slice(0, 7)}</code>
                    </span>
                  </button>

                  {isCurrent ? <span className="editor-history-current">Aktuell</span> : null}
                </article>
              );
            })}
          </div>

          {commitsLoading && commits.length === 0 ? (
            <div className="editor-history-status"><Loader2 size={15} className="spin" /> Änderungen werden geladen …</div>
          ) : null}

          {!commitsLoading && commits.length === 0 ? (
            <div className="editor-history-status">Noch keine Änderungen vorhanden.</div>
          ) : null}

          {commits.length > 0 ? (
            <div className="editor-history-pagination" aria-live="polite">
              {loadingMoreCommits ? <><Loader2 size={13} className="spin" /> Ältere Änderungen werden geladen …</> : hasMoreCommits ? "Weiter nach unten scrollen, um 10 ältere Änderungen zu laden." : "Ende des Verlaufs"}
            </div>
          ) : null}
        </div>
      )}
    </aside>
  );
}
