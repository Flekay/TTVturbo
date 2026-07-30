import * as DialogPrimitive from "@radix-ui/react-dialog";
import { FileAudio, Film, Image as ImageIcon, Loader2, Search, Upload, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import type { LibraryItem, FileType } from "../../features/library/schemas";
import { ACCEPTED_UPLOAD_ALL } from "../../features/library/schemas";
import { useLibraryItemsQuery, useUploadToLibraryTemporaryMutation } from "../../features/library/hooks";
import { Button } from "../ui/Button";

export type AddMediaMode = "VIDEO" | "AUDIO" | "IMAGE";

interface AddMediaDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdd: (item: LibraryItem, mode: AddMediaMode) => Promise<void> | void;
  busy?: boolean;
}

function fileTypeOf(item: LibraryItem): FileType {
  return item.file_type ?? "video";
}

function modeForItem(item: LibraryItem): AddMediaMode {
  const ft = fileTypeOf(item);
  if (ft === "audio") return "AUDIO";
  if (ft === "image") return "IMAGE";
  return "VIDEO";
}

function IconForItem({ item, size }: { item: LibraryItem; size: number }) {
  const ft = fileTypeOf(item);
  if (ft === "audio") return <FileAudio size={size} />;
  if (ft === "image") return <ImageIcon size={size} />;
  return <Film size={size} />;
}

export function AddMediaDialog({ open, onOpenChange, onAdd, busy = false }: AddMediaDialogProps) {
  const fileInput = useRef<HTMLInputElement | null>(null);
  // Only persistent library items — temp files would clutter the list.
  const library = useLibraryItemsQuery();
  const upload = useUploadToLibraryTemporaryMutation();
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mode, setMode] = useState<AddMediaMode>("VIDEO");
  // Track the most recently uploaded temp item so it appears in the list
  // even though the library query only returns persistent items.
  const [uploadedItem, setUploadedItem] = useState<LibraryItem | null>(null);

  const items = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("de-DE");
    const persistent = (library.data?.items ?? [])
      .filter((item) => item.file_exists !== false)
      .filter((item) => !normalized || `${item.title} ${item.file_name}`.toLocaleLowerCase("de-DE").includes(normalized));
    // Prepend the just-uploaded temp item (if not already in the list).
    if (uploadedItem && !persistent.some((item) => item.id === uploadedItem.id)) {
      if (!normalized || `${uploadedItem.title} ${uploadedItem.file_name}`.toLocaleLowerCase("de-DE").includes(normalized)) {
        return [uploadedItem, ...persistent];
      }
    }
    return persistent;
  }, [library.data?.items, query, uploadedItem]);

  const selected = items.find((item) => item.id === selectedId) ?? null;

  async function handleUpload(file: File | undefined) {
    if (!file) return;
    // Editor uploads are temporary — they are only persisted when the user
    // explicitly saves them to the library later. The temp item is tracked
    // locally so it shows up in this dialog without polluting the list
    // with every other temp file from quick tools etc.
    const item = await upload.mutateAsync(file);
    setUploadedItem(item);
    setSelectedId(item.id);
    setMode(modeForItem(item));
  }

  async function submit() {
    if (!selected || busy) return;
    const selectedMode: AddMediaMode = modeForItem(selected);
    try {
      await onAdd(selected, selectedMode);
      onOpenChange(false);
      setSelectedId(null);
      setQuery("");
      setUploadedItem(null);
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
                Library-Medium auswählen oder eine Datei hochladen. Sie wird an der aktuellen Abspielposition eingefügt. Hochgeladene Dateien sind temporär — speichere sie später in der Library, um sie dauerhaft zu behalten.
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close asChild><Button variant="icon" aria-label="Schließen"><X size={18} /></Button></DialogPrimitive.Close>
          </header>

          <div className="editor-media-dialog__toolbar">
            <label className="editor-media-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Library durchsuchen …" /></label>
            <input ref={fileInput} hidden type="file" accept={ACCEPTED_UPLOAD_ALL} onChange={(event) => void handleUpload(event.target.files?.[0])} />
            <Button variant="secondary" onClick={() => fileInput.current?.click()} loading={upload.isPending}><Upload size={15} /> Datei hochladen</Button>
          </div>

          <div className="editor-media-dialog__content">
            <div className="editor-media-list" role="listbox" aria-label="Library-Medien">
              {library.isLoading ? <div className="editor-media-dialog__state"><Loader2 className="spin" /> Medien werden geladen …</div> : null}
              {!library.isLoading && items.length === 0 ? <div className="editor-media-dialog__state">Keine passenden Medien vorhanden.</div> : null}
              {items.map((item) => {
                return (
                  <button
                    type="button"
                    role="option"
                    aria-selected={selectedId === item.id}
                    key={item.id}
                    className={`editor-media-row${selectedId === item.id ? " is-selected" : ""}`}
                    onClick={() => { setSelectedId(item.id); setMode(modeForItem(item)); }}
                  >
                    <span className="editor-media-row__icon"><IconForItem item={item} size={20} /></span>
                    <span className="editor-media-row__body"><strong>{item.title}</strong><small>{item.file_name}{item.duration_seconds ? ` · ${Math.round(item.duration_seconds)} s` : ""}</small></span>
                  </button>
                );
              })}
            </div>

            <aside className="editor-media-dialog__details">
              {selected ? (
                <>
                  <div className="editor-media-dialog__preview"><IconForItem item={selected} size={40} /></div>
                  <div><strong>{selected.title}</strong><small>{selected.file_name}</small></div>
                  <fieldset>
                    <legend>Einfügen als</legend>
                    <label><input type="radio" checked={mode === "VIDEO"} disabled={modeForItem(selected) !== "VIDEO"} onChange={() => setMode("VIDEO")} /> Video mit Ton</label>
                    <label><input type="radio" checked={mode === "AUDIO"} disabled={modeForItem(selected) !== "AUDIO"} onChange={() => setMode("AUDIO")} /> Nur Audio</label>
                    <label><input type="radio" checked={mode === "IMAGE"} disabled={modeForItem(selected) !== "IMAGE"} onChange={() => setMode("IMAGE")} /> Bild</label>
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
