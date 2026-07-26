import type { ReactNode } from "react";

interface LoadingStateProps {
  message?: ReactNode;
}

export function LoadingState({ message = "Wird geladen …" }: LoadingStateProps) {
  return (
    <div className="state" role="status" aria-live="polite">
      <div className="spinner" aria-hidden="true" />
      <div className="state__title">{message}</div>
    </div>
  );
}
