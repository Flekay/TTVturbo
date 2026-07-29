import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Film, FolderKanban, Plus, Search, Trash2 } from "lucide-react";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { useLibraryItemsQuery } from "../features/library/hooks";
import { useCreateProject, useDeleteProject, useProjects } from "../features/projects/hooks";
import type { EditProjectSummary } from "../features/projects/api";
import { formatDateTime } from "../utils/format";
import { useUIStore } from "../stores/uiStore";

export function ProjectsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedSource = searchParams.get("source");
  const projects = useProjects();
  const library = useLibraryItemsQuery();
  const create = useCreateProject();
  const remove = useDeleteProject();
  const use24h = useUIStore((state) => state.use24HourFormat);
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [deleting, setDeleting] = useState<EditProjectSummary | null>(null);

  useEffect(() => {
    if (!requestedSource) return;
    setSourceId(requestedSource);
    setCreating(true);
  }, [requestedSource]);

  if (projects.isError) {
    return <ErrorState title="Projekte konnten nicht geladen werden" message={projects.error instanceof Error ? projects.error.message : "Unbekannter Fehler"} onRetry={() => void projects.refetch()} />;
  }

  const filtered = (projects.data ?? []).filter((project) => project.name.toLowerCase().includes(search.trim().toLowerCase()));

  async function handleCreate() {
    if (!name.trim() || !sourceId) return;
    const project = await create.mutateAsync({ name: name.trim(), sources: [{ media_item_id: sourceId }] });
    setCreating(false);
    setName("");
    setSourceId("");
    navigate(`/projects/${project.id}`);
  }

  return (
    <div className="page projects-page">
      <div className="page-toolbar">
        <div className="list-controls__search-wrap projects-search">
          <Search size={16} className="list-controls__search-icon" />
          <input className="list-controls__search" placeholder="Projekte durchsuchen …" value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}><Plus size={15} /> Neues Projekt</Button>
      </div>

      {creating && (
        <Card className="project-create-card" title="Neues Bearbeitungsprojekt">
          <div className="field-grid field-grid--2">
            <label>Projektname<input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="Zum Beispiel: Daily Game Recommendation" autoFocus /></label>
            <label>Quelle<select className="input" value={sourceId} onChange={(event) => setSourceId(event.target.value)}><option value="">Medium auswählen …</option>{(library.data?.items ?? []).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
          </div>
          <div className="form-actions"><Button variant="ghost" onClick={() => setCreating(false)}>Abbrechen</Button><Button variant="primary" onClick={() => void handleCreate()} loading={create.isPending} disabled={!name.trim() || !sourceId}>Projekt erstellen</Button></div>
        </Card>
      )}

      {projects.isLoading ? (
        <div className="state">Projekte werden geladen …</div>
      ) : filtered.length === 0 ? (
        <EmptyState title={search ? "Keine Treffer" : "Noch keine Projekte"} description={search ? "Kein Projekt entspricht deiner Suche." : "Erstelle ein Projekt, sobald du mehrere Clips, Ausgaben oder Versionen bearbeiten möchtest."} action={!search ? <Button variant="primary" onClick={() => setCreating(true)}><Plus size={15} /> Projekt erstellen</Button> : undefined} />
      ) : (
        <div className="project-grid">
          {filtered.map((project) => (
            <article className="project-card" key={project.id}>
              <Link className="project-card__open" to={`/projects/${project.id}`}>
                <div className="project-card__thumb"><FolderKanban size={30} /></div>
                <div className="project-card__body"><strong>{project.name}</strong><span>{project.sequence_count} Ausgaben · {project.branch_count} Varianten</span><small>Bearbeitet {formatDateTime(project.updated_at, use24h)}</small></div>
              </Link>
              <Button variant="ghost" size="sm" aria-label="Projekt löschen" onClick={() => setDeleting(project)}><Trash2 size={14} /></Button>
            </article>
          ))}
        </div>
      )}

      <div className="projects-note"><Film size={15} /> Ein einzelnes Upscale oder generiertes Video benötigt kein Projekt. Dafür ist <Link to="/create">Create</Link> gedacht.</div>

      <ConfirmDialog
        open={Boolean(deleting)}
        onOpenChange={(open) => { if (!open) setDeleting(null); }}
        title="Projekt löschen?"
        description={deleting ? `„${deleting.name}“ und seine Timeline-History werden gelöscht. Die Originalmedien bleiben in der Library.` : ""}
        confirmLabel="Projekt löschen"
        destructive
        busy={remove.isPending}
        onConfirm={async () => { if (!deleting) return; await remove.mutateAsync(deleting.id); setDeleting(null); }}
      />
    </div>
  );
}
