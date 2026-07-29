import * as DialogPrimitive from "@radix-ui/react-dialog";
import { FileAudio, Film, Loader2, Search, Upload, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import type { LibraryItem } from "../../features/library/schemas";
import { useLibraryItemsQuery, useUploadToLibraryMutation } from "../../features/library/hooks";
import { Button } from "../ui/Button";

export type AddMediaMode = "VIDEO" | "AUDIO";

interface AddMediaDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdd: (item: LibraryItem, mode: AddMediaMode) => Promise<void> | void;
  busy?: boolean;
}

function isLikelyAudio(item: LibraryItem): boolean {
  return /\.(mp3|wav|flac|aac|m4a|ogg|opus)$/i.test(item.file_name);
}

export function AddMediaDialog({ open, onOpenChange, onAdd, busy = false }: AddMediaDialogProps) {
  const fileInput = useRef<HTMLInputElement | null>(null);
  const library = useLibraryItemsQuery();
  const upload = useUploadToLibraryMutation();
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mode, setMode] = useState<AddMediaMode>("VIDEO");

  const items = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("de-DE");
    return (library.data?.items ?? [])
      .filter((item) => item.lifecycle !== "TEMPORARY" && item.file_exists !== false)
      .filter((item) => !normalized || `${item.title} ${item.file_name}`.toLocaleLowerCase("de-DE").includes(normalized));
  }, [library.data?.items, query]);

  const selected = items.find((item) => item.id === selectedId) ?? null;

  async function handleUpload(file: File | undefined) {
    if (!file) return;
    const item = await upload.mutateAsync(file);
    setSelectedId(item.id);
    setMode(isLikelyAudio(item) ? "AUDIO" : "VIDEO");
  }

  async function submit() {
    if (!selected || busy) return;
    const selectedMode: AddMediaMode = isLikelyAudio(selected) ? "AUDIO" : mode;
    try {
      await onAdd(selected, selectedMode);
      onOpenChange(false);
      setSelectedId(null);
      setQuery("");
    } catch {
      // Parent action reports the concrete error and the dialog remains open.
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => { if (!busy) onOpenChange(next); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="editor-dialog__overlay" />
        <DialogPrimitive.Content className="editor-media-dialog" aria-describedby="editor-add-media-description">
          <header className="editor-dialog__header">
            <div>
              <DialogPrimitive.Title>Medien hinzufügen</DialogPrimitive.Title>
              <DialogPrimitive.Description id="editor-add-media-description">
                Library-Medium auswählen oder eine Datei hochladen. Sie wird an der aktuellen Abspielposition eingefügt.
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close asChild><Button variant="icon" aria-label="Schließen"><X size={18} /></Button></DialogPrimitive.Close>
          </header>

          <div className="editor-media-dialog__toolbar">
            <label className="editor-media-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Library durchsuchen …" /></label>
            <input ref={fileInput} hidden type="file" accept="video/*,audio/*" onChange={(event) => void handleUpload(event.target.files?.[0])} />
            <Button variant="secondary" onClick={() => fileInput.current?.click()} loading={upload.isPending}><Upload size={15} /> Datei hochladen</Button>
          </div>

          <div className="editor-media-dialog__content">
            <div className="editor-media-list" role="listbox" aria-label="Library-Medien">
              {library.isLoading ? <div className="editor-media-dialog__state"><Loader2 className="spin" /> Medien werden geladen …</div> : null}
              {!library.isLoading && items.length === 0 ? <div className="editor-media-dialog__state">Keine passenden Medien vorhanden.</div> : null}
              {items.map((item) => {
                const audio = isLikelyAudio(item);
                return (
                  <button
                    type="button"
                    role="option"
                    aria-selected={selectedId === item.id}
                    key={item.id}
                    className={`editor-media-row${selectedId === item.id ? " is-selected" : ""}`}
                    onClick={() => { setSelectedId(item.id); setMode(audio ? "AUDIO" : "VIDEO"); }}
                  >
                    <span className="editor-media-row__icon">{audio ? <FileAudio size={20} /> : <Film size={20} />}</span>
                    <span className="editor-media-row__body"><strong>{item.title}</strong><small>{item.file_name}{item.duration_seconds ? ` · ${Math.round(item.duration_seconds)} s` : ""}</small></span>
                  </button>
                );
              })}
            </div>

            <aside className="editor-media-dialog__details">
              {selected ? (
                <>
                  <div className="editor-media-dialog__preview">{isLikelyAudio(selected) ? <FileAudio size={40} /> : <Film size={40} />}</div>
                  <div><strong>{selected.title}</strong><small>{selected.file_name}</small></div>
                  <fieldset>
                    <legend>Einfügen als</legend>
                    <label><input type="radio" checked={mode === "VIDEO"} disabled={isLikelyAudio(selected)} onChange={() => setMode("VIDEO")} /> Video mit Ton</label>
                    <label><input type="radio" checked={mode === "AUDIO"} onChange={() => setMode("AUDIO")} /> Nur Audio</label>
                  </fieldset>
                </>
              ) : <div className="editor-media-dialog__state">Medium auswählen.</div>}
            </aside>
          </div>

          <footer className="editor-dialog__footer">
            <DialogPrimitive.Close asChild><Button variant="secondary" disabled={busy}>Abbrechen</Button></DialogPrimitive.Close>
            <Button variant="primary" disabled={!selected} loading={busy} onClick={() => void submit()}>Zur Timeline hinzufügen</Button>
          </footer>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
