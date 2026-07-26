import { ConfirmDialog } from "../ui/ConfirmDialog";
import type { Recording } from "../../types/recording";

interface RecordingDeleteDialogProps {
  open: boolean;
  recording: Recording | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function RecordingDeleteDialog({
  open,
  recording,
  busy,
  onConfirm,
  onCancel,
}: RecordingDeleteDialogProps) {
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => (next ? undefined : onCancel())}
      title="Aufnahme endgültig löschen?"
      description={
        recording ? (
          <>
            Die Datei <code style={{ fontFamily: "var(--font-mono)" }}>{recording.filename}</code>{" "}
            wird unwiderruflich gelöscht. Dieser Vorgang kann nicht rückgängig gemacht werden.
          </>
        ) : (
          ""
        )
      }
      confirmLabel="Löschen"
      cancelLabel="Abbrechen"
      onConfirm={onConfirm}
      onCancel={onCancel}
      busy={busy}
      destructive
    />
  );
}
