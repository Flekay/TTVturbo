import { useMemo, useState } from "react";
import { PromptRecordingPanel } from "./PromptRecordingPanel";
import type { PromptFilter, VoiceProfileReference, VoiceScript } from "./types";

interface PromptBrowserProps {
  scripts: VoiceScript[];
  references: VoiceProfileReference[];
  profileId: string;
  onAttachReference: (scriptId: string, recordingFilename: string) => void;
  onDetachReference: (scriptId: string) => void;
  onAcceptReview: (scriptId: string) => void;
  attachPendingScriptId: string | null;
  detachPendingScriptId: string | null;
  acceptPendingScriptId: string | null;
}

const FILTER_LABELS: Record<PromptFilter, string> = {
  ALL: "Alle",
  MISSING: "Fehlend",
  ACCEPTED: "Akzeptiert",
  REVIEW: "Review",
  REJECTED: "Abgelehnt",
};

const FILTERS: PromptFilter[] = ["ALL", "MISSING", "ACCEPTED", "REVIEW", "REJECTED"];

export function PromptBrowser({
  scripts,
  references,
  profileId,
  onAttachReference,
  onDetachReference,
  onAcceptReview,
  attachPendingScriptId,
  detachPendingScriptId,
  acceptPendingScriptId,
}: PromptBrowserProps) {
  const [filter, setFilter] = useState<PromptFilter>("ALL");
  const [styleFilter, setStyleFilter] = useState<string>("");
  const [categoryFilter, setCategoryFilter] = useState<string>("");
  const [search, setSearch] = useState("");

  const refByScript = useMemo(() => {
    const map = new Map<string, VoiceProfileReference>();
    for (const ref of references) map.set(ref.script_id, ref);
    return map;
  }, [references]);

  const styles = useMemo(() => {
    const set = new Set<string>();
    for (const s of scripts) if (s.style) set.add(s.style);
    return Array.from(set).sort();
  }, [scripts]);

  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const s of scripts) if (s.category) set.add(s.category);
    return Array.from(set).sort();
  }, [scripts]);

  const visibleScripts = useMemo(() => {
    const sorted = [...scripts].sort((a, b) => a.order - b.order);
    const q = search.trim().toLowerCase();
    return sorted.filter((script) => {
      const ref = refByScript.get(script.id);
      const status = ref?.status ?? null;
      if (filter === "MISSING") {
        if (ref) return false;
      } else if (filter !== "ALL") {
        if (status !== filter) return false;
      }
      if (styleFilter && script.style !== styleFilter) return false;
      if (categoryFilter && script.category !== categoryFilter) return false;
      if (q && !script.text.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [scripts, refByScript, filter, styleFilter, categoryFilter, search]);

  return (
    <div className="vp-prompt-browser">
      <div className="vp-prompt-browser__controls">
        <div role="group" aria-label="Statusfilter" style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              className={`btn ${filter === f ? "btn--primary" : "btn--secondary"} btn--sm`}
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
            >
              {FILTER_LABELS[f]}
            </button>
          ))}
        </div>
        {styles.length > 0 && (
          <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
            <span className="sr-only">Stil</span>
            <select
              className="list-controls__select"
              value={styleFilter}
              onChange={(e) => setStyleFilter(e.target.value)}
              aria-label="Stil filtern"
            >
              <option value="">Alle Stile</option>
              {styles.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        )}
        {categories.length > 0 && (
          <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
            <span className="sr-only">Kategorie</span>
            <select
              className="list-controls__select"
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              aria-label="Kategorie filtern"
            >
              <option value="">Alle Kategorien</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        )}
        <input
          className="list-controls__search"
          type="search"
          placeholder="Text durchsuchen …"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Prompt-Text durchsuchen"
          style={{ flex: 1, minWidth: 160 }}
        />
      </div>

      {visibleScripts.length === 0 ? (
        <p className="page__description" role="status">
          Keine Treffer für die aktuellen Filter.
        </p>
      ) : (
        <ul className="vp-prompt-browser__list" aria-label="Prompts">
          {visibleScripts.map((script, index) => (
            <li key={script.id}>
              <PromptRecordingPanel
                script={script}
                reference={refByScript.get(script.id) ?? null}
                profileId={profileId}
                index={index}
                onAttachReference={(filename) => onAttachReference(script.id, filename)}
                onDetachReference={() => onDetachReference(script.id)}
                onAcceptReview={() => onAcceptReview(script.id)}
                attachPending={attachPendingScriptId === script.id}
                detachPending={detachPendingScriptId === script.id}
                acceptPending={acceptPendingScriptId === script.id}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
