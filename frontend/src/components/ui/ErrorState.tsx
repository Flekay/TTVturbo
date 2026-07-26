import type { ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "./Button";

interface ErrorStateProps {
  title?: string;
  message?: ReactNode;
  onRetry?: () => void;
}

export function ErrorState({
  title = "Etwas ist schiefgelaufen",
  message,
  onRetry,
}: ErrorStateProps) {
  return (
    <div className="state state--error" role="alert">
      <div className="state__icon">
        <AlertTriangle />
      </div>
      <div className="state__title">{title}</div>
      {message && <div className="state__description">{message}</div>}
      {onRetry && (
        <div>
          <Button variant="secondary" onClick={onRetry}>
            <RefreshCw size={16} />
            Erneut versuchen
          </Button>
        </div>
      )}
    </div>
  );
}
