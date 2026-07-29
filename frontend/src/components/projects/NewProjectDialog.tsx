import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Bookmark, Heart, Image as ImageIcon, MessageSquare, Music, Type, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "../ui/Button";

export interface NewProjectValues {
  name: string;
  sequence: {
    name: string;
    width: number;
    height: number;
    fps_numerator: number;
    fps_denominator: number;
    format_profile: "DESKTOP_16_9" | "MOBILE_9_16" | "CUSTOM";
    safe_area_enabled: boolean;
    safe_area_margin_top: number;
    safe_area_margin_right: number;
    safe_area_margin_bottom: number;
    safe_area_margin_left: number;
  };
}

interface NewProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (values: NewProjectValues) => Promise<void> | void;
  busy?: boolean;
}

type PresetId = "desktop" | "mobile" | "custom";

interface PresetConfig {
  width: number;
  height: number;
  profile: NewProjectValues["sequence"]["format_profile"];
  safe: { top: number; right: number; bottom: number; left: number };
}

// Desktop: top bar + fullscreen title (~120px), bottom player controls (~120px).
// No side overlays on desktop players, so left/right stay at 0.
// Mobile safe zones cover TikTok, Instagram Reels and YouTube Shorts.
// Top: Reels top bar (~250px). Right: action button column (~160px).
// Bottom: caption + music + YT Shorts description (~340px). Left: 0.
const PRESETS: Record<Exclude<PresetId, "custom">, PresetConfig> = {
  desktop: { width: 1920, height: 1080, profile: "DESKTOP_16_9", safe: { top: 120, right: 0, bottom: 120, left: 0 } },
  mobile: { width: 1080, height: 1920, profile: "MOBILE_9_16", safe: { top: 250, right: 160, bottom: 340, left: 0 } },
};

