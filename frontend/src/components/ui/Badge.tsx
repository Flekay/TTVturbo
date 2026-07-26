import type { ReactNode } from "react";

export type BadgeVariant = "success" | "warning" | "error" | "muted" | "info";

interface BadgeProps {
  variant?: BadgeVariant;
  children: ReactNode;
  title?: string;
}

const variantClass: Record<BadgeVariant, string> = {
  success: "badge--success",
  warning: "badge--warning",
  error: "badge--error",
  muted: "badge--muted",
  info: "badge--info",
};

export function Badge({ variant = "muted", children, title }: BadgeProps) {
  return (
    <span className={`badge ${variantClass[variant]}`} title={title}>
      {children}
    </span>
  );
}
