import { ConfirmDialog } from "../ui/ConfirmDialog";
import type { GenerationMetadata } from "../../types/voiceClone";

function formatCreatedAt(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "…";
}

interface GenerationDeleteDialogProps {
  open: boolean;
  generation: GenerationMetadata | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function GenerationDeleteDialog({
  open,
  generation,
  busy,
  onConfirm,
  onCancel,
}: GenerationDeleteDialogProps) {
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => (next ? undefined : onCancel())}
      title="Generierung endgültig löschen?"
      description={
        generation ? (
          <span style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span>
              Generation:{" "}
              <code style={{ fontFamily: "var(--font-mono)" }}>
                {generation.id.slice(0, 12)}
              </code>
            </span>
            <span>
              Zieltext: <span title={generation.target_text}>„{truncate(generation.target_text, 80)}“</span>
            </span>
            <span>Erstellt: {formatCreatedAt(generation.created_at)}</span>
            <span style={{ color: "var(--color-text-muted)", fontSize: 12 }}>
              Die Generierung und ihre Audiodatei werden unwiderruflich gelöscht. Dieser Vorgang
              kann nicht rückgängig gemacht werden.
            </span>
          </span>
        ) : (
          ""
        )
      }
      confirmLabel="Endgültig löschen"
      cancelLabel="Abbrechen"
      onConfirm={onConfirm}
      onCancel={onCancel}
      busy={busy}
      destructive
    />
  );
}
