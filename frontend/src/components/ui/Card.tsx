import type { HTMLAttributes, ReactNode } from "react";

interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title?: ReactNode;
  value?: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  padless?: boolean;
  children?: ReactNode;
}

export function Card({
  title,
  value,
  sub,
  actions,
  padless,
  className,
  children,
  ...rest
}: CardProps) {
  const classes = ["card", padless ? "card--padless" : "", className ?? ""]
    .filter(Boolean)
    .join(" ");
  const showHeader = title || actions;
  return (
    <div className={classes} {...rest}>
      {showHeader && (
        <div className="card__header">
          {title && <div className="card__title">{title}</div>}
          {actions && <div>{actions}</div>}
        </div>
      )}
      {value !== undefined && <div className="card__value">{value}</div>}
      {sub !== undefined && <div className="card__sub">{sub}</div>}
      {children}
    </div>
  );
}
