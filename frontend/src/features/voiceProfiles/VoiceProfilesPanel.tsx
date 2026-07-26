import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ChevronRight } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { useToast } from "../../components/ui/ToastProvider";
import { ProfileHeader } from "./ProfileHeader";
import { ProfileList } from "./ProfileList";
import { PromptBrowser } from "./PromptBrowser";
import { PromptRecordingPanel } from "./PromptRecordingPanel";
import { RecordingPackProgress } from "./RecordingPackProgress";
import { HoldoutPanel } from "./HoldoutPanel";
import {
  useAcceptReviewMutation,
  useAttachReferenceMutation,
  useCreateVoiceProfileMutation,
  useDeleteVoiceProfileMutation,
  useDetachReferenceMutation,
  useHoldoutScriptsQuery,
  usePatchVoiceProfileMutation,
  useVoiceProfileQuery,
  useVoiceProfilesQuery,
  useVoiceScriptsQuery,
} from "./hooks";
import type {
  PromptRecordingRequest,
  VoiceProfile,
  VoiceProfilesPanelProps,
  VoiceScript,
} from "./types";

type NameDialogMode =
  | { kind: "create" }
  | { kind: "rename"; profile: VoiceProfile }
  | null;

function findNextScript(
  scripts: VoiceScript[],
  references: { script_id: string; status: string }[],
): VoiceScript | null {
  const refMap = new Map(references.map((r) => [r.script_id, r.status]));
  const ordered = [...scripts].sort((a, b) => a.order - b.order);
  for (const priority of ["__missing__", "REVIEW", "REJECTED"] as const) {
    for (const s of ordered) {
      const status = refMap.get(s.id);
      if (priority === "__missing__") {
        if (!status) return s;
      } else if (status === priority) {
        return s;
      }
    }
  }
  return null;
}

