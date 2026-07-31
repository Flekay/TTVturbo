import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, Download, Loader2, Save, Video } from "lucide-react";
import { apiClient } from "../api/client";
import { AddMediaDialog, type AddMediaMode } from "../components/projects/AddMediaDialog";
import { EditorCanvas, type CanvasTransform } from "../components/projects/EditorCanvas";
import { EditorSidePanel } from "../components/projects/EditorSidePanel";
import { EditorTimeline } from "../components/projects/EditorTimeline";
import { Button } from "../components/ui/Button";
import { ErrorState } from "../components/ui/ErrorState";
import { useToast } from "../components/ui/ToastProvider";
import { promoteLibraryItem } from "../features/capabilities/api";
import { useStartCapabilityJob, useCapabilityJob } from "../features/capabilities/hooks";
import { fetchLibraryItem, libraryItemFileUrl } from "../features/library/api";
import { useLibraryItemsQuery } from "../features/library/hooks";
import type { LibraryItem } from "../features/library/schemas";
import { useUIStore } from "../stores/uiStore";
import { parseEditorCommand, startRender, type EditCommit, type EditorCommandContext, type EditorCommandIntent, type EditSequence, type TimelineClip, type TimelineEffect, type TimelineElementKind, type TimelineTrack } from "../features/projects/api";
import { canPlace, clipDurationUs, firstAvailableLayerAt, firstAvailableStartAtOrAfter } from "../features/projects/timelineLogic";
import type { NormalizedRegion } from "../features/videoCut";
import {
  useAddProjectSource,
  useCheckoutBranch,
  useCheckoutCommit,
  useCommitState,
  useCreateCommit,
  useProject,
  useProjectCommits,
  useResetBranch,
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
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function mediaIsAudio(item: LibraryItem): boolean {
  return item.file_type === "audio";
}

function mediaIsImage(item: LibraryItem): boolean {
  return item.file_type === "image";
}

function elementKindForMode(mode: AddMediaMode): TimelineElementKind {
  if (mode === "AUDIO") return "AUDIO";
  if (mode === "IMAGE") return "IMAGE";
  return "VIDEO";
}

/** Default duration (in seconds) for still images that have no intrinsic duration. */
const DEFAULT_IMAGE_DURATION_SECONDS = 5;

interface ResolvedMediaMetadata {
  durationSeconds: number;
  width?: number;
  height?: number;
}

async function resolveMediaMetadata(item: LibraryItem): Promise<ResolvedMediaMetadata> {
  const knownDuration = item.duration_seconds && item.duration_seconds > 0 ? item.duration_seconds : null;
  if (mediaIsAudio(item) && knownDuration) return { durationSeconds: knownDuration };

  if (mediaIsImage(item)) {
    return new Promise<ResolvedMediaMetadata>((resolve, reject) => {
      const image = new Image();
      const timeout = window.setTimeout(() => {
        cleanup();
        reject(new Error("Die Bildgröße konnte nicht gelesen werden."));
      }, 15_000);
      const cleanup = () => {
        window.clearTimeout(timeout);
        image.onload = null;
        image.onerror = null;
      };
      image.onload = () => {
        const width = image.naturalWidth;
        const height = image.naturalHeight;
        cleanup();
        if (!width || !height) reject(new Error("Ungültige Bildgröße."));
        else resolve({ durationSeconds: knownDuration ?? DEFAULT_IMAGE_DURATION_SECONDS, width, height });
      };
      image.onerror = () => {
        cleanup();
        reject(new Error("Das Bild konnte nicht geöffnet werden."));
      };
      image.src = libraryItemFileUrl(item.id);
    });
  }

  return new Promise<ResolvedMediaMetadata>((resolve, reject) => {
    const element = document.createElement(mediaIsAudio(item) ? "audio" : "video");
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error("Die Metadaten des Mediums konnten nicht gelesen werden."));
    }, 15_000);
    const cleanup = () => {
      window.clearTimeout(timeout);
      element.removeAttribute("src");
      element.load();
    };
    element.preload = "metadata";
    element.onloadedmetadata = () => {
      const durationSeconds = knownDuration ?? element.duration;
      const width = element instanceof HTMLVideoElement ? element.videoWidth : undefined;
      const height = element instanceof HTMLVideoElement ? element.videoHeight : undefined;
      cleanup();
      if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) {
        reject(new Error("Ungültige Mediendauer."));
      } else if (element instanceof HTMLVideoElement && (!width || !height)) {
        reject(new Error("Ungültige Videogröße."));
      } else {
        resolve({ durationSeconds, width, height });
      }
    };
    element.onerror = () => {
      cleanup();
      reject(new Error("Das Medium konnte nicht geöffnet werden."));
    };
    element.src = libraryItemFileUrl(item.id);
  });
}

function defaultVisualTransform(metadata: ResolvedMediaMetadata, sequence: EditSequence): CanvasTransform | undefined {
  const sourceWidth = Number(metadata.width ?? 0);
  const sourceHeight = Number(metadata.height ?? 0);
  if (sourceWidth <= 0 || sourceHeight <= 0) return undefined;
  const fitScale = Math.min(1, sequence.width / sourceWidth, sequence.height / sourceHeight);
  const width = Math.max(1, sourceWidth * fitScale);
  const height = Math.max(1, sourceHeight * fitScale);
  const scaleX = width / sequence.width;
  const scaleY = height / sequence.height;
  return {
    x: (1 - scaleX) / 2,
    y: (1 - scaleY) / 2,
    scale_x: scaleX,
    scale_y: scaleY,
    rotation: 0,
  };
}

function isTimelineOverlapError(error: unknown): boolean {
  return errorMessage(error).toLocaleLowerCase("en-US").includes("timeline overlap");
}

function getOrderedTracks(sequence: EditSequence | undefined): TimelineTrack[] {
  if (!sequence?.tracks) return [];
  const values = sequence.tracks;
  const order = sequence.track_order ?? [];
  const result = order.map((id) => values[id]).filter(Boolean);
  for (const track of Object.values(values)) if (!result.some((item) => item.id === track.id)) result.push(track);
  return result;
}

