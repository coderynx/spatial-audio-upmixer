import * as React from "react";
import { SelectField } from "@/components/forms/fields";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Configuration, Project } from "@/api";
import type { Manifest } from "@/lib/manifest";

// Project-level identity and speaker layout. Layout lives here (not
// Delivery) since it is the single control both the spatial preview graph
// and the audio preview engine key off of — see useStemPreview's
// `layoutChannels` argument.
export function ProjectSettingsSection({
  project,
  manifest,
  configuration,
  onRename,
  onChange,
}: {
  project: Project;
  manifest: Manifest;
  configuration: Configuration | null;
  onRename: (name: string) => void;
  onChange: (next: Manifest) => void;
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
        label="Speaker layout"
        value={manifest.mixing.channel_layout}
        onChange={(channel_layout) =>
          onChange({ ...manifest, mixing: { ...manifest.mixing, channel_layout } })
        }
        options={(
          configuration?.choices.channel_layouts || ["5.1", "7.1", "5.1.2", "5.1.4", "7.1.2", "7.1.4"]
        ).map((value) => ({ value, label: value }))}
        hint="Changes the routing graph, spatial preview, and audio preview engine to the exact speaker set of this layout."
      />
    </div>
  );
}