function NameDialog({
  mode,
  initialName,
  onClose,
  onSubmit,
  pending,
}: {
  mode: Exclude<NameDialogMode, null>;
  initialName: string;
  onClose: () => void;
  onSubmit: (name: string) => void;
  pending: boolean;
}) {
  const [name, setName] = useState(initialName);
  useEffect(() => {
    setName(initialName);
  }, [initialName, mode.kind]);

  const title = mode.kind === "create" ? "Neues Voice-Profil" : "Profil umbenennen";
  const label = mode.kind === "create" ? "Profilname" : "Neuer Name";

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !pending) onClose(); }}>
      <Dialog.Portal>
        <div className="dialog-root">
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="vp-name-dialog" onEscapeKeyDown={(e) => {
            if (pending) e.preventDefault();
          }}>
            <Dialog.Title className="vp-name-dialog__title">{title}</Dialog.Title>
            <Dialog.Description asChild>
              <p style={{ color: "var(--color-text-secondary)", fontSize: 13, margin: 0 }}>
                {mode.kind === "create"
                  ? "Lege Name und Locale für das neue Profil fest."
                  : "Gib einen neuen Namen für das Profil ein."}
              </p>
            </Dialog.Description>
            <form
              className="vp-name-dialog__field"
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim() && !pending) onSubmit(name.trim());
              }}
            >
              <label htmlFor="vp-name-input" className="sr-only">
                {label}
              </label>
              <input
                id="vp-name-input"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
                aria-label={label}
              />
              {mode.kind === "create" && (
                <>
                  <label htmlFor="vp-locale-input" className="sr-only">
                    Locale
                  </label>
                  <input
                    id="vp-locale-input"
                    type="text"
                    value="de-DE"
                    readOnly
                    aria-label="Locale"
                  />
                </>
              )}
              <div className="vp-name-dialog__actions">
                <button
                  type="button"
                  className="btn btn--secondary btn--sm"
                  onClick={() => {
                    if (!pending) onClose();
                  }}
                  disabled={pending}
                >
                  Abbrechen
                </button>
                <button
                  type="submit"
                  className="btn btn--primary btn--sm"
                  disabled={!name.trim() || pending}
                >
                  {pending ? "…" : "Speichern"}
                </button>
              </div>
            </form>
          </Dialog.Content>
        </div>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function VoiceProfilesPanel({ onStartPromptRecording }: VoiceProfilesPanelProps) {
  const toast = useToast();
  const profilesQuery = useVoiceProfilesQuery();
  const scriptsQuery = useVoiceScriptsQuery();
  const holdoutQuery = useHoldoutScriptsQuery();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedScriptId, setSelectedScriptId] = useState<string | null>(null);
  const [nameDialog, setNameDialog] = useState<NameDialogMode>(null);
  const [deleteTarget, setDeleteTarget] = useState<VoiceProfile | null>(null);

  const profileQuery = useVoiceProfileQuery(selectedId);

  const createMutation = useCreateVoiceProfileMutation();
  const patchMutation = usePatchVoiceProfileMutation();
  const deleteMutation = useDeleteVoiceProfileMutation();
  const attachMutation = useAttachReferenceMutation();
  const detachMutation = useDetachReferenceMutation();
  const acceptMutation = useAcceptReviewMutation();

  // Auto-select the first non-archived profile when the list loads.
  useEffect(() => {
    if (profilesQuery.data && selectedId === null) {
      const first = profilesQuery.data.profiles.find((p) => !p.archived);
      if (first) setSelectedId(first.id);
    }
  }, [profilesQuery.data, selectedId]);

  // Reset script selection when switching profiles.
  useEffect(() => {
    setSelectedScriptId(null);
  }, [selectedId]);

  const profiles = profilesQuery.data?.profiles ?? [];
  const scripts = scriptsQuery.data?.prompts ?? [];
  const holdouts = holdoutQuery.data?.prompts ?? [];
  const selectedProfile = profileQuery.data ?? null;
  // The backend stores references as a dict keyed by script_id. Convert to
  // an array for the components that iterate over them.
  const references = useMemo(
    () => (selectedProfile?.references ? Object.values(selectedProfile.references) : []),
    [selectedProfile?.references],
  );
  const selectedScript = useMemo(
    () => scripts.find((s) => s.id === selectedScriptId) ?? null,
    [scripts, selectedScriptId],
  );
  const selectedReference = useMemo(
    () => references.find((r) => r.script_id === selectedScriptId) ?? null,
    [references, selectedScriptId],
  );

  const nextScript = useMemo(
    () => findNextScript(scripts, references),
    [scripts, references],
  );

  const handleNameSubmit = (name: string) => {
    if (nameDialog?.kind === "create") {
      createMutation.mutate(
        { name, locale: "de-DE" },
        {
          onSuccess: (profile) => {
            toast.show({ title: "Profil erstellt", variant: "success" });
            setSelectedId(profile.id);
            setNameDialog(null);
          },
          onError: (err) => {
            toast.show({
              title: "Erstellen fehlgeschlagen",
              description: err instanceof Error ? err.message : "Unbekannter Fehler",
              variant: "error",
            });
          },
        },
      );
    } else if (nameDialog?.kind === "rename") {
      patchMutation.mutate(
        { id: nameDialog.profile.id, request: { name } },
        {
          onSuccess: () => {
            toast.show({ title: "Profil umbenannt", variant: "success" });
            setNameDialog(null);
          },
          onError: (err) => {
            toast.show({
              title: "Umbenennen fehlgeschlagen",
              description: err instanceof Error ? err.message : "Unbekannter Fehler",
              variant: "error",
            });
          },
        },
      );
    }
  };

  const handleArchiveToggle = () => {
    if (!selectedProfile) return;
    patchMutation.mutate(
      { id: selectedProfile.id, request: { archived: !selectedProfile.archived } },
      {
        onError: (err) =>
          toast.show({
            title: "Aktion fehlgeschlagen",
            description: err instanceof Error ? err.message : "Unbekannter Fehler",
            variant: "error",
          }),
      },
    );
  };

  const handleDeleteConfirm = () => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        toast.show({ title: "Profil gelöscht", variant: "success" });
        if (selectedId === deleteTarget.id) setSelectedId(null);
        setDeleteTarget(null);
      },
      onError: (err) =>
        toast.show({
          title: "Löschen fehlgeschlagen",
          description: err instanceof Error ? err.message : "Unbekannter Fehler",
          variant: "error",
        }),
    });
  };

  const handleAttach = (recordingFilename: string) => {
    if (!selectedProfile || !selectedScript) return;
    attachMutation.mutate(
      {
        profileId: selectedProfile.id,
        scriptId: selectedScript.id,
        request: { recording_filename: recordingFilename },
      },
      {
        onError: (err) =>
          toast.show({
            title: "Zuweisung fehlgeschlagen",
            description: err instanceof Error ? err.message : "Unbekannter Fehler",
            variant: "error",
          }),
      },
    );
  };

  const handleDetach = () => {
    if (!selectedProfile || !selectedScript) return;
    detachMutation.mutate(
      { profileId: selectedProfile.id, scriptId: selectedScript.id },
      {
        onError: (err) =>
          toast.show({
            title: "Trennen fehlgeschlagen",
            description: err instanceof Error ? err.message : "Unbekannter Fehler",
            variant: "error",
          }),
      },
    );
  };

  const handleAcceptReview = () => {
    if (!selectedProfile || !selectedScript) return;
    acceptMutation.mutate(
      { profileId: selectedProfile.id, scriptId: selectedScript.id },
      {
        onError: (err) =>
          toast.show({
            title: "Akzeptieren fehlgeschlagen",
            description: err instanceof Error ? err.message : "Unbekannter Fehler",
            variant: "error",
          }),
      },
    );
  };

  const handleStartRecording: ((req: PromptRecordingRequest) => void) | undefined =
    onStartPromptRecording;

  const anyMutationPending =
    createMutation.isPending ||
    patchMutation.isPending ||
    deleteMutation.isPending;

  return (
    <div className="voice-profiles-panel">
      <div>
        <ProfileList
          profiles={profiles}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onNewProfile={() => setNameDialog({ kind: "create" })}
          isLoading={profilesQuery.isLoading}
          isError={profilesQuery.isError}
          errorMessage={
            profilesQuery.error instanceof Error
              ? profilesQuery.error.message
              : "Unbekannter Fehler"
          }
          onRetry={() => void profilesQuery.refetch()}
        />
      </div>

      <div className="voice-profiles-panel__main">
        {profilesQuery.isLoading ? (
          <LoadingState message="Lade Profile …" />
        ) : profilesQuery.isError ? (
          // The list column already surfaces the error with a retry action;
          // the detail column only shows a neutral loading placeholder to
          // avoid duplicating the same error banner twice.
          <LoadingState message="Profile konnten nicht geladen werden." />
        ) : profiles.length === 0 ? (
          // The list (left column) already renders the empty state with a
          // "Neues Profil" action; the detail column stays empty.
          null
        ) : !selectedProfile ? (
          profileQuery.isLoading ? (
            <LoadingState message="Lade Profil …" />
          ) : profileQuery.isError ? (
            <ErrorState
              title="Profil konnte nicht geladen werden"
              message={
                profileQuery.error instanceof Error
                  ? profileQuery.error.message
                  : "Unbekannter Fehler"
              }
              onRetry={() => void profileQuery.refetch()}
            />
          ) : (
            <EmptyState
              title="Kein Profil ausgewählt"
              description="Wähle links ein Profil aus."
            />
          )
        ) : (
          <div className="voice-profiles-panel__scroll">
            <ProfileHeader
              profile={selectedProfile}
              onNewProfile={() => setNameDialog({ kind: "create" })}
              onRename={() => setNameDialog({ kind: "rename", profile: selectedProfile })}
              onArchive={handleArchiveToggle}
              onRestore={handleArchiveToggle}
              onDelete={() => setDeleteTarget(selectedProfile)}
              busy={anyMutationPending}
            />

            <section style={{ marginTop: 16 }}>
              <h3 className="vp-section-title">Fortschritt</h3>
              <RecordingPackProgress progress={selectedProfile.progress} />
            </section>

            <section style={{ marginTop: 16 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                <h3 className="vp-section-title">Prompts</h3>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    if (nextScript) setSelectedScriptId(nextScript.id);
                  }}
                  disabled={!nextScript}
                >
                  <ChevronRight size={14} /> Nächstes fehlendes Skript
                </Button>
              </div>
              {scriptsQuery.isLoading ? (
                <LoadingState message="Lade Skripte …" />
              ) : scriptsQuery.isError ? (
                <ErrorState
                  title="Skripte konnten nicht geladen werden"
                  message={
                    scriptsQuery.error instanceof Error
                      ? scriptsQuery.error.message
                      : "Unbekannter Fehler"
                  }
                  onRetry={() => void scriptsQuery.refetch()}
                />
              ) : scripts.length === 0 ? (
                <EmptyState
                  title="Keine Skripte verfügbar"
                  description="Der Server hat keine Aufnahmeskripte bereitgestellt."
                />
              ) : (
                <PromptBrowser
                  scripts={scripts}
                  references={references}
                  selectedScriptId={selectedScriptId}
                  onSelectScript={setSelectedScriptId}
                />
              )}
            </section>

            {selectedScript ? (
              <section style={{ marginTop: 16 }}>
                <h3 className="vp-section-title">Ausgewählter Prompt</h3>
                <PromptRecordingPanel
                  profile={selectedProfile}
                  script={selectedScript}
                  reference={selectedReference ?? null}
                  onStartPromptRecording={handleStartRecording}
                  onAttachRecording={handleAttach}
                  onDetachReference={handleDetach}
                  onAcceptReview={handleAcceptReview}
                  attachPending={attachMutation.isPending}
                  detachPending={detachMutation.isPending}
                  acceptPending={acceptMutation.isPending}
                />
              </section>
            ) : (
              <p className="page__description" style={{ marginTop: 16 }}>
                Wähle einen Prompt aus der Liste, um ihn aufzunehmen.
              </p>
            )}

            <section style={{ marginTop: 16 }}>
              <HoldoutPanel
                scripts={holdouts}
                isLoading={holdoutQuery.isLoading}
                isError={holdoutQuery.isError}
                errorMessage={
                  holdoutQuery.error instanceof Error
                    ? holdoutQuery.error.message
                    : undefined
                }
                onRetry={() => void holdoutQuery.refetch()}
              />
            </section>
          </div>
        )}
      </div>

      {nameDialog && (
        <NameDialog
          mode={nameDialog}
          initialName={nameDialog.kind === "rename" ? nameDialog.profile.name : ""}
          onClose={() => setNameDialog(null)}
          onSubmit={handleNameSubmit}
          pending={createMutation.isPending || patchMutation.isPending}
        />
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="Profil löschen?"
        description={
          <>
            Das Profil wird gelöscht.
            <br />
            Die zugrunde liegenden WAV-Aufnahmen bleiben erhalten.
          </>
        }
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
        busy={deleteMutation.isPending}
        destructive
      />
    </div>
  );
}
