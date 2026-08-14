import * as React from "react";
import { Save } from "lucide-react";
import { SelectField } from "@/components/forms/fields";
import { api, type Configuration, type Project } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
// Project-level identity and preview taste. Speaker layout is deliberately
// absent: it is per track (a track carries a mix per layout), chosen in the
// Prepare tab and selected in the tracks panel.
export function ProjectSettingsSection({
  project,
  configuration,
  onRename,
  onPreviewQualityChange,
}: {
  project: Project;
  configuration: Configuration | null;
  onRename: (name: string) => void;
  onPreviewQualityChange: (quality: string) => void;
}) {
  const [name, setName] = React.useState(project.name);
  React.useEffect(() => setName(project.name), [project.name]);
  const commitName = () => {
    const trimmed = name.trim();
    if (!trimmed || trimmed === project.name) { setName(project.name); return; }
    onRename(trimmed);
  };
  return (
    <div className="grid max-w-xl gap-4 rounded-md border p-4">
      <div className="space-y-2">
        <Label htmlFor="project-settings-name">Project name</Label>
        <Input
          id="project-settings-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          onBlur={commitName}
          onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
        />
      </div>
      <SelectField
        label="Preview audio quality"
        value={project.preview_quality}
        onChange={onPreviewQualityChange}
        options={(configuration?.choices.preview_qualities || ["low", "medium", "high"]).map((value) => ({ value, label: value }))}
        hint="Lower quality decodes and loads faster in the browser preview. Does not affect the exported/delivered master."
      />
      {project.tracks.length > 0 && (
        <div className="space-y-2 border-t pt-4">
          <Button variant="outline" size="sm" asChild>
            <a href={api.projectArchiveUrl(project.id)} download aria-label="Download project">
              <Save />
              Download project
            </a>
          </Button>
          <p className="text-[11px] text-muted-foreground">
            A portable .upmix.zip re-importable to an identical workspace — distinct from the Delivery tab's
            "Export project", which renders a deliverable mix, not a re-editable project.
          </p>
        </div>
      )}
    </div>
  );
}