export function NewProjectDialog({ open, onOpenChange, onCreate, busy = false }: NewProjectDialogProps) {
  const nameRef = useRef<HTMLInputElement | null>(null);
  const [preset, setPreset] = useState<PresetId>("desktop");
  const [name, setName] = useState("Untitled Project");
  const [width, setWidth] = useState(1920);
  const [height, setHeight] = useState(1080);
  const [fps, setFps] = useState(60);
  const [safeArea, setSafeArea] = useState(true);
  const [safeTop, setSafeTop] = useState(80);
  const [safeRight, setSafeRight] = useState(80);
  const [safeBottom, setSafeBottom] = useState(80);
  const [safeLeft, setSafeLeft] = useState(80);

  useEffect(() => {
    if (!open) return;
    setPreset("desktop");
    setName("Untitled Project");
    setWidth(1920);
    setHeight(1080);
    setFps(60);
    setSafeArea(true);
    setSafeTop(80); setSafeRight(80); setSafeBottom(80); setSafeLeft(80);
    const timer = window.setTimeout(() => {
      nameRef.current?.focus();
      nameRef.current?.select();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open]);

  const validationError = useMemo(() => {
    if (!name.trim()) return "Projektname fehlt.";
    if (!Number.isInteger(width) || width < 64 || width > 7680) return "Breite muss zwischen 64 und 7680 Pixel liegen.";
    if (!Number.isInteger(height) || height < 64 || height > 7680) return "Höhe muss zwischen 64 und 7680 Pixel liegen.";
    if (!Number.isInteger(fps) || fps < 1 || fps > 240) return "FPS muss zwischen 1 und 240 liegen.";
    if (safeArea) {
      const maxH = Math.floor(width / 2);
      const maxV = Math.floor(height / 2);
      const sides: Array<[number, string, number]> = [
        [safeTop, "Oben", maxV], [safeRight, "Rechts", maxH],
        [safeBottom, "Unten", maxV], [safeLeft, "Links", maxH],
      ];
      for (const [value, label, maximum] of sides) {
        if (!Number.isInteger(value) || value < 0 || value > maximum) return `Safe-Area ${label} muss zwischen 0 und ${maximum} px liegen.`;
      }
    }
    return null;
  }, [fps, height, name, safeArea, safeBottom, safeLeft, safeRight, safeTop, width]);

  function choosePreset(next: PresetId) {
    setPreset(next);
    if (next === "custom") return;
    const value = PRESETS[next];
    setWidth(value.width);
    setHeight(value.height);
    setSafeTop(value.safe.top);
    setSafeRight(value.safe.right);
    setSafeBottom(value.safe.bottom);
    setSafeLeft(value.safe.left);
  }

  function setSafeSide(setter: (v: number) => void, value: number) {
    setPreset("custom");
    setter(value);
  }

  async function submit() {
    if (validationError || busy) return;
    const profile = preset === "custom" ? "CUSTOM" : PRESETS[preset].profile;
    const sequenceName = preset === "desktop" ? "Desktop" : preset === "mobile" ? "Mobile" : "Custom";
    await onCreate({
      name: name.trim(),
      sequence: {
        name: sequenceName,
        width,
        height,
        fps_numerator: fps,
        fps_denominator: 1,
        format_profile: profile,
        safe_area_enabled: safeArea,
        safe_area_margin_top: safeTop,
        safe_area_margin_right: safeRight,
        safe_area_margin_bottom: safeBottom,
        safe_area_margin_left: safeLeft,
      },
    });
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => { if (!busy) onOpenChange(next); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="new-project-dialog__overlay" />
        <DialogPrimitive.Content
          className="new-project-dialog"
          onOpenAutoFocus={(event) => event.preventDefault()}
          onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}
        >
          <div className="new-project-dialog__header">
            <div>
              <DialogPrimitive.Title className="new-project-dialog__title">Neues Projekt</DialogPrimitive.Title>
              <DialogPrimitive.Description className="new-project-dialog__description">Leeres Bearbeitungsprojekt mit einem festen Ausgabeformat erstellen.</DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="icon" aria-label="Dialog schließen" disabled={busy}><X size={18} /></Button>
            </DialogPrimitive.Close>
          </div>

          <div className="new-project-dialog__body">
            <section className="new-project-presets" aria-label="Projektformat auswählen">
              <h3>Projektformat</h3>
              <div className="new-project-presets__grid">
                <button type="button" className={`new-project-preset${preset === "desktop" ? " is-selected" : ""}`} onClick={() => choosePreset("desktop")}>
                  <span className="new-project-preset__preview new-project-preset__preview--desktop">
                    <span className="preset-mockup preset-mockup--desktop">
                      <span className="preset-mockup__bar" />
                      <span className="preset-mockup__skeleton"><ImageIcon size={18} /></span>
                      <span className="preset-mockup__controls" />
                    </span>
                  </span>
                  <strong>Desktop</strong>
                  <small>16:9 · 1920 × 1080</small>
                </button>
                <button type="button" className={`new-project-preset${preset === "mobile" ? " is-selected" : ""}`} onClick={() => choosePreset("mobile")}>
                  <span className="new-project-preset__preview new-project-preset__preview--mobile">
                    <span className="preset-mockup preset-mockup--mobile">
                      <span className="preset-mockup__profile" />
                      <span className="preset-mockup__username"><Type size={8} /></span>
                      <span className="preset-mockup__skeleton"><ImageIcon size={18} /></span>
                      <span className="preset-mockup__actions">
                        <Heart size={12} /><MessageSquare size={12} /><Bookmark size={12} />
                      </span>
                      <span className="preset-mockup__caption" />
                      <span className="preset-mockup__music"><Music size={8} /></span>
                    </span>
                  </span>
                  <strong>Mobile</strong>
                  <small>9:16 · 1080 × 1920</small>
                </button>
                <button type="button" className={`new-project-preset${preset === "custom" ? " is-selected" : ""}`} onClick={() => choosePreset("custom")}>
                  <span className="new-project-preset__preview new-project-preset__preview--custom">
                    <span className="preset-mockup preset-mockup--custom" />
                  </span>
                  <strong>Custom</strong>
                  <small>Eigene Auflösung</small>
                </button>
              </div>
              <div className="new-project-dialog__empty-note">Das Projekt startet leer. Medien werden erst im Editor hinzugefügt.</div>
            </section>

            <aside className="new-project-details">
              <h3>Projektdetails</h3>
              <label>
                Projektname
                <input ref={nameRef} className="input" value={name} onChange={(event) => setName(event.target.value)} maxLength={120} />
              </label>
              <div className="new-project-details__dimensions">
                <label>
                  Breite
                  <input className="input" type="number" min={64} max={7680} step={2} value={width} onChange={(event) => { setWidth(Number(event.target.value)); setPreset("custom"); }} />
                </label>
                <label>
                  Höhe
                  <input className="input" type="number" min={64} max={7680} step={2} value={height} onChange={(event) => { setHeight(Number(event.target.value)); setPreset("custom"); }} />
                </label>
              </div>
              <label>
                Bildrate
                <select className="input" value={fps} onChange={(event) => setFps(Number(event.target.value))}>
                  <option value={24}>24 FPS</option>
                  <option value={25}>25 FPS</option>
                  <option value={30}>30 FPS</option>
                  <option value={50}>50 FPS</option>
                  <option value={60}>60 FPS</option>
                </select>
              </label>
              <div className="new-project-details__safe-area">
                <label className="new-project-details__safe-area-toggle">
                  <input type="checkbox" checked={safeArea} onChange={(event) => { setSafeArea(event.target.checked); setPreset("custom"); }} />
                  Safe Area anzeigen
                </label>
                <div className="new-project-details__safe-area-sides">
                  <label>Oben
                    <input className="input" type="number" min={0} max={Math.floor(height / 2)} step={1} value={safeTop} onChange={(event) => setSafeSide(setSafeTop, Number(event.target.value))} disabled={!safeArea} />
                    px
                  </label>
                  <label>Rechts
                    <input className="input" type="number" min={0} max={Math.floor(width / 2)} step={1} value={safeRight} onChange={(event) => setSafeSide(setSafeRight, Number(event.target.value))} disabled={!safeArea} />
                    px
                  </label>
                  <label>Unten
                    <input className="input" type="number" min={0} max={Math.floor(height / 2)} step={1} value={safeBottom} onChange={(event) => setSafeSide(setSafeBottom, Number(event.target.value))} disabled={!safeArea} />
                    px
                  </label>
                  <label>Links
                    <input className="input" type="number" min={0} max={Math.floor(width / 2)} step={1} value={safeLeft} onChange={(event) => setSafeSide(setSafeLeft, Number(event.target.value))} disabled={!safeArea} />
                    px
                  </label>
                </div>
              </div>
              <div className="new-project-details__summary">
                <span>Ausgabe</span>
                <strong>{width || 0} × {height || 0} · {fps || 0} FPS</strong>
              </div>
              {validationError && <p className="new-project-details__error" role="alert">{validationError}</p>}
            </aside>
          </div>

          <div className="new-project-dialog__footer">
            <DialogPrimitive.Close asChild><Button variant="secondary" disabled={busy}>Abbrechen</Button></DialogPrimitive.Close>
            <Button variant="primary" loading={busy} disabled={Boolean(validationError)} onClick={() => void submit()}>Projekt erstellen</Button>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
