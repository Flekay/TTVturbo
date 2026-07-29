import { Check, GitBranch, GitCommitHorizontal, History, Loader2, Send, Sparkles } from "lucide-react";
import { useState } from "react";
import type { EditBranch, EditCommit } from "../../features/projects/api";
import { Button } from "../ui/Button";

interface EditorSidePanelProps {
  branches: EditBranch[];
  activeBranchId?: string | null;
  checkoutCommitId: string;
  detachedCommitId?: string | null;
  commits: EditCommit[];
  onExecuteCommand: (command: string) => Promise<string>;
  onCheckoutBranch: (branchId: string) => Promise<void> | void;
  onCheckoutCommit: (commitId: string) => Promise<void> | void;
  onCreateVariant: (commitId: string) => Promise<void> | void;
}

type Tab = "language" | "history";

interface CommandEntry {
  id: string;
  command: string;
  result: string;
  error?: boolean;
}

const EXAMPLES = [
  "Zentriere den ausgewählten Clip",
  "Mach den Clip 20% kleiner",
  "Verschiebe ihn 10% nach rechts",
  "Teile den Clip am Abspielkopf",
  "Video und Audio trennen",
  "Stummschalten",
];

export function EditorSidePanel({
  branches,
  activeBranchId,
  checkoutCommitId,
  detachedCommitId,
  commits,
  onExecuteCommand,
  onCheckoutBranch,
  onCheckoutCommit,
  onCreateVariant,
}: EditorSidePanelProps) {
  const [tab, setTab] = useState<Tab>("language");
  const [command, setCommand] = useState("");
  const [busy, setBusy] = useState(false);
  const [entries, setEntries] = useState<CommandEntry[]>([]);

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
        <div className="editor-history-panel">
          <div className="editor-history-branches">
            <header><GitBranch size={15} /><strong>Varianten</strong></header>
            <div>
              {branches.map((branch) => (
                <button type="button" key={branch.id} className={branch.id === activeBranchId && !detachedCommitId ? "is-active" : ""} onClick={() => void onCheckoutBranch(branch.id)}>{branch.name}</button>
              ))}
            </div>
          </div>

          {detachedCommitId ? (
            <div className="editor-history-detached">
              <History size={15} />
              <div><strong>Historische Version</strong><span>Zum Bearbeiten zuerst eine neue Variante erstellen.</span></div>
              <Button size="sm" variant="primary" onClick={() => void onCreateVariant(detachedCommitId)}>Von hier weiterarbeiten</Button>
            </div>
          ) : null}

          <div className="editor-history-list">
            {commits.map((commit, index) => (
              <article key={commit.id} className={commit.id === checkoutCommitId ? "is-current" : ""}>
                <div className="editor-history-node"><GitCommitHorizontal size={14} /><span /></div>
                <button type="button" onClick={() => void onCheckoutCommit(commit.id)}>
                  <strong>{commit.message}</strong>
                  <small>{new Date(commit.created_at).toLocaleString("de-DE")}</small>
                </button>
                {commit.id === checkoutCommitId ? <span className="editor-history-current">Aktuell</span> : <Button variant="ghost" size="sm" onClick={() => void onCreateVariant(commit.id)}>Variante</Button>}
                {index === commits.length - 1 ? <i /> : null}
              </article>
            ))}
          </div>
          {commits.length === 0 ? <div className="editor-language-empty"><Loader2 className="spin" /> Versionen werden geladen …</div> : null}
        </div>
      )}
    </aside>
  );
}
