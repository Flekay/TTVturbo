import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  GitBranch,
  Check,
  ChevronDown,
  Download,
  Film,
  GitCommitHorizontal,
  History,
  Layers3,
  Loader2,
  MonitorPlay,
  Play,
  Plus,
  Save,
  SlidersHorizontal,
  Smartphone,
  Square,
  Video,
} from "lucide-react";
import { apiClient } from "../api/client";
import { Button } from "../components/ui/Button";
import { ErrorState } from "../components/ui/ErrorState";
import { useToast } from "../components/ui/ToastProvider";
import { fetchLibraryItem, libraryItemFileUrl } from "../features/library/api";
import { promoteLibraryItem } from "../features/capabilities/api";
import { startRender, type EditSequence, type TimelineClip, type TimelineTrack } from "../features/projects/api";
import {
  useCheckoutBranch,
  useCheckoutCommit,
  useCheckoutSequence,
  useCommitState,
  useCreateBranch,
  useCreateCommit,
  useProject,
  useProjectCommits,
} from "../features/projects/hooks";

interface RenderJob {
  id: string;
  status: string;
  progress?: number;
  current_stage?: string | null;
  library_item_id?: string | null;
  error?: { message?: string } | string | null;
}

function safeId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function sequenceIcon(profile: string) {
  if (profile === "MOBILE_9_16") return <Smartphone size={15} />;
  if (profile === "SQUARE_1_1") return <Square size={15} />;
  return <MonitorPlay size={15} />;
}

