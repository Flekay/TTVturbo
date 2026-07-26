import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

export type ToastVariant = "success" | "error" | "info";

export interface ToastMessage {
  id: number;
  title: string;
  description?: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  toasts: ToastMessage[];
  show: (toast: Omit<ToastMessage, "id">) => void;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const show = useCallback((toast: Omit<ToastMessage, "id">) => {
    const id = nextId++;
    setToasts((list) => [...list, { ...toast, id }]);
    window.setTimeout(() => dismiss(id), 4000);
  }, [dismiss]);

  return (
    <ToastContext.Provider value={{ toasts, show, dismiss }}>
      {children}
      <div className="toast-viewport" role="region" aria-label="Benachrichtigungen">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`toast toast--${t.variant}`}
            role={t.variant === "error" ? "alert" : "status"}
          >
            <div className="toast__title">{t.title}</div>
            {t.description && <div className="toast__description">{t.description}</div>}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return ctx;
}
