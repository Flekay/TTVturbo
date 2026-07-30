import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  Download,
  ExternalLink,
  FileVideo,
  Image as ImageIcon,
  Loader2,
  Save,
  Scissors,
  Settings2,
  Sparkles,
  Trash2,
  Upload,
  WandSparkles,
} from "lucide-react";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ErrorState } from "../components/ui/ErrorState";
import { useToast } from "../components/ui/ToastProvider";
import { ApiError } from "../api/client";
import { fetchLibraryItem } from "../features/library/api";
import type { LibraryItem } from "../features/library/schemas";
import {
  deleteTemporaryItem,
  libraryFileUrl,
  type CapabilityJob,
  type QuickToolId,
} from "../features/capabilities/api";
import {
  useCancelCapabilityJob,
  useCapabilityInfo,
  useCapabilityJob,
  useCapabilityStatus,
  usePromoteItem,
  useRetryCapabilityJob,
  useStartCapabilityJob,
  useUploadTemporaryMedia,
} from "../features/capabilities/hooks";
import { createProject } from "../features/projects/api";
import { RegionPicker, type NormalizedRegion } from "../features/videoCut";

interface ToolDefinition {
  id: QuickToolId;
  title: string;
  description: string;
  accept?: string;
  fileLabel?: string;
  needsSource: boolean;
}

const DEFINITIONS: Record<QuickToolId, ToolDefinition> = {
  "video-upscale": {
    id: "video-upscale",
    title: "Video hochskalieren",
    description: "Erhöhe die Auflösung eines einzelnen Videos. Quelle und Ergebnis bleiben zunächst temporär.",
    accept: "video/*",
    fileLabel: "Video auswählen",
    needsSource: true,
  },
  "video-background-removal": {
    id: "video-background-removal",
    title: "Hintergrund entfernen",
    description: "Stelle den Vordergrund frei oder setze einen neuen Hintergrund ein.",
    accept: "video/*",
    fileLabel: "Video auswählen",
    needsSource: true,
  },
  "video-text-edit": {
    id: "video-text-edit",
    title: "Video per Text bearbeiten",
    description: "Verändere das vollständige Video oder einen definierten Bereich mit einer Textanweisung.",
    accept: "video/*",
    fileLabel: "Video auswählen",
    needsSource: true,
  },
  "video-generation": {
    id: "video-generation",
    title: "Video generieren",
    description: "Erzeuge ein temporäres Video aus Text oder optional aus einem Referenzbild.",
    accept: "image/png,image/jpeg,image/webp,image/bmp",
    fileLabel: "Referenzbild optional",
    needsSource: false,
  },
  "video-cut": {
    id: "video-cut",
    title: "Video-Bereich ausschneiden",
    description: "Wähle einen rechteckigen Bereich aus dem Video aus und speichere ihn als eigenes Video. Audio bleibt erhalten.",
    accept: "video/*",
    fileLabel: "Video auswählen",
    needsSource: true,
  },
};

const ACTIVE = new Set(["QUEUED", "RUNNING", "RETRYING", "CANCELING"]);

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Der Vorgang ist fehlgeschlagen.";
}

function getJobErrorMessage(error: CapabilityJob["error"]): string {
  if (typeof error === "string" && error.trim()) return error;
  if (error && typeof error === "object" && typeof error.message === "string") {
    return error.message;
  }
  return "Unbekannter Fehler";
}

function outputItemIds(job: CapabilityJob | undefined): string[] {
  if (!job) return [];
  const ids = Array.isArray(job.library_item_ids)
    ? job.library_item_ids.filter((value): value is string => typeof value === "string")
    : [];
  if (typeof job.library_item_id === "string" && !ids.includes(job.library_item_id)) {
    ids.unshift(job.library_item_id);
  }
  return ids;
}

function formatStatus(job: CapabilityJob): string {
  switch (job.status) {
    case "QUEUED": return "Wartet";
    case "RUNNING": return job.current_stage ? String(job.current_stage) : "Wird verarbeitet";
    case "COMPLETED": return "Fertig";
    case "FAILED": return "Fehlgeschlagen";
    case "CANCELED": return "Abgebrochen";
    default: return job.status;
  }
}