export function ProjectWorkspacePage() {
  const { projectId } = useParams();
  const toast = useToast();
  const project = useProject(projectId);
  const commits = useProjectCommits(projectId);
  const commitState = useCommitState(projectId, project.data?.checkout_commit_id);
  const createCommit = useCreateCommit(projectId!);
  const checkoutBranch = useCheckoutBranch(projectId!);
  const checkoutSequence = useCheckoutSequence(projectId!);
  const checkoutCommit = useCheckoutCommit(projectId!);
  const createBranch = useCreateBranch(projectId!);

  const [versionsOpen, setVersionsOpen] = useState(false);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [sourceDuration, setSourceDuration] = useState(0);
  const [renderJobId, setRenderJobId] = useState<string | null>(null);
  const [renderMode, setRenderMode] = useState<"PREVIEW" | "FINAL">("PREVIEW");
  const [savedRenderId, setSavedRenderId] = useState<string | null>(null);
  const [transform, setTransform] = useState({ x: "0", y: "0", scale: "1", rotation: "0", opacity: "1" });

  const sourceId = project.data?.sources?.[0]?.media_item_id;
  const sourceQuery = useQuery({ queryKey: ["library", "item", sourceId], queryFn: () => fetchLibraryItem(sourceId!), enabled: Boolean(sourceId) });
  const renderJob = useQuery({
    queryKey: ["render-job", renderJobId],
    queryFn: () => apiClient.get<RenderJob>(`/api/rendering/jobs/${renderJobId}`),
    enabled: Boolean(renderJobId),
    refetchInterval: (query) => ["QUEUED", "RUNNING", "RETRYING"].includes(query.state.data?.status ?? "") ? 1000 : false,
  });
  const startRenderMutation = useMutation({ mutationFn: startRender, onSuccess: (job) => setRenderJobId(String(job.id)) });
  const promoteRender = useMutation({ mutationFn: promoteLibraryItem, onSuccess: (item) => setSavedRenderId(item.id) });

  const activeSequenceId = project.data?.active_sequence_id ?? project.data?.sequences?.[0]?.id;
  const stateSequences = commitState.data?.state?.sequences ?? {};
  const activeSequence: EditSequence | undefined = activeSequenceId ? stateSequences[activeSequenceId] ?? project.data?.sequences.find((seq) => seq.id === activeSequenceId) : undefined;
  const tracks = useMemo(() => Object.values(activeSequence?.tracks ?? {}), [activeSequence?.tracks]);
  const selectedTrack = tracks.find((track) => track.id === selectedTrackId);
  const selectedClip = selectedTrack?.clips?.[selectedClipId ?? ""];
  const activeBranch = project.data?.branches.find((branch) => branch.id === project.data?.active_branch_id);

  useEffect(() => {
    if (!selectedClip) return;
    setTransform({
      x: String(selectedClip.transform?.x ?? 0),
      y: String(selectedClip.transform?.y ?? 0),
      scale: String(selectedClip.transform?.scale_x ?? 1),
      rotation: String(selectedClip.transform?.rotation ?? 0),
      opacity: String(selectedClip.opacity ?? 1),
    });
  }, [selectedClip]);

  if (project.isError || !projectId) return <ErrorState title="Projekt konnte nicht geladen werden" message={project.error instanceof Error ? project.error.message : "Unbekannter Fehler"} />;
  if (project.isLoading || !project.data) return <div className="state"><Loader2 className="spin" /> Projekt wird geladen …</div>;

  async function addSourceToTimeline() {
    if (!activeBranch || !activeSequence || !sourceId || sourceDuration <= 0) return;
    const existingVideoTrack = tracks.find((track) => ["VIDEO", "GAMEPLAY"].includes(track.type));
    const trackId = existingVideoTrack?.id ?? safeId("video");
    const operations: Array<Record<string, unknown>> = [];
    if (!existingVideoTrack) {
      operations.push({ type: "ADD_TRACK", sequence_id: activeSequence.id, payload: { track: { id: trackId, type: "VIDEO", name: "Video" } } });
    }
    operations.push({ type: "ADD_CLIP", sequence_id: activeSequence.id, payload: { track_id: trackId, clip: { id: safeId("clip"), source_media_item_id: sourceId, source_start_us: 0, source_end_us: Math.round(sourceDuration * 1_000_000), timeline_start_us: 0 } } });
    await createCommit.mutateAsync({ branch_id: activeBranch.id, expected_head_commit_id: activeBranch.head_commit_id, message: "Quelle zur Timeline hinzugefügt", operations });
    toast.show({ title: "Quelle zur Timeline hinzugefügt", variant: "success" });
  }

  async function saveInspector() {
    if (!activeBranch || !activeSequence || !selectedTrack || !selectedClip) return;
    const operations = [
      { type: "SET_TRANSFORM", sequence_id: activeSequence.id, payload: { track_id: selectedTrack.id, clip_id: selectedClip.id, value: { x: Number(transform.x), y: Number(transform.y), scale_x: Number(transform.scale), scale_y: Number(transform.scale), rotation: Number(transform.rotation) } } },
      { type: "SET_OPACITY", sequence_id: activeSequence.id, payload: { track_id: selectedTrack.id, clip_id: selectedClip.id, value: Number(transform.opacity) } },
    ];
    await createCommit.mutateAsync({ branch_id: activeBranch.id, expected_head_commit_id: activeBranch.head_commit_id, message: "Clip angepasst", operations });
    toast.show({ title: "Version gespeichert", variant: "success" });
  }

  async function handleRender(mode: "PREVIEW" | "FINAL") {
    if (!activeSequence || !project.data) return;
    setRenderMode(mode);
    setRenderJobId(null);
    setSavedRenderId(null);
    await startRenderMutation.mutateAsync({ project_id: projectId!, sequence_id: activeSequence.id, commit_id: project.data.checkout_commit_id, output_lifecycle: "TEMPORARY", settings: { mode, video_codec: "libx264", audio_codec: "aac", include_audio: true, preview_max_dimension: 720 } });
  }

  async function makeVariant(commitId: string) {
    const branch = await createBranch.mutateAsync({ name: `Variante ${new Date().toLocaleString("de-DE")}`, from_commit_id: commitId });
    await checkoutBranch.mutateAsync(branch.id);
    setVersionsOpen(false);
  }

  const renderedItemId = renderJob.data?.library_item_id ?? null;

  return (
    <div className="project-workspace">
      <div className="project-toolbar">
        <div className="project-toolbar__left">
          <Link className="btn btn--ghost btn--icon" to="/projects" aria-label="Zurück"><ArrowLeft size={16} /></Link>
          <div className="project-toolbar__name"><strong>{project.data.name}</strong><span>{project.data.detached_commit_id ? "Historische Version" : "Gespeichert"}</span></div>
        </div>
        <div className="project-toolbar__selectors">
          <label>{sequenceIcon(activeSequence?.format_profile ?? "")}<select value={activeSequenceId} onChange={(event) => void checkoutSequence.mutateAsync(event.target.value)}>{project.data.sequences.map((sequence) => <option key={sequence.id} value={sequence.id}>{sequence.name} · {sequence.width}×{sequence.height}</option>)}</select><ChevronDown size={13} /></label>
          <label><GitBranch size={14} /><select value={project.data.active_branch_id ?? ""} onChange={(event) => void checkoutBranch.mutateAsync(event.target.value)}>{project.data.branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}</select><ChevronDown size={13} /></label>
        </div>
        <div className="project-toolbar__actions">
          <Button variant="ghost" size="sm" onClick={() => setVersionsOpen((value) => !value)}><History size={15} /> Versionen</Button>
          <Button size="sm" onClick={() => void handleRender("PREVIEW")} loading={startRenderMutation.isPending && renderMode === "PREVIEW"}><Play size={14} /> Vorschau</Button>
          <Button variant="primary" size="sm" onClick={() => void handleRender("FINAL")} loading={startRenderMutation.isPending && renderMode === "FINAL"}><Video size={14} /> Rendern</Button>
        </div>
      </div>

      <div className="editor-grid">
        <aside className="editor-panel editor-panel--media">
          <div className="editor-panel__header"><span><Layers3 size={15} /> Medien</span></div>
          {sourceQuery.data && <div className="editor-media-item"><Film size={19} /><div><strong>{sourceQuery.data.title}</strong><span>Projektquelle</span></div></div>}
          <Button variant="secondary" size="sm" onClick={() => void addSourceToTimeline()} loading={createCommit.isPending} disabled={!sourceDuration || !activeBranch}><Plus size={14} /> Zur Timeline</Button>
        </aside>

        <main className="editor-canvas-area">
          <div className={`editor-canvas editor-canvas--${activeSequence?.format_profile?.toLowerCase() ?? "custom"}`}>
            {renderedItemId && renderJob.data?.status === "COMPLETED" ? (
              <video src={libraryItemFileUrl(renderedItemId)} controls autoPlay />
            ) : sourceId ? (
              <video src={libraryItemFileUrl(sourceId)} controls onLoadedMetadata={(event) => setSourceDuration(event.currentTarget.duration)} />
            ) : <div className="result-empty"><Film size={32} /> Keine Quelle</div>}
          </div>
          {renderJobId && (
            <div className="render-strip">
              <div>{["QUEUED", "RUNNING", "RETRYING"].includes(renderJob.data?.status ?? "") ? <Loader2 size={15} className="spin" /> : <Check size={15} />}<span>{renderJob.data?.status === "COMPLETED" ? `${renderMode === "PREVIEW" ? "Vorschau" : "Render"} fertig` : renderJob.data?.current_stage || "Render wird vorbereitet"}</span></div>
              <strong>{Math.round(renderJob.data?.progress ?? 0)} %</strong>
              {renderedItemId && renderJob.data?.status === "COMPLETED" && <><a className="btn btn--ghost btn--sm" href={libraryItemFileUrl(renderedItemId)}><Download size={14} /> Download</a>{savedRenderId !== renderedItemId && <Button size="sm" onClick={() => promoteRender.mutate(renderedItemId)} loading={promoteRender.isPending}><Save size={14} /> In Library</Button>}</>}
            </div>
          )}
        </main>

        <aside className="editor-panel editor-panel--inspector">
          <div className="editor-panel__header"><span><SlidersHorizontal size={15} /> Inspector</span></div>
          {selectedClip ? (
            <div className="inspector-form">
              <strong>{selectedClip.id}</strong>
              <div className="field-grid field-grid--2"><label>X<input className="input" type="number" step="0.01" value={transform.x} onChange={(e) => setTransform({ ...transform, x: e.target.value })} /></label><label>Y<input className="input" type="number" step="0.01" value={transform.y} onChange={(e) => setTransform({ ...transform, y: e.target.value })} /></label><label>Größe<input className="input" type="number" step="0.05" value={transform.scale} onChange={(e) => setTransform({ ...transform, scale: e.target.value })} /></label><label>Rotation<input className="input" type="number" step="1" value={transform.rotation} onChange={(e) => setTransform({ ...transform, rotation: e.target.value })} /></label></div>
              <label>Deckkraft<input className="input" type="number" min="0" max="1" step="0.05" value={transform.opacity} onChange={(e) => setTransform({ ...transform, opacity: e.target.value })} /></label>
              <Button variant="primary" size="sm" onClick={() => void saveInspector()} loading={createCommit.isPending}>Als neue Version speichern</Button>
            </div>
          ) : <div className="editor-empty-panel">Clip in der Timeline auswählen.</div>}
        </aside>

        <section className="editor-timeline">
          <div className="timeline-ruler"><span>00:00</span><span>00:15</span><span>00:30</span><span>00:45</span><span>01:00</span></div>
          {tracks.length === 0 ? <div className="timeline-empty"><Film size={20} /><span>Noch keine Clips in dieser Ausgabe.</span><Button size="sm" onClick={() => void addSourceToTimeline()} disabled={!sourceDuration}>Quelle hinzufügen</Button></div> : tracks.map((track: TimelineTrack) => (
            <div className="timeline-track" key={track.id}>
              <div className="timeline-track__label"><strong>{track.name || track.type}</strong><span>{track.type}</span></div>
              <div className="timeline-track__lane">
                {Object.values(track.clips ?? {}).map((clip: TimelineClip) => {
                  const durationUs = Math.max(1, clip.source_end_us - clip.source_start_us);
                  const width = Math.max(8, Math.min(95, durationUs / 60_000_000 * 100));
                  const left = Math.max(0, Math.min(95, clip.timeline_start_us / 60_000_000 * 100));
                  const selected = selectedTrackId === track.id && selectedClipId === clip.id;
                  return <button type="button" key={clip.id} className={`timeline-clip${selected ? " is-selected" : ""}`} style={{ left: `${left}%`, width: `${width}%` }} onClick={() => { setSelectedTrackId(track.id); setSelectedClipId(clip.id); }}><span>{sourceQuery.data?.title || "Clip"}</span></button>;
                })}
              </div>
            </div>
          ))}
        </section>
      </div>

      {versionsOpen && (
        <aside className="versions-drawer">
          <div className="versions-drawer__header"><div><History size={18} /><strong>Versionen</strong></div><Button variant="ghost" size="sm" onClick={() => setVersionsOpen(false)}>Schließen</Button></div>
          <div className="versions-drawer__branches">{project.data.branches.map((branch) => <button type="button" key={branch.id} className={branch.id === project.data.active_branch_id ? "is-active" : ""} onClick={() => void checkoutBranch.mutateAsync(branch.id)}><GitBranch size={14} /> {branch.name}</button>)}</div>
          <div className="versions-list">
            {(commits.data ?? []).map((commit, index) => <article key={commit.id} className={commit.id === project.data.checkout_commit_id ? "is-current" : ""}><div className="versions-node"><GitCommitHorizontal size={15} /><span /></div><button type="button" onClick={() => void checkoutCommit.mutateAsync(commit.id)}><strong>{commit.message}</strong><span>{new Date(commit.created_at).toLocaleString("de-DE")}</span></button><Button variant="ghost" size="sm" onClick={() => void makeVariant(commit.id)}>Neue Variante</Button>{index === 0 && <small>Aktuell</small>}</article>)}
          </div>
        </aside>
      )}
    </div>
  );
}
