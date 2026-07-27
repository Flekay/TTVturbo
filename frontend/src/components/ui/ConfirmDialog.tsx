import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog";
import { useEffect, useRef, type ReactNode } from "react";
import { Button } from "../ui/Button";

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel?: () => void;
  busy?: boolean;
  destructive?: boolean;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Bestätigen",
  cancelLabel = "Abbrechen",
  onConfirm,
  onCancel,
  busy,
  destructive = true,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  // Close the dialog (parent-controlled) and run the optional cancel hook.
  // Both the cancel button, escape and overlay click route through here so
  // the dialog always actually closes — previously only onCancel ran and
  // the parent's `open` state was never reset, leaving the dialog stuck.
  const dismiss = () => {
    if (busy) return;
    onCancel?.();
    onOpenChange(false);
  };

  // When the dialog opens, move focus to the safe (cancel) action so that an
  // accidental Enter keypress cannot trigger the destructive action.
  useEffect(() => {
    if (!open) return;
    const id = window.setTimeout(() => {
      cancelRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(id);
  }, [open]);

  return (
    <AlertDialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialogPrimitive.Portal>
        <div className="dialog-root">
          <AlertDialogPrimitive.Overlay
            className="dialog-overlay"
            // AlertDialog does not dismiss on overlay click by default (it is
            // a "you must choose" dialog). The user explicitly wants overlay
            // click to cancel, so handle it here.
            onPointerDown={(e) => {
              if (e.target === e.currentTarget) dismiss();
            }}
          />
          <AlertDialogPrimitive.Content
            className="dialog"
            onEscapeKeyDown={(e) => {
              // Escape cancels (safe default); never confirm on escape.
              e.preventDefault();
              dismiss();
            }}
            onOpenAutoFocus={(e) => {
              // Radix would focus the first focusable element; we explicitly
              // focus the cancel button below via effect, so suppress the default
              // to avoid a focus jump.
              e.preventDefault();
            }}
          >
            <AlertDialogPrimitive.Title className="dialog__title">
              {title}
            </AlertDialogPrimitive.Title>
            <AlertDialogPrimitive.Description className="dialog__description">
              {description}
            </AlertDialogPrimitive.Description>
            <div className="dialog__actions">
              <Button
                ref={cancelRef}
                variant="secondary"
                onClick={dismiss}
                disabled={busy}
              >
                {cancelLabel}
              </Button>
              <Button
                variant={destructive ? "danger" : "primary"}
                onClick={() => {
                  if (!busy) onConfirm();
                }}
                loading={busy}
              >
                {confirmLabel}
              </Button>
            </div>
          </AlertDialogPrimitive.Content>
        </div>
      </AlertDialogPrimitive.Portal>
    </AlertDialogPrimitive.Root>
  );
}
