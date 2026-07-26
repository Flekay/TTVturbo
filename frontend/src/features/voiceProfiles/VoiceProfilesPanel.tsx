import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { useToast } from "../../components/ui/ToastProvider";
import { ProfileHeader } from "./ProfileHeader";
import { ProfileList } from "./ProfileList";
import { PromptBrowser } from "./PromptBrowser";
import { RecordingPackProgress } from "./RecordingPackProgress";
import {
  useAcceptReviewMutation,
  useAttachReferenceMutation,
  useCreateVoiceProfileMutation,
  useDeleteVoiceProfileMutation,
  useDetachReferenceMutation,
  usePatchVoiceProfileMutation,
  useVoiceProfileQuery,
  useVoiceProfilesQuery,
  useVoiceScriptsQuery,
} from "./hooks";
import type {
  VoiceProfile,
} from "./types";

type NameDialogMode =
  | { kind: "create" }
  | { kind: "rename"; profile: VoiceProfile }
  | null;

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

export function VoiceProfilesPanel() {
  const toast = useToast();
  const profilesQuery = useVoiceProfilesQuery();
  const scriptsQuery = useVoiceScriptsQuery();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [nameDialog, setNameDialog] = useState<NameDialogMode>(null);
  const [deleteTarget, setDeleteTarget] = useState<VoiceProfile | null>(null);

  const profileQuery = useVoiceProfileQuery(selectedId);

  const createMutation = useCreateVoiceProfileMutation();
  const patchMutation = usePatchVoiceProfileMutation();
  const deleteMutation = useDeleteVoiceProfileMutation();
  const attachMutation = useAttachReferenceMutation();
  const detachMutation = useDetachReferenceMutation();
  const acceptMutation = useAcceptReviewMutation();

  // Auto-select the first profile when the list loads, and drop a selection
  // that no longer exists in the freshest list (e.g. the profile was deleted
  // out-of-band). Without this, the detail query would keep 404-ing on a
  // stale id.
  useEffect(() => {
    if (!profilesQuery.data) return;
    const ids = new Set(profilesQuery.data.profiles.map((p) => p.id));
    if (selectedId === null) {
      const first = profilesQuery.data.profiles[0];
      if (first) setSelectedId(first.id);
    } else if (!ids.has(selectedId)) {
      const first = profilesQuery.data.profiles[0] ?? null;
      setSelectedId(first?.id ?? null);
    }
  }, [profilesQuery.data, selectedId]);

  const profiles = profilesQuery.data?.profiles ?? [];
  const scripts = scriptsQuery.data?.prompts ?? [];
  const selectedProfile = profileQuery.data ?? null;
  // The backend stores references as a dict keyed by script_id. Convert to
  // an array for the components that iterate over them.
  const references = useMemo(
    () => (selectedProfile?.references ? Object.values(selectedProfile.references) : []),
    [selectedProfile?.references],
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

  const handleDeleteConfirm = () => {
    if (!deleteTarget) return;
    const targetId = deleteTarget.id;
    // Pick the next profile from the *current* list (excluding the one being
    // deleted) so we don't briefly re-select the deleted id while the list
    // refetch is in flight — that would trigger a 404 on the detail query.
    const remaining = profiles.filter((p) => p.id !== targetId);
    const next = remaining[0] ?? null;
    deleteMutation.mutate(targetId, {
      onSuccess: () => {
        toast.show({ title: "Profil gelöscht", variant: "success" });
      },
      onError: (err) =>
        toast.show({
          title: "Löschen fehlgeschlagen",
          description: err instanceof Error ? err.message : "Unbekannter Fehler",
          variant: "error",
        }),
      onSettled: () => {
        // Always close the modal. Select the next profile directly (computed
        // above) instead of falling through to the auto-select effect, which
        // would read stale list data and re-pick the just-deleted id.
        setSelectedId(next?.id ?? null);
        setDeleteTarget(null);
      },
    });
  };

  const handleDetach = (scriptId: string) => {
    if (!selectedProfile) return;
    detachMutation.mutate(
      { profileId: selectedProfile.id, scriptId },
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

  const handleAttachReference = (scriptId: string, recordingFilename: string) => {
    if (!selectedProfile) return;
    attachMutation.mutate(
      {
        profileId: selectedProfile.id,
        scriptId,
        request: { recording_filename: recordingFilename },
      },
      {
        onSuccess: () => {
          toast.show({ title: "Referenz gespeichert", variant: "success" });
        },
        onError: (err) =>
          toast.show({
            title: "Speichern fehlgeschlagen",
            description: err instanceof Error ? err.message : "Unbekannter Fehler",
            variant: "error",
          }),
      },
    );
  };

  const handleAcceptReview = (scriptId: string) => {
    if (!selectedProfile) return;
    acceptMutation.mutate(
      { profileId: selectedProfile.id, scriptId },
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

  const anyMutationPending =
    createMutation.isPending ||
    patchMutation.isPending ||
    deleteMutation.isPending ||
    attachMutation.isPending ||
    detachMutation.isPending ||
    acceptMutation.isPending;

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
          <div className="voice-profiles-panel__detail">
            <ProfileHeader
              profile={selectedProfile}
              onRename={() => setNameDialog({ kind: "rename", profile: selectedProfile })}
              onDelete={() => setDeleteTarget(selectedProfile)}
              busy={anyMutationPending}
            />

            <section style={{ marginTop: 16 }}>
              <h3 className="vp-section-title">Fortschritt</h3>
              <RecordingPackProgress progress={selectedProfile.progress} />
            </section>

            <section style={{ marginTop: 16 }}>
              <h3 className="vp-section-title">Prompts</h3>
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
                  profileId={selectedProfile.id}
                  onAttachReference={handleAttachReference}
                  onDetachReference={handleDetach}
                  onAcceptReview={handleAcceptReview}
                  attachPendingScriptId={
                    attachMutation.isPending
                      ? attachMutation.variables?.scriptId ?? null
                      : null
                  }
                  detachPendingScriptId={
                    detachMutation.isPending
                      ? detachMutation.variables?.scriptId ?? null
                      : null
                  }
                  acceptPendingScriptId={
                    acceptMutation.isPending
                      ? acceptMutation.variables?.scriptId ?? null
                      : null
                  }
                />
              )}
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
          if (!open && !deleteMutation.isPending) setDeleteTarget(null);
        }}
        title="Profil löschen?"
        description={
          <>
            Das Profil und alle verknüpften WAV-Aufnahmen werden gelöscht.
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
