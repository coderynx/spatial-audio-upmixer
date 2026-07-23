import { FolderPlus, Layers3, Music2, RefreshCw, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import type { Project } from "@/api";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";

export function ProjectsPage({ projects, loading, error, onRefresh, onDelete }: { projects: Project[]; loading: boolean; error: string | null; onRefresh: () => void; onDelete: (project: Project) => void }) {
  return <main className="mx-auto max-w-7xl p-4 sm:p-7">
    <div className="mb-6 flex items-start justify-between gap-4">
      <div><h1 className="text-2xl font-semibold tracking-tight">Projects</h1><p className="mt-1 text-sm text-muted-foreground">Editable spatial mixes with reusable project stems.</p></div>
      <Button variant="outline" size="sm" onClick={onRefresh}><RefreshCw />Refresh</Button>
    </div>
    {error && <p className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
    {loading ? <div className="h-48 animate-pulse rounded-md border bg-muted/30" /> : projects.length === 0 ? <div className="grid min-h-72 place-items-center rounded-lg border border-dashed p-8 text-center"><div><FolderPlus className="mx-auto mb-3 h-9 w-9 text-muted-foreground" /><h2 className="font-semibold">Create your first project</h2><p className="mt-1 max-w-sm text-sm text-muted-foreground">Import tracks once, separate stems in the background, then keep shaping the mix.</p><Button className="mt-5" asChild><Link to="/projects/new">New project</Link></Button></div></div> : <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {projects.map((project) => <Link key={project.id} to={`/projects/${project.id}`} className="rounded-lg border bg-card p-4 transition-colors hover:bg-muted/40">
        <div className="flex items-start justify-between gap-3"><div className="min-w-0"><h2 className="truncate font-semibold">{project.name}</h2><p className="mt-1 text-xs text-muted-foreground">{project.tracks.length} track{project.tracks.length === 1 ? "" : "s"} · {project.prepared_stems.length || project.requested_stems.length} stems</p></div><div className="flex shrink-0 items-center gap-1"><Music2 className="h-5 w-5 text-muted-foreground" /><Button variant="ghost" size="icon" aria-label={`Delete ${project.name}`} onClick={(event) => { event.preventDefault(); event.stopPropagation(); onDelete(project); }}><Trash2 className="text-muted-foreground" /></Button></div></div>
        <div className="mt-5 flex items-center justify-between text-xs"><span className="capitalize text-muted-foreground">{project.status.replace("_", " ")}</span><span>{Math.round(project.progress * 100)}%</span></div>
        <Progress className="mt-2" value={project.progress * 100} />
        <div className="mt-4 flex items-center gap-2 text-xs text-muted-foreground"><Layers3 className="h-3.5 w-3.5" />{project.status_message}</div>
      </Link>)}
    </div>}
  </main>;
}
