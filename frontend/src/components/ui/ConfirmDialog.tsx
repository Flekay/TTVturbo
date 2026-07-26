import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog";
import type { ReactNode } from "react";
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
  return (
    <AlertDialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialogPrimitive.Portal>
        <AlertDialogPrimitive.Overlay className="dialog-overlay" />
        <AlertDialogPrimitive.Content
          className="dialog"
          onEscapeKeyDown={(e) => {
            // Escape cancels (safe default); never confirm on escape.
            e.preventDefault();
            onCancel?.();
            onOpenChange(false);
          }}
        >
          <AlertDialogPrimitive.Title className="dialog__title">
            {title}
          </AlertDialogPrimitive.Title>
          <AlertDialogPrimitive.Description className="dialog__description">
            {description}
          </AlertDialogPrimitive.Description>
          <div className="dialog__actions">
            <AlertDialogPrimitive.Cancel asChild>
              <Button
                variant="secondary"
                onClick={() => onCancel?.()}
                disabled={busy}
              >
                {cancelLabel}
              </Button>
            </AlertDialogPrimitive.Cancel>
            <AlertDialogPrimitive.Action asChild>
              <Button
                variant={destructive ? "danger" : "primary"}
                onClick={onConfirm}
                loading={busy}
              >
                {confirmLabel}
              </Button>
            </AlertDialogPrimitive.Action>
          </div>
        </AlertDialogPrimitive.Content>
      </AlertDialogPrimitive.Portal>
    </AlertDialogPrimitive.Root>
  );
}
