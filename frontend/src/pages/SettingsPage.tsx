import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useUIStore } from "../stores/uiStore";
import { useBackendStatus } from "../hooks/useBackendStatus";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { useToast } from "../components/ui/ToastProvider";
import { formatBytes } from "../utils/format";

const settingsSchema = z.object({
  sidebarCollapsed: z.boolean(),
  use24HourFormat: z.boolean(),
  autoplayAfterRecord: z.boolean(),
  confirmDelete: z.boolean(),
});

type SettingsFormValues = z.infer<typeof settingsSchema>;

function Switch({
  checked,
  onChange,
  ariaLabel,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      className={`switch${checked ? " is-on" : ""}`}
      onClick={() => onChange(!checked)}
    >
      <span className="switch__thumb" aria-hidden="true" />
    </button>
  );
}

export function SettingsPage() {
  const store = useUIStore();
  const toast = useToast();
  const backend = useBackendStatus();

  const { register, watch, reset, setValue, handleSubmit, formState } =
    useForm<SettingsFormValues>({
      resolver: zodResolver(settingsSchema),
      defaultValues: {
        sidebarCollapsed: store.sidebarCollapsed,
        use24HourFormat: store.use24HourFormat,
        autoplayAfterRecord: store.autoplayAfterRecord,
        confirmDelete: store.confirmDelete,
      },
    });

  // Keep the form in sync if the store changes elsewhere.
  useEffect(() => {
    reset({
      sidebarCollapsed: store.sidebarCollapsed,
      use24HourFormat: store.use24HourFormat,
      autoplayAfterRecord: store.autoplayAfterRecord,
      confirmDelete: store.confirmDelete,
    });
  }, [store.sidebarCollapsed, store.use24HourFormat, store.autoplayAfterRecord, store.confirmDelete, reset]);

  const values = watch();

  const onSubmit = (data: SettingsFormValues) => {
    store.setSettings(data);
    toast.show({ title: "Einstellungen gespeichert", variant: "success" });
  };

  const browser = typeof navigator !== "undefined" ? navigator.userAgent : "—";
  const backendUrl =
    typeof window !== "undefined" ? `${window.location.origin}/api` : "/api";

  return (
    <div className="page">
      <div className="settings-page">
      <form className="form" onSubmit={handleSubmit(onSubmit)}>
        <Card>
          <h3 style={{ fontSize: 16, marginBottom: 12 }}>Allgemein</h3>

          <div className="form-row">
            <div className="form-row__label">
              <span className="form-row__label-text">Sidebar beim Start eingeklappt</span>
              <span className="form-row__hint">
                Zeigt die Sidebar beim Laden der App im eingeklappten Zustand.
              </span>
            </div>
            <div className="form-row__control">
              <Switch
                checked={values.sidebarCollapsed}
                onChange={(v) => setValue("sidebarCollapsed", v, { shouldDirty: true })}
                ariaLabel="Sidebar beim Start eingeklappt"
              />
              <input type="hidden" {...register("sidebarCollapsed")} />
            </div>
          </div>

          <div className="form-row">
            <div className="form-row__label">
              <span className="form-row__label-text">24-Stunden-Zeitformat</span>
              <span className="form-row__hint">
                Zeigt Uhrzeiten im 24-Stunden-Format anstelle von AM/PM.
              </span>
            </div>
            <div className="form-row__control">
              <Switch
                checked={values.use24HourFormat}
                onChange={(v) => setValue("use24HourFormat", v, { shouldDirty: true })}
                ariaLabel="24-Stunden-Zeitformat"
              />
              <input type="hidden" {...register("use24HourFormat")} />
            </div>
          </div>

          <div className="form-row">
            <div className="form-row__label">
              <span className="form-row__label-text">Aufnahme nach Abschluss automatisch abspielen</span>
              <span className="form-row__hint">
                Spielt eine neue Aufnahme nach erfolgreicher Konvertierung automatisch ab.
              </span>
            </div>
            <div className="form-row__control">
              <Switch
                checked={values.autoplayAfterRecord}
                onChange={(v) => setValue("autoplayAfterRecord", v, { shouldDirty: true })}
                ariaLabel="Aufnahme nach Abschluss automatisch abspielen"
              />
              <input type="hidden" {...register("autoplayAfterRecord")} />
            </div>
          </div>

          <div className="form-row" style={{ borderBottom: "none" }}>
            <div className="form-row__label">
              <span className="form-row__label-text">Löschbestätigung aktivieren</span>
              <span className="form-row__hint">
                Verlangt eine zusätzliche Bestätigung beim Löschen von Aufnahmen.
              </span>
            </div>
            <div className="form-row__control">
              <Switch
                checked={values.confirmDelete}
                onChange={(v) => setValue("confirmDelete", v, { shouldDirty: true })}
                ariaLabel="Löschbestätigung aktivieren"
              />
              <input type="hidden" {...register("confirmDelete")} />
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <Button type="submit" variant="primary" disabled={!formState.isDirty}>
              Speichern
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => reset()}
              disabled={!formState.isDirty}
            >
              Verwerfen
            </Button>
          </div>
        </Card>
      </form>

      <Card>
        <h3 style={{ fontSize: 16, marginBottom: 12 }}>Systeminformationen (schreibgeschützt)</h3>
        <div className="info-row">
          <span className="info-row__label">Backendstatus</span>
          <span className="info-row__value">
            {backend.status === "online" ? "online" : backend.status === "offline" ? "offline" : "verbinde …"}
          </span>
        </div>
        <div className="info-row">
          <span className="info-row__label">Backendadresse</span>
          <span className="info-row__value">{backendUrl}</span>
        </div>
        <div className="info-row">
          <span className="info-row__label">App-Version</span>
          <span className="info-row__value">{backend.data?.version ?? "—"}</span>
        </div>
        <div className="info-row">
          <span className="info-row__label">Browser</span>
          <span className="info-row__value" style={{ wordBreak: "break-all" }}>{browser}</span>
        </div>
        <div className="info-row" style={{ borderBottom: "none" }}>
          <span className="info-row__label">Freier Speicher</span>
          <span className="info-row__value">
            {backend.data ? formatBytes(backend.data.storage.free_bytes) : "—"}
          </span>
        </div>
      </Card>
      </div>
    </div>
  );
}