export function QuickToolPage({ tool }: { tool: QuickToolId }) {
  const definition = DEFINITIONS[tool];
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const sourceId = searchParams.get("source");
  const autoPersist = searchParams.get("persist") === "1";

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadedSource, setUploadedSource] = useState<LibraryItem | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [promotedIds, setPromotedIds] = useState<Set<string>>(new Set());
  const [discarding, setDiscarding] = useState(false);
  const [autoPromotionFailed, setAutoPromotionFailed] = useState(false);
  const autoPromotingIds = useRef<Set<string>>(new Set());

  const [quality, setQuality] = useState("STANDARD");
  const [scale, setScale] = useState("2");
  const [targetWidth, setTargetWidth] = useState("");
  const [targetHeight, setTargetHeight] = useState("");
  const [foregroundMode, setForegroundMode] = useState("PERSON");
  const [backgroundMode, setBackgroundMode] = useState("TRANSPARENT");
  const [backgroundColor, setBackgroundColor] = useState("#111111");
  const [editMode, setEditMode] = useState("TEXT_EDIT");
  const [prompt, setPrompt] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [duration, setDuration] = useState("5");
  const [seed, setSeed] = useState("");
  const [steps, setSteps] = useState("30");
  const [guidance, setGuidance] = useState("7.5");
  const [startSeconds, setStartSeconds] = useState("0");
  const [endSeconds, setEndSeconds] = useState("");
  const [cutRegion, setCutRegion] = useState<NormalizedRegion | null>(null);

  const status = useCapabilityStatus(tool);
  const capabilityInfo = useCapabilityInfo(tool);
  const upload = useUploadTemporaryMedia();
  const start = useStartCapabilityJob(tool);
  const jobQuery = useCapabilityJob(tool, jobId);
  const promote = usePromoteItem();
  const cancel = useCancelCapabilityJob(tool);
  const retry = useRetryCapabilityJob(tool);

  const existingSource = useQuery({
    queryKey: ["library", "item", sourceId],
    queryFn: () => fetchLibraryItem(sourceId!),
    enabled: Boolean(sourceId),
  });
  const source = existingSource.data ?? uploadedSource;
  const job = jobQuery.data;
  const resultIds = useMemo(() => outputItemIds(job), [job]);
  const resultKey = resultIds.join("|");

  useEffect(() => {
    if (!autoPersist || job?.status !== "COMPLETED" || resultIds.length === 0) return;
    const pending = resultIds.filter(
      (id) => !promotedIds.has(id) && !autoPromotingIds.current.has(id),
    );
    if (pending.length === 0) return;
    pending.forEach((id) => autoPromotingIds.current.add(id));
    setAutoPromotionFailed(false);
    void Promise.all(pending.map((id) => promote.mutateAsync(id)))
      .then(() => {
        setPromotedIds((current) => new Set([...current, ...pending]));
        toast.show({ title: "Ergebnis in der Library gespeichert", variant: "success" });
      })
      .catch((error: unknown) => {
        pending.forEach((id) => autoPromotingIds.current.delete(id));
        setAutoPromotionFailed(true);
        toast.show({ title: getErrorMessage(error), variant: "error" });
      });
  }, [autoPersist, job?.status, promotedIds, resultKey, toast]);

  const busy = upload.isPending || start.isPending || (job ? ACTIVE.has(job.status) : false);
  const statusUnavailable = status.data && !status.data.available;

  async function ensureSource(): Promise<LibraryItem | null> {
    if (!definition.needsSource && !selectedFile) return null;
    if (source) return source;
    if (!selectedFile) throw new Error(definition.needsSource ? "Wähle zuerst eine Datei aus." : "");
    const item = await upload.mutateAsync(selectedFile);
    setUploadedSource(item);
    return item;
  }

  function commonTimeRange() {
    const startUs = Math.max(0, Number(startSeconds || 0)) * 1_000_000;
    const endValue = endSeconds.trim() ? Number(endSeconds) * 1_000_000 : undefined;
    return {
      start_us: Math.round(startUs),
      ...(endValue && endValue > startUs ? { end_us: Math.round(endValue) } : {}),
    };
  }

  async function handleStart() {
    try {
      const input = await ensureSource();
      let payload: Record<string, unknown>;
      if (tool === "video-upscale") {
        const custom = targetWidth.trim() && targetHeight.trim();
        const engine = quality === "FAST" ? "LANCZOS" : quality === "HIGH" ? "REALESRGAN" : "AUTO";
        payload = {
          media_item_id: input!.id,
          output_lifecycle: "TEMPORARY",
          ...commonTimeRange(),
          options: {
            scale: custom ? null : Number(scale),
            ...(custom ? { target_width: Number(targetWidth), target_height: Number(targetHeight) } : {}),
            denoise: quality === "HIGH",
            deblock: quality === "HIGH",
            preserve_audio: true,
            engine,
            quality: "FINAL",
          },
        };
      } else if (tool === "video-background-removal") {
        const composite = backgroundMode !== "TRANSPARENT";
        payload = {
          media_item_id: input!.id,
          output_lifecycle: "TEMPORARY",
          ...commonTimeRange(),
          mode: foregroundMode,
          output_modes: [composite ? "COMPOSITED_VIDEO" : "TRANSPARENT_VIDEO"],
          background: {
            mode: backgroundMode,
            color: backgroundColor,
            blur_radius: 18,
          },
          temporal_smoothing: quality === "FAST" ? 0.45 : quality === "HIGH" ? 0.82 : 0.68,
          edge_refinement: quality !== "FAST",
          preserve_audio: true,
        };
      } else if (tool === "video-text-edit") {
        if (!prompt.trim()) throw new Error("Eine Bearbeitungsanweisung ist erforderlich.");
        payload = {
          media_item_id: input!.id,
          output_lifecycle: "TEMPORARY",
          ...commonTimeRange(),
          mode: editMode,
          prompt: prompt.trim(),
          negative_prompt: negativePrompt.trim(),
          mask_mode: "FULL_FRAME",
          options: {
            num_inference_steps: Number(steps),
            guidance_scale: Number(guidance),
            image_guidance_scale: 1.5,
            strength: 0.85,
            temporal_consistency: quality === "FAST" ? 0.1 : quality === "HIGH" ? 0.35 : 0.2,
            ...(seed.trim() ? { seed: Number(seed) } : {}),
            quality: quality === "FAST" ? "PREVIEW" : "FINAL",
            preserve_audio: true,
          },
        };
      } else if (tool === "video-cut") {
        if (!cutRegion) throw new Error("Wähle zuerst einen Bereich im Video aus.");
        payload = {
          media_item_id: input!.id,
          output_lifecycle: "TEMPORARY",
          ...commonTimeRange(),
          region: cutRegion,
          options: {
            preserve_audio: true,
            quality: quality === "FAST" ? "PREVIEW" : "FINAL",
          },
        };
      } else {
        if (!prompt.trim()) throw new Error("Ein Prompt ist erforderlich.");
        payload = {
          type: input ? "IMAGE_TO_VIDEO" : "TEXT_TO_VIDEO",
          prompt: prompt.trim(),
          source_image_asset_id: input?.id ?? null,
          duration_seconds: Number(duration),
          aspect_ratio: aspectRatio,
          ...(seed.trim() ? { seed: Number(seed) } : {}),
          output_lifecycle: "TEMPORARY",
          options: {
            num_inference_steps: Number(steps),
            guidance_scale: Number(guidance),
            negative_prompt: negativePrompt.trim(),
          },
        };
      }
      const created = await start.mutateAsync(payload);
      setJobId(created.id);
    } catch (error) {
      toast.show({ title: getErrorMessage(error), variant: "error" });
    }
  }

  async function handlePromote(itemId: string): Promise<boolean> {
    try {
      await promote.mutateAsync(itemId);
      setPromotedIds((current) => new Set(current).add(itemId));
      toast.show({ title: "In der Library gespeichert", variant: "success" });
      return true;
    } catch (error) {
      toast.show({ title: getErrorMessage(error), variant: "error" });
      return false;
    }
  }

  async function handleOpenEditor(itemId: string) {
    try {
      const isPersistent = promotedIds.has(itemId) || await handlePromote(itemId);
      if (!isPersistent) return;
      const project = await createProject({
        name: `${definition.title} – ${new Date().toLocaleDateString("de-DE")}`,
        sources: [{ media_item_id: itemId }],
      });
      navigate(`/projects/${project.id}`);
    } catch (error) {
      toast.show({ title: getErrorMessage(error), variant: "error" });
    }
  }

  async function handleDiscard() {
    setDiscarding(true);
    try {
      const ids = [...resultIds];
      if (uploadedSource?.id) ids.push(uploadedSource.id);
      await Promise.all(ids.filter((id) => !promotedIds.has(id)).map((id) => deleteTemporaryItem(id)));
      navigate("/create");
    } catch (error) {
      toast.show({ title: getErrorMessage(error), variant: "error" });
    } finally {
      setDiscarding(false);
    }
  }

  if (existingSource.isError) {
    return <ErrorState title="Quelle konnte nicht geladen werden" message={getErrorMessage(existingSource.error)} />;
  }

  return (
    <div className="page quick-tool-page">
      <Link className="back-link" to="/create"><ArrowLeft size={15} /> Zurück zu Create</Link>

      <div className="quick-tool-layout">
        <section className="quick-tool-main">
          <Card className="quick-tool-card">
            <div className="quick-tool-heading">
              <div className="quick-tool-heading__icon"><WandSparkles size={22} /></div>
              <div>
                <h1>{definition.title}</h1>
                <p>{definition.description}</p>
              </div>
            </div>

            {statusUnavailable && (
              <div className="inline-problem" role="alert">
                <strong>Dieses Werkzeug ist aktuell nicht verfügbar.</strong>
                <span>{status.data?.reasons?.join(" · ") || status.data?.error || "Backend-Konfiguration prüfen."}</span>
              </div>
            )}

            {tool !== "video-generation" || selectedFile || !job ? (
              <div className="quick-tool-section">
                <div className="section-label">{definition.fileLabel}</div>
                {source ? (
                  <div className="selected-source">
                    {tool === "video-generation" ? <ImageIcon size={22} /> : <FileVideo size={22} />}
                    <div><strong>{source.title}</strong><span>{source.lifecycle === "TEMPORARY" ? "Temporäre Quelle" : "Aus der Library"}</span></div>
                    {!sourceId && !busy && <Button variant="ghost" size="sm" onClick={() => { setUploadedSource(null); setSelectedFile(null); }}>Ändern</Button>}
                  </div>
                ) : selectedFile ? (
                  <div className="selected-source">
                    {tool === "video-generation" ? <ImageIcon size={22} /> : <FileVideo size={22} />}
                    <div><strong>{selectedFile.name}</strong><span>{Math.round(selectedFile.size / 1024 / 1024 * 10) / 10} MB · wird temporär hochgeladen</span></div>
                    {!busy && <Button variant="ghost" size="sm" onClick={() => setSelectedFile(null)}>Entfernen</Button>}
                  </div>
                ) : (
                  <button className="file-drop" type="button" onClick={() => fileInputRef.current?.click()}>
                    <Upload size={24} />
                    <strong>{definition.needsSource ? "Datei auswählen" : "Referenzbild hinzufügen"}</strong>
                    <span>{definition.needsSource ? "Die Datei erscheint nicht automatisch in der Library." : "Optional für Image-to-Video."}</span>
                  </button>
                )}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={definition.accept}
                  hidden
                  onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
                />
              </div>
            ) : null}

            <div className="quick-tool-section">
              {tool === "video-upscale" && (
                <>
                  <div className="section-label">Ausgabe</div>
                  <div className="segmented-control">
                    {["2", "4"].map((value) => <button key={value} type="button" className={scale === value ? "is-active" : ""} onClick={() => { setScale(value); setTargetWidth(""); setTargetHeight(""); }}>{value}×</button>)}
                    <button type="button" className={targetWidth || targetHeight ? "is-active" : ""} onClick={() => { setTargetWidth("1920"); setTargetHeight("1080"); }}>Custom</button>
                  </div>
                  {(targetWidth || targetHeight) && <div className="field-grid field-grid--2"><label>Breite<input className="input" type="number" value={targetWidth} onChange={(e) => setTargetWidth(e.target.value)} /></label><label>Höhe<input className="input" type="number" value={targetHeight} onChange={(e) => setTargetHeight(e.target.value)} /></label></div>}
                </>
              )}

              {tool === "video-background-removal" && (
                <>
                  <div className="section-label">Vordergrund</div>
                  <div className="segmented-control"><button type="button" className={foregroundMode === "PERSON" ? "is-active" : ""} onClick={() => setForegroundMode("PERSON")}>Person</button><button type="button" className={foregroundMode === "AUTO_FOREGROUND" ? "is-active" : ""} onClick={() => setForegroundMode("AUTO_FOREGROUND")}>Automatisch</button></div>
                  <div className="section-label">Hintergrund</div>
                  <select className="input" value={backgroundMode} onChange={(e) => setBackgroundMode(e.target.value)}>
                    <option value="TRANSPARENT">Transparent</option>
                    <option value="BLURRED_ORIGINAL">Original weichzeichnen</option>
                    <option value="SOLID_COLOR">Farbe</option>
                  </select>
                  {backgroundMode === "SOLID_COLOR" && <label className="color-field">Farbe<input type="color" value={backgroundColor} onChange={(e) => setBackgroundColor(e.target.value)} /></label>}
                </>
              )}

              {tool === "video-text-edit" && (
                <>
                  <div className="section-label">Bearbeitungsart</div>
                  <div className="segmented-control"><button type="button" className={editMode === "TEXT_EDIT" ? "is-active" : ""} onClick={() => setEditMode("TEXT_EDIT")}>Verändern</button><button type="button" className={editMode === "TEXT_INPAINT" ? "is-active" : ""} onClick={() => setEditMode("TEXT_INPAINT")}>Ersetzen / Entfernen</button></div>
                  <label>Anweisung<textarea className="input textarea" rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Zum Beispiel: Ersetze den bewölkten Himmel durch einen klaren Sonnenuntergang." /></label>
                </>
              )}

              {tool === "video-cut" && source && (
                <RegionPicker
                  videoUrl={libraryFileUrl(source.id)}
                  region={cutRegion}
                  onChange={setCutRegion}
                  label="Bereich im Video"
                  disabled={busy}
                />
              )}

              {tool === "video-generation" && (
                <>
                  <label>Prompt<textarea className="input textarea" rows={6} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Beschreibe Szene, Bewegung, Licht und Kameraführung." /></label>
                  <div className="field-grid field-grid--2">
                    <label>Format<select className="input" value={aspectRatio} onChange={(e) => setAspectRatio(e.target.value)}><option value="9:16">Mobile 9:16</option><option value="16:9">Desktop 16:9</option><option value="1:1">Quadratisch 1:1</option><option value="4:3">4:3</option><option value="3:4">3:4</option></select></label>
                    <label>Dauer<select className="input" value={duration} onChange={(e) => setDuration(e.target.value)}><option value="3">3 Sekunden</option><option value="5">5 Sekunden</option><option value="8">8 Sekunden</option></select></label>
                  </div>
                </>
              )}
            </div>

            <div className="quick-tool-section">
              <div className="section-label">Qualität</div>
              <div className="quality-options">
                {[{id:"FAST", title:"Schnell", sub:"Kurzer Test"},{id:"STANDARD", title:"Standard", sub:"Empfohlen"},{id:"HIGH", title:"Hoch", sub:"Mehr Qualität"}].map((option) => (
                  <button type="button" key={option.id} className={`quality-option${quality === option.id ? " is-active" : ""}`} onClick={() => setQuality(option.id)}><strong>{option.title}</strong><span>{option.sub}</span></button>
                ))}
              </div>
            </div>

            <button type="button" className="advanced-toggle" onClick={() => setAdvanced((value) => !value)}><Settings2 size={15} /> Erweiterte Einstellungen <span>{advanced ? "−" : "+"}</span></button>
            {advanced && (
              <div className="advanced-panel">
                {tool !== "video-generation" && <div className="field-grid field-grid--2"><label>Start (Sek.)<input className="input" type="number" min="0" step="0.1" value={startSeconds} onChange={(e) => setStartSeconds(e.target.value)} /></label><label>Ende (Sek., optional)<input className="input" type="number" min="0" step="0.1" value={endSeconds} onChange={(e) => setEndSeconds(e.target.value)} /></label></div>}
                {(tool === "video-text-edit" || tool === "video-generation") && <><label>Negativer Prompt<textarea className="input textarea" rows={2} value={negativePrompt} onChange={(e) => setNegativePrompt(e.target.value)} /></label><div className="field-grid field-grid--3"><label>Seed<input className="input" type="number" value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="zufällig" /></label><label>Schritte<input className="input" type="number" min="1" max="100" value={steps} onChange={(e) => setSteps(e.target.value)} /></label><label>Guidance<input className="input" type="number" step="0.5" value={guidance} onChange={(e) => setGuidance(e.target.value)} /></label></div></>}
              </div>
            )}

            {!job && (
              <div className="quick-tool-actions">
                <Button variant="primary" onClick={() => void handleStart()} loading={upload.isPending || start.isPending} disabled={Boolean(statusUnavailable) || (definition.needsSource && !source && !selectedFile) || (tool === "video-cut" && !cutRegion)}>{tool === "video-cut" ? <Scissors size={16} /> : <Sparkles size={16} />} {definition.title}</Button>
                <span>Ergebnis wird nach Ablauf der temporären Aufbewahrung gelöscht, solange du es nicht speicherst.</span>
              </div>
            )}
          </Card>
        </section>

        <aside className="quick-tool-result">
          <Card title="Vorschau und Ergebnis">
            {!job && <div className="result-empty"><FileVideo size={34} /><strong>Noch kein Ergebnis</strong><span>Starte den Vorgang, um Fortschritt und Ausgabe hier zu sehen.</span></div>}
            {job && (
              <>
                <div className={`job-state job-state--${job.status.toLowerCase()}`}>
                  {ACTIVE.has(job.status) ? <Loader2 size={18} className="spin" /> : job.status === "COMPLETED" ? <Check size={18} /> : <FileVideo size={18} />}
                  <div><strong>{formatStatus(job)}</strong><span>{job.source_title ? String(job.source_title) : definition.title}</span></div>
                  <b>{Math.round(Number(job.progress ?? 0))}%</b>
                </div>
                {ACTIVE.has(job.status) && <div className="progress-track"><span style={{ width: `${Math.max(3, Number(job.progress ?? 0))}%` }} /></div>}
                {job.status === "FAILED" && <div className="inline-problem" role="alert"><strong>Verarbeitung fehlgeschlagen</strong><span>{getJobErrorMessage(job.error)}</span></div>}

                {job.status === "COMPLETED" && resultIds.map((itemId, index) => (
                  <div className="result-output" key={itemId}>
                    <video src={libraryFileUrl(itemId)} controls preload="metadata" />
                    <div className="result-output__title">{resultIds.length > 1 ? `Ausgabe ${index + 1}` : "Ergebnis"}</div>
                    <div className="result-actions">
                      <a className="btn btn--primary" href={libraryFileUrl(itemId)} download><Download size={15} /> Herunterladen</a>
                      {!promotedIds.has(itemId) && (!autoPersist || autoPromotionFailed) && <Button onClick={() => void handlePromote(itemId)} loading={promote.isPending}><Save size={15} /> In Library speichern</Button>}
                      <Button variant="secondary" onClick={() => void handleOpenEditor(itemId)}><ExternalLink size={15} /> Im Editor öffnen</Button>
                    </div>
                    {promotedIds.has(itemId) && <div className="saved-note"><Check size={14} /> In der Library gespeichert</div>}
                  </div>
                ))}

                <div className="result-footer-actions">
                  {ACTIVE.has(job.status) && <Button variant="danger" size="sm" onClick={() => void cancel.mutateAsync(job.id)} loading={cancel.isPending}>Abbrechen</Button>}
                  {["FAILED", "CANCELED"].includes(job.status) && <Button size="sm" onClick={() => void retry.mutateAsync(job.id)} loading={retry.isPending}>Erneut versuchen</Button>}
                  {!ACTIVE.has(job.status) && <Button variant="ghost" size="sm" onClick={() => void handleDiscard()} loading={discarding}><Trash2 size={14} /> Temporäre Dateien verwerfen</Button>}
                </div>
              </>
            )}
          </Card>

          {capabilityInfo.data && (
            <Card className="capability-note" title="Verarbeitung">
              <div className="info-row"><span className="info-row__label">Speicherung</span><span className="info-row__value">Temporär</span></div>
              <div className="info-row"><span className="info-row__label">Original</span><span className="info-row__value">Unverändert</span></div>
              <div className="info-row"><span className="info-row__label">GPU</span><span className="info-row__value">Gemeinsamer Worker</span></div>
            </Card>
          )}
        </aside>
      </div>
    </div>
  );
}
