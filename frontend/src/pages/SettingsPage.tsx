import { useEffect, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { ChevronRight, Cpu, HardDrive, Mic2, Server, SlidersHorizontal, Tv } from "lucide-react";
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

function Switch({ checked, onChange, ariaLabel }: { checked: boolean; onChange: (next: boolean) => void; ariaLabel: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={ariaLabel} className={`switch${checked ? " is-on" : ""}`} onClick={() => onChange(!checked)}>
      <span className="switch__thumb" aria-hidden="true" />
    </button>
  );
}

function SettingsLink({ to, icon, title, description }: { to: string; icon: ReactNode; title: string; description: string }) {
  return (
    <Link className="settings-link-card" to={to}>
      <span className="settings-link-card__icon">{icon}</span>
      <span><strong>{title}</strong><small>{description}</small></span>
      <ChevronRight size={17} />
    </Link>
  );
}

export function SettingsPage() {
  const store = useUIStore();
  const toast = useToast();
  const backend = useBackendStatus();
  const { register, watch, reset, setValue, handleSubmit, formState } = useForm<SettingsFormValues>({
    resolver: zodResolver(settingsSchema),
    defaultValues: {
      sidebarCollapsed: store.sidebarCollapsed,
      use24HourFormat: store.use24HourFormat,
      autoplayAfterRecord: store.autoplayAfterRecord,
      confirmDelete: store.confirmDelete,
    },
  });

  useEffect(() => {
    reset({
      sidebarCollapsed: store.sidebarCollapsed,
      use24HourFormat: store.use24HourFormat,
      autoplayAfterRecord: store.autoplayAfterRecord,
      confirmDelete: store.confirmDelete,
    });
  }, [store.sidebarCollapsed, store.use24HourFormat, store.autoplayAfterRecord, store.confirmDelete, reset]);

  const values = watch();
  const backendUrl = typeof window !== "undefined" ? `${window.location.origin}/api` : "/api";

  return (
    <div className="page settings-page-v2">
      <section className="settings-shortcuts">
        <SettingsLink to="/twitch-profiles" icon={<Tv size={19} />} title="Twitch-Profile" description="Kanäle, Synchronisierung und VOD-Quellen." />
        <SettingsLink to="/voice-profiles" icon={<Mic2 size={19} />} title="Voice-Profile" description="Referenzaufnahmen und Stimmen verwalten." />
        <a className="settings-link-card" href="#systemstatus">
          <span className="settings-link-card__icon"><Server size={19} /></span>
          <span><strong>Systemstatus</strong><small>Backend, Speicher und Laufzeit prüfen.</small></span>
          <ChevronRight size={17} />
        </a>
      </section>

      <div className="settings-columns">
        <form className="form" onSubmit={handleSubmit((data) => { store.setSettings(data); toast.show({ title: "Einstellungen gespeichert", variant: "success" }); })}>
          <Card title="Allgemein">
            <div className="settings-section-lead"><SlidersHorizontal size={17} /><span>Darstellung und Standardverhalten der Oberfläche.</span></div>
            {[
              { key: "sidebarCollapsed" as const, title: "Sidebar beim Start einklappen", hint: "Startet mit der kompakten Navigation." },
              { key: "use24HourFormat" as const, title: "24-Stunden-Zeitformat", hint: "Zeigt Uhrzeiten ohne AM/PM." },
              { key: "autoplayAfterRecord" as const, title: "Neue Aufnahme automatisch abspielen", hint: "Spielt eine Aufnahme nach erfolgreicher Konvertierung ab." },
              { key: "confirmDelete" as const, title: "Löschen immer bestätigen", hint: "Zeigt vor destruktiven Aktionen eine Sicherheitsabfrage." },
            ].map((option) => (
              <div className="form-row" key={option.key}>
                <div className="form-row__label"><span className="form-row__label-text">{option.title}</span><span className="form-row__hint">{option.hint}</span></div>
                <div className="form-row__control">
                  <Switch checked={values[option.key]} onChange={(value) => setValue(option.key, value, { shouldDirty: true })} ariaLabel={option.title} />
                  <input type="hidden" {...register(option.key)} />
                </div>
              </div>
            ))}
            <div className="form-actions"><Button type="button" variant="ghost" onClick={() => reset()} disabled={!formState.isDirty}>Verwerfen</Button><Button type="submit" variant="primary" disabled={!formState.isDirty}>Speichern</Button></div>
          </Card>
        </form>

        <Card title="Modelle und Verarbeitung">
          <div className="settings-section-lead"><Cpu size={17} /><span>Modelle werden zentral vom Backend geladen. Schnellwerkzeuge zeigen nur sinnvolle Qualitätsstufen.</span></div>
          <div className="info-row"><span className="info-row__label">GPU-Worker</span><span className="info-row__value">Gemeinsam, ein schwerer Job gleichzeitig</span></div>
          <div className="info-row"><span className="info-row__label">Modellauswahl</span><span className="info-row__value">Backend-Konfiguration</span></div>
          <div className="info-row"><span className="info-row__label">Temporäre Ergebnisse</span><span className="info-row__value">Automatische Aufbewahrungsfrist</span></div>
          <p className="settings-note">Modell-IDs und Dateipfade werden nicht als normale Benutzeroptionen angeboten. Sie bleiben in der Serverkonfiguration.</p>
        </Card>
      </div>

      <Card title="Systemstatus" className="system-status-card">
        <div id="systemstatus" className="system-status-grid">
          <div><Server size={17} /><span>Backend</span><strong>{backend.status === "online" ? "Online" : backend.status === "offline" ? "Offline" : "Verbindung …"}</strong></div>
          <div><HardDrive size={17} /><span>Freier Speicher</span><strong>{backend.data ? formatBytes(backend.data.storage.free_bytes) : "—"}</strong></div>
          <div><Cpu size={17} /><span>Version</span><strong>{backend.data?.version ?? "—"}</strong></div>
        </div>
        <div className="system-address"><span>API</span><code>{backendUrl}</code></div>
      </Card>
    </div>
  );
}
