import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "icon";
export type ButtonSize = "md" | "sm";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  children?: ReactNode;
}

const variantClass: Record<ButtonVariant, string> = {
  primary: "btn--primary",
  secondary: "btn--secondary",
  ghost: "btn--ghost",
  danger: "btn--danger",
  icon: "btn--icon",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading = false, className, children, disabled, ...rest },
  ref,
) {
  const classes = [
    "btn",
    variantClass[variant],
    size === "sm" ? "btn--sm" : "",
    loading ? "btn--loading" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button
      ref={ref}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {children}
    </button>
  );
});
