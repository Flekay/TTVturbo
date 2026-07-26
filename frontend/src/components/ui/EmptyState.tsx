import type { ReactNode } from "react";
import { Inbox } from "lucide-react";

interface EmptyStateProps {
  title: string;
  description?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
}

export function EmptyState({ title, description, icon, action }: EmptyStateProps) {
  return (
    <div className="state" role="status">
      <div className="state__icon">{icon ?? <Inbox />}</div>
      <div className="state__title">{title}</div>
      {description && <div className="state__description">{description}</div>}
      {action && <div>{action}</div>}
    </div>
  );
}