function transformOf(clip: TimelineClip): CanvasTransform {
  return {
    x: Number(clip.transform?.x ?? 0),
    y: Number(clip.transform?.y ?? 0),
    scale_x: Number(clip.transform?.scale_x ?? 1),
    scale_y: Number(clip.transform?.scale_y ?? 1),
    rotation: Number(clip.transform?.rotation ?? 0),
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Der Vorgang konnte nicht abgeschlossen werden.";
}

export function ProjectWorkspacePage() {
  const { projectId } = useParams();
  const toast = useToast();
  const project = useProject(projectId);
  const commits = useProjectCommits(projectId);
  const commitState = useCommitState(projectId, project.data?.checkout_commit_id);
  // Include temporary items so mediaTitles/mediaFileTypes cover editor uploads.
  const library = useLibraryItemsQuery(undefined, { includeTemporary: true });
  const createCommit = useCreateCommit(projectId!);
  const addProjectSource = useAddProjectSource(projectId!);
  const checkoutBranch = useCheckoutBranch(projectId!);
  const checkoutCommit = useCheckoutCommit(projectId!);
  const resetBranch = useResetBranch(projectId!);

  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);
  const setTopbarHidden = useUIStore((s) => s.setTopbarHidden);

  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [playheadUs, setPlayheadUs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [addMediaOpen, setAddMediaOpen] = useState(false);
  const [editorBusy, setEditorBusy] = useState(false);
  const [renderJobId, setRenderJobId] = useState<string | null>(null);
  const [savedRenderId, setSavedRenderId] = useState<string | null>(null);
  const [cutJobId, setCutJobId] = useState<string | null>(null);
  const cutTargetRef = useRef<{ trackId: string; clip: TimelineClip; targetTransform?: CanvasTransform } | null>(null);
  const [sidePanelWidth, setSidePanelWidth] = useState(350);
  const [timelineHeight, setTimelineHeight] = useState(330);
  const editorGridRef = useRef<HTMLDivElement>(null);

  const startResize = (cursor: string) => {
    document.body.style.cursor = cursor;
    document.body.style.userSelect = "none";
    return () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  };

  const beginSideResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = sidePanelWidth;
    const reset = startResize("col-resize");
    const move = (moveEvent: PointerEvent) => {
      const delta = startX - moveEvent.clientX;
      setSidePanelWidth(Math.max(240, Math.min(720, startWidth + delta)));
    };
    const release = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", release);
      reset();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", release);
  };

  const beginTimelineResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = timelineHeight;
    const gridEl = editorGridRef.current;
    const gridHeight = gridEl?.getBoundingClientRect().height ?? startHeight + 360;
    const reset = startResize("row-resize");
    const move = (moveEvent: PointerEvent) => {
      const delta = moveEvent.clientY - startY;
      setTimelineHeight(Math.max(160, Math.min(gridHeight - 240, startHeight - delta)));
    };
    const release = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", release);
      reset();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", release);
  };

  const beginCornerResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const startWidth = sidePanelWidth;
    const startHeight = timelineHeight;
    const gridEl = editorGridRef.current;
    const gridHeight = gridEl?.getBoundingClientRect().height ?? startHeight + 360;
    const reset = startResize("nwse-resize");
    const move = (moveEvent: PointerEvent) => {
      const deltaX = startX - moveEvent.clientX;
      const deltaY = moveEvent.clientY - startY;
      setSidePanelWidth(Math.max(240, Math.min(720, startWidth + deltaX)));
      setTimelineHeight(Math.max(160, Math.min(gridHeight - 240, startHeight - deltaY)));
    };
    const release = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", release);
      reset();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", release);
  };

  const renderJob = useQuery({
    queryKey: ["render-job", renderJobId],
    queryFn: () => apiClient.get<RenderJob>(`/api/rendering/jobs/${renderJobId}`),
    enabled: Boolean(renderJobId),
    refetchInterval: (query) => ["QUEUED", "RUNNING", "RETRYING"].includes(query.state.data?.status ?? "") ? 1000 : false,
  });
  const startRenderMutation = useMutation({ mutationFn: startRender, onSuccess: (job) => setRenderJobId(String(job.id)) });
  const promoteRender = useMutation({ mutationFn: promoteLibraryItem, onSuccess: (item) => setSavedRenderId(item.id) });

  const startCutJob = useStartCapabilityJob("video-cut");
  const cutJob = useCapabilityJob("video-cut", cutJobId);
  const cutResultItemId = cutJob.data?.library_item_ids?.[0] ?? cutJob.data?.library_item_id ?? null;

  const activeSequenceId = project.data?.active_sequence_id ?? project.data?.sequences?.[0]?.id;
  const stateSequences = commitState.data?.state?.sequences ?? {};
  const activeSequence: EditSequence | undefined = activeSequenceId
    ? stateSequences[activeSequenceId] ?? project.data?.sequences.find((sequence) => sequence.id === activeSequenceId)
    : undefined;
  const tracks = useMemo(() => getOrderedTracks(activeSequence), [activeSequence]);
  const selectedTrack = tracks.find((track) => track.id === selectedTrackId) ?? null;
  const selectedClip = selectedTrack?.clips?.[selectedClipId ?? ""] ?? null;
  const stateSources = commitState.data?.state?.sources ?? project.data?.sources ?? [];
  const activeBranch = project.data?.branches.find((branch) => branch.id === project.data?.active_branch_id);
  const commitItems = useMemo(() => commits.data?.pages.flatMap((page) => page.commits) ?? [], [commits.data]);
  const mediaTitles = useMemo(() => Object.fromEntries((library.data?.items ?? []).map((item) => [item.id, item.title])), [library.data?.items]);
  const mediaFileTypes = useMemo(() => Object.fromEntries((library.data?.items ?? []).map((item) => [item.id, item.file_type ?? "video"])), [library.data?.items]);

  const timelineEndUs = useMemo(() => {
    let end = 0;
    for (const track of tracks) for (const clip of Object.values(track.clips ?? {})) end = Math.max(end, clip.timeline_start_us + clipDurationUs(clip));
    return end;
  }, [tracks]);

  const headRef = useRef<string>("");
  const branchRef = useRef<string>("");
  const detachedRef = useRef<string | null>(null);
  const playheadRef = useRef(0);
  const layersRef = useRef<TimelineTrack[]>([]);
  const commitQueueRef = useRef<Promise<unknown>>(Promise.resolve());
  const sourceIdsRef = useRef<Set<string>>(new Set());

  useEffect(() => { playheadRef.current = playheadUs; }, [playheadUs]);
  useEffect(() => { layersRef.current = tracks; }, [tracks]);
  useEffect(() => { sourceIdsRef.current = new Set(stateSources.filter((source) => !source.asset_id).map((source) => source.media_item_id)); }, [stateSources]);

  useEffect(() => {
    const { sidebarCollapsed, topbarHidden } = useUIStore.getState();
    setSidebarCollapsed(true);
    setTopbarHidden(true);
    return () => {
      setSidebarCollapsed(sidebarCollapsed);
      setTopbarHidden(topbarHidden);
    };
  }, [setSidebarCollapsed, setTopbarHidden]);
  useEffect(() => {
    if (activeBranch) {
      headRef.current = activeBranch.head_commit_id;
      branchRef.current = activeBranch.id;
    }
    detachedRef.current = project.data?.detached_commit_id ?? null;
  }, [activeBranch, project.data?.detached_commit_id]);

  useEffect(() => {
    if (!selectedTrackId || !selectedClipId) return;
    if (!tracks.some((track) => track.id === selectedTrackId && Boolean(track.clips?.[selectedClipId]))) {
      setSelectedTrackId(null);
      setSelectedClipId(null);
    }
  }, [selectedClipId, selectedTrackId, tracks]);

  useEffect(() => {
    if (!playing) return;
    if (timelineEndUs <= 0) { setPlaying(false); return; }
    const startAt = playheadRef.current >= timelineEndUs ? 0 : playheadRef.current;
    if (startAt !== playheadRef.current) setPlayheadUs(startAt);
    const started = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const next = startAt + (now - started) * 1000;
      if (next >= timelineEndUs) {
        setPlayheadUs(timelineEndUs);
        setPlaying(false);
        return;
      }
      setPlayheadUs(next);
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [playing, timelineEndUs]);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      // Space is handled by EditorCanvas (short press = play, long hold = pan).
      if (event.code === "Space") return;
      if ((event.key === "Delete" || event.key === "Backspace") && selectedTrack && selectedClip) {
        event.preventDefault();
        void removeClip(selectedTrack.id, selectedClip).catch((error) => toast.show({ title: "Clip konnte nicht entfernt werden", description: errorMessage(error), variant: "error" }));
      } else if (event.key.toLowerCase() === "s" && selectedTrack && selectedClip) {
        event.preventDefault();
        void splitClip(selectedTrack.id, selectedClip).catch((error) => toast.show({ title: "Clip konnte nicht geteilt werden", description: errorMessage(error), variant: "error" }));
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        const fps = activeSequence ? activeSequence.fps_numerator / activeSequence.fps_denominator : 30;
        setPlayheadUs((value) => Math.max(0, value - 1_000_000 / fps));
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        const fps = activeSequence ? activeSequence.fps_numerator / activeSequence.fps_denominator : 30;
        setPlayheadUs((value) => value + 1_000_000 / fps);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  });

  // Ref holds the latest addMedia function so the auto-insert effect can call
  // it without being defined after the early returns below.
  const addMediaRef = useRef<((item: LibraryItem, mode: AddMediaMode) => Promise<void>) | null>(null);

  // Replace the original clip with the cut result when the job completes.
  // Must be declared BEFORE the early returns to keep hook order stable.
  useEffect(() => {
    if (!cutJob.data || cutJob.data.status !== "COMPLETED") return;
    if (!cutResultItemId) return;
    if (cutJobId && cutJob.data.id !== cutJobId) return;
    const target = cutTargetRef.current;
    if (!target) return;
    void (async () => {
      let item;
      try {
        // Fetch the item directly by ID — it may be TEMPORARY and thus
        // absent from the filtered library list query.
        item = await fetchLibraryItem(cutResultItemId);
      } catch {
        toast.show({ title: "Zuschnitt konnte nicht geladen werden", variant: "error" });
        return;
      }
      try {
        await replaceClipWithCutResult(target.trackId, target.clip, item, target.targetTransform);
        setCutJobId(null);
        cutTargetRef.current = null;
      } catch {
        // replaceClipWithCutResult already shows a toast on failure.
      }
    })();
  }, [cutJob.data, cutResultItemId, cutJobId]);

  // Toast on cut job failure (render-strip was removed).
  useEffect(() => {
    if (!cutJob.data || cutJob.data.status !== "FAILED") return;
    if (cutJobId && cutJob.data.id !== cutJobId) return;
    const msg = typeof cutJob.data.error === "string" ? cutJob.data.error : cutJob.data.error?.message ?? "Unbekannter Fehler";
    toast.show({ title: "Zuschneiden fehlgeschlagen", description: msg, variant: "error" });
    setCutJobId(null);
    cutTargetRef.current = null;
  }, [cutJob.data, cutJobId]);

  if (project.isError || !projectId) return <ErrorState title="Projekt konnte nicht geladen werden" message={project.error instanceof Error ? project.error.message : "Unbekannter Fehler"} />;
  if (project.isLoading || !project.data || !activeSequence) return <div className="state"><Loader2 className="spin" /> Projekt wird geladen …</div>;

  const readyProjectId = projectId;
  const readyProject = project.data;
  const readySequence = activeSequence;

  function runEditorAction(action: () => Promise<unknown>, title = "Bearbeitung fehlgeschlagen"): void {
    void action().catch((error) => toast.show({ title, description: errorMessage(error), variant: "error" }));
  }

  function seekTimeline(timeUs: number): void {
    const value = Math.max(0, timeUs);
    playheadRef.current = value;
    setPlayheadUs(value);
    setPlaying(false);
  }

  function serialize<T>(task: () => Promise<T>): Promise<T> {
    const next = commitQueueRef.current.then(task, task);
    commitQueueRef.current = next.then(() => undefined, () => undefined);
    return next;
  }

  async function ensureWritableBranchNow(): Promise<{ id: string; head: string }> {
    if (detachedRef.current) {
      if (!branchRef.current || !headRef.current) throw new Error("Aktiver Bearbeitungsverlauf konnte nicht ermittelt werden.");
      const targetCommitId = detachedRef.current;
      await resetBranch.mutateAsync({
        branch_id: branchRef.current,
        expected_head_commit_id: headRef.current,
        target_commit_id: targetCommitId,
      });
      headRef.current = targetCommitId;
      detachedRef.current = null;
      toast.show({ title: "Historische Version als neuer Bearbeitungsstand übernommen", variant: "success" });
    }
    if (!branchRef.current || !headRef.current) throw new Error("Aktiver Bearbeitungsverlauf konnte nicht ermittelt werden.");
    return { id: branchRef.current, head: headRef.current };
  }

  async function commitNow(message: string, operations: Array<Record<string, unknown>>): Promise<EditCommit> {
    const branch = await ensureWritableBranchNow();
    const result = await createCommit.mutateAsync({
      branch_id: branch.id,
      expected_head_commit_id: headRef.current,
      message,
      operations,
    });
    headRef.current = result.id;
    return result;
  }

  async function commitOperations(message: string, operations: Array<Record<string, unknown>>): Promise<EditCommit> {
    setEditorBusy(true);
    try {
      return await serialize(() => commitNow(message, operations));
    } finally {
      setEditorBusy(false);
    }
  }

  async function addMedia(item: LibraryItem, mode: AddMediaMode) {
    setEditorBusy(true);
    try {
      await serialize(async () => {
        const metadata = await resolveMediaMetadata(item);
        const branch = await ensureWritableBranchNow();
        const sourceExists = sourceIdsRef.current.has(item.id);
        if (!sourceExists) {
          const attached = await addProjectSource.mutateAsync({
            branch_id: branch.id,
            expected_head_commit_id: headRef.current,
            source: { media_item_id: item.id },
            message: `Medium hinzugefügt: ${item.title}`,
          });
          headRef.current = attached.commit.id;
          sourceIdsRef.current.add(item.id);
        }

        const kind = elementKindForMode(mode);
        const durationUs = Math.max(1, Math.round(metadata.durationSeconds * 1_000_000));
        const timelineStartUs = Math.max(0, Math.round(playheadRef.current));
        const currentLayers = layersRef.current;
        let targetLayer = firstAvailableLayerAt(currentLayers, timelineStartUs, durationUs, selectedTrackId);
        let createdLayer = false;
        if (!targetLayer) {
          targetLayer = { id: safeId("layer"), type: "UNIVERSAL", name: `Layer ${currentLayers.length + 1}`, clips: {} };
          createdLayer = true;
        }

        const clip: TimelineClip = {
          id: safeId("clip"),
          kind,
          source_media_item_id: item.id,
          source_start_us: 0,
          source_end_us: durationUs,
          timeline_start_us: timelineStartUs,
          transform: kind === "VIDEO" || kind === "IMAGE" ? defaultVisualTransform(metadata, readySequence) : undefined,
          audio_muted: false,
          effects: [],
        };

        const commitInsertion = async (layer: TimelineTrack, addLayer: boolean): Promise<EditCommit> => {
          const operations: Array<Record<string, unknown>> = [];
          if (addLayer) operations.push({ type: "ADD_TRACK", sequence_id: readySequence.id, payload: { track: layer } });
          operations.push({
            type: "ADD_CLIP",
            sequence_id: readySequence.id,
            payload: { track_id: layer.id, clip, placement: "EXACT" },
          });
          return createCommit.mutateAsync({
            branch_id: branchRef.current,
            expected_head_commit_id: headRef.current,
            message: `${item.title} zur Timeline hinzugefügt`,
            operations,
          });
        };

        let result: EditCommit;
        try {
          result = await commitInsertion(targetLayer, createdLayer);
        } catch (error) {
          // A freshly attached source or another local commit can make the
          // browser snapshot stale. Preserve the exact playhead position by
          // retrying in a new layer instead of shifting the element in time.
          if (createdLayer || !isTimelineOverlapError(error)) throw error;
          targetLayer = { id: safeId("layer"), type: "UNIVERSAL", name: `Layer ${currentLayers.length + 1}`, clips: {} };
          createdLayer = true;
          result = await commitInsertion(targetLayer, true);
        }

        headRef.current = result.id;
        const layerWithClip: TimelineTrack = {
          ...targetLayer,
          clips: { ...(targetLayer.clips ?? {}), [clip.id]: clip },
        };
        layersRef.current = createdLayer
          ? [...currentLayers, layerWithClip]
          : currentLayers.map((layer) => layer.id === targetLayer.id ? layerWithClip : layer);
        setSelectedTrackId(targetLayer.id);
        setSelectedClipId(clip.id);
      });
      toast.show({ title: "Medium zur Timeline hinzugefügt", variant: "success" });
    } catch (error) {
      toast.show({ title: "Medium konnte nicht hinzugefügt werden", description: errorMessage(error), variant: "error" });
      throw error;
    } finally {
      setEditorBusy(false);
    }
  }

  addMediaRef.current = addMedia;

  async function replaceClipWithCutResult(trackId: string, oldClip: TimelineClip, newItem: LibraryItem, targetTransform?: CanvasTransform) {
    setEditorBusy(true);
    try {
      await serialize(async () => {
        const metadata = await resolveMediaMetadata(newItem);
        const branch = await ensureWritableBranchNow();
        const sourceExists = sourceIdsRef.current.has(newItem.id);
        if (!sourceExists) {
          const attached = await addProjectSource.mutateAsync({
            branch_id: branch.id,
            expected_head_commit_id: headRef.current,
            source: { media_item_id: newItem.id },
            message: `Zuschnitt hinzugefügt: ${newItem.title}`,
          });
          headRef.current = attached.commit.id;
          sourceIdsRef.current.add(newItem.id);
        }
        // Build a new clip that points at the cut result. The transform is
        // set so the cropped video occupies the same screen rectangle the crop
        // region occupied — not the original clip's full-frame transform (which
        // would stretch the cropped portion to fill the whole stage).
        const newClip: TimelineClip = {
          id: safeId("clip"),
          kind: "VIDEO",
          source_media_item_id: newItem.id,
          source_start_us: 0,
          source_end_us: Math.max(1, Math.round(metadata.durationSeconds * 1_000_000)),
          timeline_start_us: oldClip.timeline_start_us,
          transform: targetTransform ?? oldClip.transform ?? { x: 0, y: 0, scale_x: 1, scale_y: 1, rotation: 0 },
          opacity: oldClip.opacity,
          audio_muted: oldClip.audio_muted,
          speed: oldClip.speed,
          effects: oldClip.effects ?? [],
        };
        const operations: Array<Record<string, unknown>> = [
          { type: "REMOVE_CLIP", sequence_id: readySequence.id, payload: { track_id: trackId, clip_id: oldClip.id } },
          { type: "ADD_CLIP", sequence_id: readySequence.id, payload: { track_id: trackId, clip: newClip } },
        ];
        const result = await createCommit.mutateAsync({
          branch_id: branchRef.current,
          expected_head_commit_id: headRef.current,
          message: `Video zugeschnitten (ersetzt ${oldClip.id})`,
          operations,
        });
        headRef.current = result.id;
        setSelectedTrackId(trackId);
        setSelectedClipId(newClip.id);
      });
      toast.show({ title: "Video zugeschnitten und ersetzt", variant: "success" });
    } catch (error) {
      toast.show({ title: "Zuschnitt konnte nicht eingefügt werden", description: errorMessage(error), variant: "error" });
      throw error;
    } finally {
      setEditorBusy(false);
    }
  }

  async function handleCutRegion(trackId: string, clip: TimelineClip, region: NormalizedRegion, targetTransform: CanvasTransform) {
    if (!trackId || !clip) return;
    cutTargetRef.current = { trackId, clip, targetTransform };
    try {
      const job = await startCutJob.mutateAsync({
        media_item_id: clip.source_media_item_id,
        output_lifecycle: "TEMPORARY",
        region,
        start_us: clip.source_start_us,
        end_us: clip.source_end_us,
        options: { preserve_audio: true, quality: "FINAL" },
      });
      setCutJobId(job.id);
      toast.show({ title: "Zuschneiden gestartet", description: "Das Video wird zugeschnitten …", variant: "info" });
    } catch (error) {
      toast.show({ title: "Zuschneiden konnte nicht gestartet werden", description: errorMessage(error), variant: "error" });
      cutTargetRef.current = null;
    }
  }

  async function addTrack() {
    const count = tracks.length + 1;
    await commitOperations("Layer hinzugefügt", [{
      type: "ADD_TRACK",
      sequence_id: readySequence.id,
      payload: { track: { id: safeId("layer"), type: "UNIVERSAL", name: `Layer ${count}` } },
    }]);
  }

  async function deleteTrack(track: TimelineTrack) {
    const count = Object.keys(track.clips ?? {}).length;
    if (!window.confirm(count ? `Layer und ${count} Clip(s) entfernen?` : "Leeren Layer entfernen?")) return;
    await commitOperations("Layer entfernt", [{ type: "REMOVE_TRACK", sequence_id: readySequence.id, payload: { track_id: track.id } }]);
  }

  async function renameTrack(track: TimelineTrack, name: string) {
    const normalized = name.trim();
    if (!normalized) throw new Error("Der Layername darf nicht leer sein.");
    await commitOperations("Layer umbenannt", [{ type: "RENAME_TRACK", sequence_id: readySequence.id, payload: { track_id: track.id, name: normalized } }]);
  }

  async function reorderTracks(trackIds: string[]) {
    const currentIds = tracks.map((track) => track.id);
    if (trackIds.length !== currentIds.length || trackIds.some((id) => !currentIds.includes(id))) {
      throw new Error("Die Layer-Reihenfolge ist nicht mehr aktuell.");
    }
    await commitOperations("Layer neu angeordnet", [{ type: "REORDER_TRACK", sequence_id: readySequence.id, payload: { order: trackIds } }]);
  }

  async function moveClip(trackId: string, clip: TimelineClip, targetTrackId: string, timelineStartUs: number) {
    const operations: Array<Record<string, unknown>> = [];
    if (targetTrackId === trackId) {
      operations.push({ type: "MOVE_CLIP", sequence_id: readySequence.id, payload: { track_id: trackId, clip_id: clip.id, timeline_start_us: Math.round(timelineStartUs) } });
    } else {
      operations.push({ type: "REMOVE_CLIP", sequence_id: readySequence.id, payload: { track_id: trackId, clip_id: clip.id } });
      operations.push({ type: "ADD_CLIP", sequence_id: readySequence.id, payload: { track_id: targetTrackId, clip: { ...clip, timeline_start_us: Math.round(timelineStartUs) } } });
    }
    await commitOperations("Clip verschoben", operations);
    setSelectedTrackId(targetTrackId);
    setSelectedClipId(clip.id);
  }

  async function trimClip(trackId: string, clip: TimelineClip, sourceStartUs: number, sourceEndUs: number, timelineStartUs: number) {
    const trimOperation: Record<string, unknown> = {
      type: "TRIM_CLIP",
      sequence_id: readySequence.id,
      payload: { track_id: trackId, clip_id: clip.id, source_start_us: Math.round(sourceStartUs), source_end_us: Math.round(sourceEndUs) },
    };
    const moveOperation: Record<string, unknown> = {
      type: "MOVE_CLIP",
      sequence_id: readySequence.id,
      payload: { track_id: trackId, clip_id: clip.id, timeline_start_us: Math.round(timelineStartUs) },
    };
    const timelineChanged = Math.round(timelineStartUs) !== Math.round(clip.timeline_start_us);
    // Extending the left edge must move the currently-shorter clip first and
    // only then restore its source range. Otherwise the temporary intermediate
    // state grows to the right and can overlap the following clip.
    const operations: Array<Record<string, unknown>> = !timelineChanged
      ? [trimOperation]
      : timelineStartUs < clip.timeline_start_us
        ? [moveOperation, trimOperation]
        : [trimOperation, moveOperation];
    await commitOperations("Clip getrimmt", operations);
  }

  async function splitClip(trackId: string, clip: TimelineClip) {
    const offsetTimeline = playheadRef.current - clip.timeline_start_us;
    if (offsetTimeline <= 0 || offsetTimeline >= clipDurationUs(clip)) throw new Error("Der Abspielkopf muss innerhalb des Clips liegen.");
    const splitSourceUs = Math.round(clip.source_start_us + offsetTimeline * Math.max(0.05, Number(clip.speed ?? 1)));
    const leftId = safeId("clip");
    const rightId = safeId("clip");
    await commitOperations("Clip geteilt", [{
      type: "SPLIT_CLIP",
      sequence_id: readySequence.id,
      payload: { track_id: trackId, clip_id: clip.id, split_source_us: splitSourceUs, left_clip_id: leftId, right_clip_id: rightId },
    }]);
    setSelectedTrackId(trackId);
    setSelectedClipId(rightId);
  }

  async function separateAudio(trackId: string, clip: TimelineClip) {
    const durationUs = clipDurationUs(clip);
    let audioTrack = tracks.find((track) => track.id !== trackId && canPlace(track, clip.timeline_start_us, durationUs));
    const operations: Array<Record<string, unknown>> = [];
    if (!audioTrack) {
      audioTrack = { id: safeId("layer"), type: "UNIVERSAL", name: `Layer ${tracks.length + 1}`, clips: {} };
      operations.push({ type: "ADD_TRACK", sequence_id: readySequence.id, payload: { track: audioTrack } });
    }
    const audioClip: TimelineClip = {
      ...clip,
      id: safeId("audio-clip"),
      kind: "AUDIO",
      audio_muted: false,
      transform: undefined,
      crop: undefined,
      opacity: 1,
      effects: [...(clip.effects ?? [])],
    };
    operations.push({ type: "ADD_CLIP", sequence_id: readySequence.id, payload: { track_id: audioTrack.id, clip: audioClip } });
    operations.push({ type: "SET_AUDIO_MUTE", sequence_id: readySequence.id, payload: { track_id: trackId, clip_id: clip.id, value: true } });
    await commitOperations("Video und Audio getrennt", operations);
    setSelectedTrackId(audioTrack.id);
    setSelectedClipId(audioClip.id);
  }

  async function duplicateClip(trackId: string, clip: TimelineClip) {
    const track = tracks.find((item) => item.id === trackId);
    if (!track) throw new Error("Layer wurde nicht gefunden.");
    const startUs = firstAvailableStartAtOrAfter(track, Math.round(clip.timeline_start_us + clipDurationUs(clip)), clipDurationUs(clip));
    const copy: TimelineClip = { ...clip, id: safeId("clip"), timeline_start_us: startUs, effects: [...(clip.effects ?? [])] };
    await commitOperations("Element dupliziert", [{ type: "ADD_CLIP", sequence_id: readySequence.id, payload: { track_id: trackId, clip: copy } }]);
    setSelectedTrackId(trackId);
    setSelectedClipId(copy.id);
  }

  async function addTextElement(preferredTrackId?: string, requestedStartUs?: number) {
    const content = window.prompt("Textinhalt", "Neuer Text");
    if (content === null || !content.trim()) return;
    const operations: Array<Record<string, unknown>> = [];
    const durationUs = 5_000_000;
    const startUs = Math.max(0, Math.round(requestedStartUs ?? playheadRef.current));
    const currentLayers = layersRef.current;
    let targetTrack = firstAvailableLayerAt(currentLayers, startUs, durationUs, preferredTrackId ?? selectedTrackId);
    if (!targetTrack) {
      targetTrack = { id: safeId("layer"), type: "UNIVERSAL", name: `Layer ${currentLayers.length + 1}`, clips: {} };
      operations.push({ type: "ADD_TRACK", sequence_id: readySequence.id, payload: { track: targetTrack } });
    }
    const clip: TimelineClip = {
      id: safeId("text"),
      kind: "TEXT",
      source_media_item_id: "",
      source_start_us: 0,
      source_end_us: durationUs,
      timeline_start_us: startUs,
      text: { content: content.trim(), font_size: 64, color: "#ffffff", font_weight: 700, align: "center" },
      transform: { x: 0.15, y: 0.4, scale_x: 0.7, scale_y: 0.2, rotation: 0 },
      opacity: 1,
      effects: [],
    };
    operations.push({ type: "ADD_CLIP", sequence_id: readySequence.id, payload: { track_id: targetTrack.id, clip } });
    await commitOperations("Text hinzugefügt", operations);
    setSelectedTrackId(targetTrack.id);
    setSelectedClipId(clip.id);
  }

  async function editTextElement(trackId: string, clip: TimelineClip) {
    const content = window.prompt("Text bearbeiten", clip.text?.content ?? "");
    if (content === null || !content.trim()) return;
    await commitOperations("Text bearbeitet", [{
      type: "SET_TEXT",
      sequence_id: readySequence.id,
      payload: { track_id: trackId, clip_id: clip.id, value: { ...(clip.text ?? {}), content: content.trim() } },
    }]);
  }

  async function addEffect(trackId: string, clip: TimelineClip, anchor: "START" | "END") {
    const durationUs = Math.min(1_000_000, Math.max(100_000, Math.round(clipDurationUs(clip) / 2)));
    const effect: TimelineEffect = { id: safeId("effect"), type: "FADE", anchor, duration_us: durationUs, enabled: true, parameters: {} };
    await commitOperations(`${anchor === "START" ? "Fade-In" : "Fade-Out"} hinzugefügt`, [{
      type: "ADD_EFFECT",
      sequence_id: readySequence.id,
      payload: { track_id: trackId, clip_id: clip.id, effect },
    }]);
  }

  async function updateEffectDuration(trackId: string, clip: TimelineClip, effect: TimelineEffect, durationUs: number) {
    await commitOperations("Effektdauer geändert", [{
      type: "UPDATE_EFFECT",
      sequence_id: readySequence.id,
      payload: { track_id: trackId, clip_id: clip.id, effect_id: effect.id, updates: { duration_us: Math.round(durationUs) } },
    }]);
  }

  async function removeEffect(trackId: string, clip: TimelineClip, effect: TimelineEffect) {
    await commitOperations("Effekt entfernt", [{
      type: "REMOVE_EFFECT",
      sequence_id: readySequence.id,
      payload: { track_id: trackId, clip_id: clip.id, effect_id: effect.id },
    }]);
  }

  async function removeClip(trackId: string, clip: TimelineClip) {
    await commitOperations("Clip entfernt", [{ type: "REMOVE_CLIP", sequence_id: readySequence.id, payload: { track_id: trackId, clip_id: clip.id } }]);
    setSelectedTrackId(null);
    setSelectedClipId(null);
  }

  async function toggleMute(trackId: string, clip: TimelineClip) {
    await commitOperations(Boolean(clip.audio_muted) ? "Ton eingeschaltet" : "Clip stummgeschaltet", [{
      type: "SET_AUDIO_MUTE",
      sequence_id: readySequence.id,
      payload: { track_id: trackId, clip_id: clip.id, value: !Boolean(clip.audio_muted) },
    }]);
  }

  async function commitTransform(trackId: string, clipId: string, transform: CanvasTransform) {
    await commitOperations("Element auf der Arbeitsfläche angepasst", [{
      type: "SET_TRANSFORM",
      sequence_id: readySequence.id,
      payload: { track_id: trackId, clip_id: clipId, value: transform },
    }]);
  }

  async function executeNaturalLanguage(rawCommand: string): Promise<string> {
    const command = rawCommand.trim();
    if (!command) throw new Error("Befehl ist leer.");

    // Build the context the LLM needs to interpret the command. The parser
    // itself runs in the backend (local LLM); the frontend only applies the
    // returned intent through the existing editor operations.
    const context: EditorCommandContext = {
      sequence: { width: readySequence.width, height: readySequence.height },
      playhead_seconds: playheadRef.current / 1_000_000,
      selected_clip: selectedClip ? {
        id: selectedClip.id,
        transform: selectedClip.transform ?? null,
        opacity: selectedClip.opacity ?? null,
        speed: typeof selectedClip.speed === "number" ? selectedClip.speed : null,
        audio_muted: typeof selectedClip.audio_muted === "boolean" ? selectedClip.audio_muted : null,
        source_start_us: selectedClip.source_start_us,
        source_end_us: selectedClip.source_end_us,
        timeline_start_us: selectedClip.timeline_start_us,
      } : null,
      tracks: tracks.map((track) => ({
        id: track.id,
        type: track.type ?? null,
        name: track.name ?? null,
        clip_count: Object.keys(track.clips ?? {}).length,
        selected: track.id === selectedTrackId,
      })),
    };

    const intent = await parseEditorCommand(command, context);
    return applyEditorIntent(intent);
  }

  function requireSelection(): { track: TimelineTrack; clip: TimelineClip } {
    if (!selectedTrack || !selectedClip) throw new Error("Dafür zuerst einen Clip auswählen.");
    return { track: selectedTrack, clip: selectedClip };
  }

  async function applyEditorIntent(intent: EditorCommandIntent): Promise<string> {
    const action = String(intent.action ?? "");
    switch (action) {
      case "play": setPlaying(true); return "Wiedergabe gestartet.";
      case "pause": setPlaying(false); return "Wiedergabe pausiert.";
      case "seek": {
        const seconds = Number(intent.seconds);
        if (!Number.isFinite(seconds)) throw new Error("Abspielposition konnte nicht gelesen werden.");
        setPlayheadUs(seconds * 1_000_000);
        return "Abspielkopf verschoben.";
      }
      case "split": {
        const { track, clip } = requireSelection();
        await splitClip(track.id, clip); return "Clip am Abspielkopf geteilt.";
      }
      case "separate_audio": {
        const { track, clip } = requireSelection();
        await separateAudio(track.id, clip); return "Video und Audio wurden getrennt.";
      }
      case "delete": {
        const { track, clip } = requireSelection();
        await removeClip(track.id, clip); return "Clip entfernt.";
      }
      case "duplicate": {
        const { track, clip } = requireSelection();
        await duplicateClip(track.id, clip); return "Clip dupliziert.";
      }
      case "mute": {
        const { track, clip } = requireSelection();
        if (!clip.audio_muted) await toggleMute(track.id, clip);
        return "Clip stummgeschaltet.";
      }
      case "unmute": {
        const { track, clip } = requireSelection();
        if (clip.audio_muted) await toggleMute(track.id, clip);
        return "Ton eingeschaltet.";
      }
      case "center": {
        const { track, clip } = requireSelection();
        const next = transformOf(clip);
        next.x = (1 - next.scale_x) / 2;
        next.y = (1 - next.scale_y) / 2;
        await commitTransform(track.id, clip.id, next);
        return "Clip zentriert.";
      }
      case "fit": {
        const { track, clip } = requireSelection();
        const next = transformOf(clip);
        await commitTransform(track.id, clip.id, { x: 0, y: 0, scale_x: 1, scale_y: 1, rotation: next.rotation });
        return "Clip an Arbeitsfläche angepasst.";
      }
      case "move": {
        const { track, clip } = requireSelection();
        const axis = String(intent.axis ?? "");
        const direction = String(intent.direction ?? "");
        const amount = Number(intent.amount ?? 10);
        const unit = String(intent.unit ?? "percent");
        const span = axis === "y" ? readySequence.height : readySequence.width;
        const magnitude = unit === "pixels" ? amount / Math.max(1, span) : amount / 100;
        const sign = direction === "left" || direction === "up" ? -1 : 1;
        const next = transformOf(clip);
        if (axis === "x") next.x += sign * magnitude;
        else if (axis === "y") next.y += sign * magnitude;
        else throw new Error("Verschiebungsachse konnte nicht gelesen werden.");
        await commitTransform(track.id, clip.id, next);
        return "Clip verschoben.";
      }
      case "scale": {
        const { track, clip } = requireSelection();
        const mode = String(intent.mode ?? "");
        const value = Number(intent.value ?? 10);
        const next = transformOf(clip);
        if (mode === "set") {
          const v = Math.max(0.03, value / 100);
          next.scale_x = v; next.scale_y = v;
        } else if (mode === "larger") {
          const factor = 1 + value / 100;
          next.scale_x *= factor; next.scale_y *= factor;
        } else if (mode === "smaller") {
          const factor = Math.max(0.03, 1 - value / 100);
          next.scale_x *= factor; next.scale_y *= factor;
        } else {
          throw new Error("Skalierungsmodus konnte nicht gelesen werden.");
        }
        await commitTransform(track.id, clip.id, next);
        return "Größe angepasst.";
      }
      case "rotate": {
        const { track, clip } = requireSelection();
        const degrees = Number(intent.degrees ?? 0);
        const next = transformOf(clip);
        next.rotation = degrees;
        await commitTransform(track.id, clip.id, next);
        return `Auf ${degrees}° rotiert.`;
      }
      case "opacity": {
        const { track, clip } = requireSelection();
        let value = Number(intent.value ?? 100);
        if (value > 1) value /= 100;
        value = Math.min(1, Math.max(0, value));
        await commitOperations("Deckkraft angepasst", [{ type: "SET_OPACITY", sequence_id: readySequence.id, payload: { track_id: track.id, clip_id: clip.id, value } }]);
        return `Deckkraft auf ${Math.round(value * 100)} % gesetzt.`;
      }
      case "speed": {
        const { track, clip } = requireSelection();
        const value = Math.min(16, Math.max(0.05, Number(intent.value ?? 1)));
        await commitOperations("Geschwindigkeit angepasst", [{ type: "SET_SPEED", sequence_id: readySequence.id, payload: { track_id: track.id, clip_id: clip.id, value } }]);
        return `Geschwindigkeit auf ${value}× gesetzt.`;
      }
      case "timeline_move": {
        const { track, clip } = requireSelection();
        const seconds = Number(intent.seconds ?? 0);
        await moveClip(track.id, clip, track.id, seconds * 1_000_000);
        return "Clip auf der Timeline verschoben.";
      }
      case "delete_track": {
        const trackType = String(intent.track_type ?? "").toLowerCase();
        const emptyOnly = Boolean(intent.empty_only);
        let target = tracks.find((track) => track.id === selectedTrackId) ?? null;
        if (trackType) {
          const candidates = tracks.filter((track) => (track.type ?? "").toLowerCase() === trackType);
          if (emptyOnly) {
            target = candidates.find((track) => Object.keys(track.clips ?? {}).length === 0) ?? null;
          } else {
            target = candidates[0] ?? target;
          }
        }
        if (!target) {
          if (trackType && emptyOnly) throw new Error(`Keine leere ${trackType}-Layer gefunden.`);
          if (trackType) throw new Error(`Keine ${trackType}-Layer gefunden.`);
          throw new Error("Kein Layer ausgewählt.");
        }
        if (emptyOnly && Object.keys(target.clips ?? {}).length > 0) {
          throw new Error("Der Layer ist nicht leer.");
        }
        await deleteTrack(target);
        return "Layer entfernt.";
      }
      case "add_track": {
        const newLayer = { id: safeId("layer"), type: "UNIVERSAL", name: `Layer ${tracks.length + 1}`, clips: {} };
        await commitOperations("Layer angelegt", [{ type: "ADD_TRACK", sequence_id: readySequence.id, payload: { track: newLayer } }]);
        setSelectedTrackId(newLayer.id);
        setSelectedClipId(null);
        return "Layer hinzugefügt.";
      }
      case "undo": {
        const currentId = readyProject.checkout_commit_id;
        const current = commitState.data?.id === currentId
          ? commitState.data
          : commitItems.find((commit) => commit.id === currentId);
        const parentId = current?.parent_ids?.[0];
        if (!parentId) throw new Error("Keine vorherige Version vorhanden.");
        await checkoutCommit.mutateAsync(parentId);
        detachedRef.current = parentId;
        setPlaying(false);
        return "Letzte Änderung rückgängig gemacht.";
      }
      case "redo": {
        const currentId = readyProject.checkout_commit_id;
        const current = commitState.data?.id === currentId
          ? commitState.data
          : commitItems.find((commit) => commit.id === currentId);
        const childId = current?.child_ids?.[0];
        if (!childId) throw new Error("Keine neuere Version vorhanden.");
        await checkoutCommit.mutateAsync(childId);
        detachedRef.current = childId;
        setPlaying(false);
        return "Änderung wiederhergestellt.";
      }
      case "unknown":
        throw new Error(String(intent.reason ?? "Befehl nicht erkannt."));
      default:
        throw new Error(`Unbekannte Aktion: ${action}`);
    }
  }

  async function handleRender() {
    setPlaying(false);
    setRenderJobId(null);
    setSavedRenderId(null);
    try {
      await startRenderMutation.mutateAsync({
        project_id: readyProjectId,
        sequence_id: readySequence.id,
        commit_id: readyProject.checkout_commit_id,
        output_lifecycle: "TEMPORARY",
        settings: { mode: "FINAL", video_codec: "libx264", audio_codec: "aac", include_audio: true },
      });
    } catch (error) {
      toast.show({ title: "Render konnte nicht gestartet werden", description: errorMessage(error), variant: "error" });
    }
  }

  async function selectCommit(commitId: string) {
    try {
      if (activeBranch && commitId === activeBranch.head_commit_id) {
        const result = await checkoutBranch.mutateAsync(activeBranch.id);
        const branch = result.branches.find((item) => item.id === activeBranch.id);
        if (branch) {
          branchRef.current = branch.id;
          headRef.current = branch.head_commit_id;
        }
        detachedRef.current = null;
      } else {
        await checkoutCommit.mutateAsync(commitId);
        detachedRef.current = commitId;
      }
      setPlaying(false);
    } catch (error) {
      toast.show({ title: "Version konnte nicht geöffnet werden", description: errorMessage(error), variant: "error" });
    }
  }

  const renderedItemId = renderJob.data?.library_item_id ?? null;
  const renderActive = ["QUEUED", "RUNNING", "RETRYING"].includes(renderJob.data?.status ?? "");

  return (
    <div className="project-workspace project-workspace--editor-v2">
      <header className="project-toolbar project-toolbar--minimal">
        <div className="project-toolbar__left">
          <Link className="btn btn--ghost btn--icon" to="/projects" aria-label="Zurück"><ArrowLeft size={16} /></Link>
          <div className="project-toolbar__name">
            <strong>{readyProject.name}</strong>
            <span>{readySequence.width} × {readySequence.height} · {Math.round(readySequence.fps_numerator / readySequence.fps_denominator)} FPS{readyProject.detached_commit_id ? " · Historische Version" : editorBusy ? " · Speichert …" : " · Gespeichert"}</span>
          </div>
        </div>
        <Button variant="primary" onClick={() => void handleRender()} loading={startRenderMutation.isPending || renderActive} disabled={timelineEndUs <= 0}><Video size={15} /> Final rendern</Button>
      </header>

      <div
        ref={editorGridRef}
        className="editor-v2-grid"
        style={{
          gridTemplateColumns: `minmax(0, 1fr) 1px ${sidePanelWidth}px`,
          gridTemplateRows: `minmax(240px, 1fr) 1px ${timelineHeight}px`,
        }}
      >
        <div className="editor-v2-resizer editor-v2-resizer--vertical" onPointerDown={beginSideResize} role="separator" aria-orientation="vertical" aria-label="Seitenleiste skalieren" />
        <div className="editor-v2-resizer editor-v2-resizer--horizontal editor-v2-resizer--h-left" onPointerDown={beginTimelineResize} role="separator" aria-orientation="horizontal" aria-label="Zeitleiste skalieren" />
        <div className="editor-v2-resizer editor-v2-resizer--corner" onPointerDown={beginCornerResize} role="separator" aria-orientation="vertical" aria-label="Seitenleiste und Zeitleiste skalieren" />
        <div className="editor-v2-resizer editor-v2-resizer--horizontal editor-v2-resizer--h-right" onPointerDown={beginTimelineResize} role="separator" aria-orientation="horizontal" aria-label="Zeitleiste skalieren" />
        <main className="editor-v2-main">
          <EditorCanvas
            sequence={readySequence}
            tracks={tracks}
            playheadUs={playheadUs}
            playing={playing}
            selectedTrackId={selectedTrackId}
            selectedClipId={selectedClipId}
            mediaTitles={mediaTitles}
            mediaFileTypes={mediaFileTypes}
            onSelect={(trackId, clipId) => { setSelectedTrackId(trackId); setSelectedClipId(clipId); }}
            onTransformCommit={(trackId, clipId, transform) => runEditorAction(() => commitTransform(trackId, clipId, transform))}
            onAddMedia={() => setAddMediaOpen(true)}
            onCutRegion={handleCutRegion}
            onTogglePlay={() => setPlaying((value) => !value)}
          />

          {renderJobId ? (
            <div className="render-strip render-strip--editor">
              <div>{renderActive ? <Loader2 size={15} className="spin" /> : <Check size={15} />}<span>{renderJob.data?.status === "COMPLETED" ? "Finaler Render fertig" : renderJob.data?.current_stage || "Render wird vorbereitet"}</span></div>
              <strong>{Math.round(renderJob.data?.progress ?? 0)} %</strong>
              {renderedItemId && renderJob.data?.status === "COMPLETED" ? (
                <>
                  <a className="btn btn--ghost btn--sm" href={libraryItemFileUrl(renderedItemId)}><Download size={14} /> Download</a>
                  {savedRenderId !== renderedItemId ? <Button size="sm" onClick={() => promoteRender.mutate(renderedItemId)} loading={promoteRender.isPending}><Save size={14} /> In Library</Button> : null}
                </>
              ) : null}
            </div>
          ) : null}
        </main>

        <EditorSidePanel
          checkoutCommitId={readyProject.checkout_commit_id}
          commits={commitItems}
          commitsLoading={commits.isLoading}
          hasMoreCommits={Boolean(commits.hasNextPage)}
          loadingMoreCommits={commits.isFetchingNextPage}
          onExecuteCommand={executeNaturalLanguage}
          onCheckoutCommit={selectCommit}
          onLoadMoreCommits={() => commits.fetchNextPage()}
        />

        <EditorTimeline
          sequence={readySequence}
          tracks={tracks}
          playheadUs={playheadUs}
          playing={playing}
          selectedTrackId={selectedTrackId}
          selectedClipId={selectedClipId}
          mediaTitles={mediaTitles}
          mediaFileTypes={mediaFileTypes}
          onPlayToggle={() => setPlaying((value) => !value)}
          onSeek={seekTimeline}
          onAddMedia={() => setAddMediaOpen(true)}
          onAddText={(trackId, timeUs) => runEditorAction(() => addTextElement(trackId, timeUs), "Text konnte nicht hinzugefügt werden")}
          onEditText={(trackId, clip) => runEditorAction(() => editTextElement(trackId, clip), "Text konnte nicht bearbeitet werden")}
          onSelect={(trackId, clipId) => { setSelectedTrackId(trackId); setSelectedClipId(clipId); }}
          onTrackSelect={(trackId) => { setSelectedTrackId(trackId); setSelectedClipId(null); }}
          onSplit={(trackId, clip) => runEditorAction(() => splitClip(trackId, clip), "Clip konnte nicht geteilt werden")}
          onSeparateAudio={(trackId, clip) => runEditorAction(() => separateAudio(trackId, clip), "Audio konnte nicht getrennt werden")}
          onDuplicate={(trackId, clip) => runEditorAction(() => duplicateClip(trackId, clip), "Clip konnte nicht dupliziert werden")}
          onDeleteClip={(trackId, clip) => runEditorAction(() => removeClip(trackId, clip), "Clip konnte nicht entfernt werden")}
          onToggleMute={(trackId, clip) => runEditorAction(() => toggleMute(trackId, clip), "Ton konnte nicht geändert werden")}
          onMoveClip={(trackId, clip, targetTrackId, timeUs) => runEditorAction(() => moveClip(trackId, clip, targetTrackId, timeUs), "Clip konnte nicht verschoben werden")}
          onTrimClip={(trackId, clip, sourceStartUs, sourceEndUs, timelineStartUs) => runEditorAction(() => trimClip(trackId, clip, sourceStartUs, sourceEndUs, timelineStartUs), "Clip konnte nicht getrimmt werden")}
          onAddTrack={() => runEditorAction(() => addTrack(), "Layer konnte nicht hinzugefügt werden")}
          onDeleteTrack={(track) => runEditorAction(() => deleteTrack(track), "Layer konnte nicht entfernt werden")}
          onRenameTrack={async (track, name) => {
            try {
              await renameTrack(track, name);
            } catch (error) {
              toast.show({ title: "Layer konnte nicht umbenannt werden", description: errorMessage(error), variant: "error" });
              throw error;
            }
          }}
          onReorderTracks={async (trackIds) => {
            try {
              await reorderTracks(trackIds);
            } catch (error) {
              toast.show({ title: "Layer konnten nicht verschoben werden", description: errorMessage(error), variant: "error" });
              throw error;
            }
          }}
          onAddEffect={(trackId, clip, anchor) => runEditorAction(() => addEffect(trackId, clip, anchor), "Effekt konnte nicht hinzugefügt werden")}
          onUpdateEffect={(trackId, clip, effect, durationUs) => runEditorAction(() => updateEffectDuration(trackId, clip, effect, durationUs), "Effekt konnte nicht geändert werden")}
          onRemoveEffect={(trackId, clip, effect) => runEditorAction(() => removeEffect(trackId, clip, effect), "Effekt konnte nicht entfernt werden")}
        />
      </div>

      <AddMediaDialog open={addMediaOpen} onOpenChange={setAddMediaOpen} onAdd={addMedia} busy={editorBusy || addProjectSource.isPending || createCommit.isPending} />
    </div>
  );
}
