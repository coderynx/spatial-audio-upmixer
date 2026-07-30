import * as React from "react";
import { Plus, Play, UploadCloud } from "lucide-react";
import { api, type Configuration, type Project } from "@/api";
import { EmptyState } from "@/app/EmptyState";
import { PanelBody, PanelHeader } from "@/app/Panel";
import { Button } from "@/components/ui/button";
import { fallbackStems } from "@/lib/manifest";
import { normalizeStemHierarchy } from "@/lib/stemHierarchy";
import { droppedItems, type UploadItem } from "@/lib/uploads";
import { cn } from "@/lib/utils";
import { PreparationPanel } from "../PreparationView";
import { AssetStagingRow, type StagedAsset } from "./AssetStagingRow";
import { PreparedTrackTree } from "./PreparedTrackTree";

let stagingCounter = 0;
function nextStagingId() {
  stagingCounter += 1;
  return `staged-${stagingCounter}`;
}

type Defaults = Omit<StagedAsset, "localId" | "file">;

/** Project-level Assets tab: upload one or more files with per-file
 * extraction settings, then watch/browse the prepared Track → Stem → Zone
 * tree. Assets is project-wide (unlike Mixing/Mastering/Delivery, which are
 * per-track), so it carries no track switcher of its own. */
export function AssetsTab({
  project,
  configuration,
  onProjectUpdate,
  onOpenTrack,
  onRetry,
}: {
  project: Project;
  configuration: Configuration | null;
  onProjectUpdate: (project: Project) => void;
  onOpenTrack: (trackId: string) => void;
  onRetry: () => void;
}) {
  const choices = configuration?.choices;
  const availableStems = choices?.stems || fallbackStems;
  const sampleRates = choices?.sample_rates || [44100, 48000, 88200, 96000, 192000];
  const subtypes = choices?.output_subtypes || ["PCM_16", "PCM_24", "PCM_32", "FLOAT"];
  const channelLayouts = choices?.channel_layouts || ["5.1", "7.1", "5.1.2", "5.1.4", "7.1.2", "7.1.4"];
  const projectLayout = (project.manifest as { mixing?: { channel_layout?: string } }).mixing?.channel_layout;

  const defaultSettings = React.useCallback((): Defaults => ({
    stems: normalizeStemHierarchy(project.requested_stems.length ? project.requested_stems : availableStems.slice(0, 6)),
    sampleRate: sampleRates[0],
    subtype: subtypes.includes("PCM_24") ? "PCM_24" : subtypes[0],
    channelLayout: projectLayout || channelLayouts[channelLayouts.length - 1],
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stable option lists derived from `configuration`, not worth re-deriving the callback identity for
  }), [project.requested_stems, projectLayout]);

  const [staged, setStaged] = React.useState<StagedAsset[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [dragOver, setDragOver] = React.useState(false);
  const fileInput = React.useRef<HTMLInputElement>(null);

  const stageItems = (items: UploadItem[]) => {
    const defaults = defaultSettings();
    setStaged((current) => [
      ...current,
      ...items.map((item) => ({ localId: nextStagingId(), file: item.file, ...defaults })),
    ]);
  };

  const applyDefaultsToAll = () => {
    const defaults = defaultSettings();
    setStaged((current) => current.map((item) => ({ ...item, ...defaults })));
  };

  const updateStaged = (localId: string, patch: Partial<StagedAsset>) =>
    setStaged((current) => current.map((item) => (item.localId === localId ? { ...item, ...patch } : item)));

  const removeStaged = (localId: string) =>
    setStaged((current) => current.filter((item) => item.localId !== localId));

  const startPreparation = async () => {
    if (!staged.length) return;
    setBusy(true);
    setError(null);
    try {
      const imported = await api.upload(staged.map((item) => ({ file: item.file, path: item.file.name })));
      const perAssetOverrides: Record<string, Record<string, unknown>> = {};
      imported.assets.forEach((asset, index) => {
        const settings = staged[index];
        if (!settings) return;
        perAssetOverrides[asset.id] = {
          engine: { stems: settings.stems },
          format: { sample_rate: settings.sampleRate, subtype: settings.subtype },
          mixing: { channel_layout: settings.channelLayout },
        };
      });
      const next = await api.addProjectAssets(project.id, { import_id: imported.id, per_asset_overrides: perAssetOverrides });
      onProjectUpdate(next);
      setStaged([]);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onFilesPicked = (files: FileList | null) => {
    if (!files?.length) return;
    stageItems(Array.from(files).map((file) => ({ file, path: file.webkitRelativePath || file.name })));
  };

  const activelyPreparing = ["queued", "preparing", "expanding"].includes(project.status);
  const anyTrackReady = project.tracks.some((track) => track.status === "ready");
  // The rich preparation log is most useful before anything has finished —
  // once a first track is ready, the tree below (with its own per-track
  // progress row for anything still in flight) is the more useful view.
  const showLog = activelyPreparing && !anyTrackReady;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
      <div
        onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          void droppedItems(event).then(stageItems);
        }}
        className={cn(
          "flex min-h-0 flex-col overflow-hidden rounded-lg border bg-card transition-colors",
          dragOver && "ring-2 ring-primary/60",
        )}
      >
        <PanelHeader
          title="Add tracks"
          actions={
            staged.length > 1 ? (
              <Button variant="ghost" size="sm" onClick={applyDefaultsToAll}>
                Apply defaults to all
              </Button>
            ) : undefined
          }
        />
        <PanelBody className="space-y-3">
          {staged.length === 0 ? (
            <EmptyState
              icon={UploadCloud}
              title="Upload one or more tracks"
              description="WAV and FLAC audio, or a ZIP of either. Each file gets its own stems, sample rate, bit depth, and channel layout."
              action={
                <Button size="sm" variant="outline" onClick={() => fileInput.current?.click()}>
                  <Plus />
                  Choose audio
                </Button>
              }
            />
          ) : (
            <>
              {staged.map((item) => (
                <AssetStagingRow
                  key={item.localId}
                  asset={item}
                  availableStems={availableStems}
                  sampleRates={sampleRates}
                  subtypes={subtypes}
                  channelLayouts={channelLayouts}
                  onChange={(patch) => updateStaged(item.localId, patch)}
                  onRemove={() => removeStaged(item.localId)}
                />
              ))}
              <div className="flex items-center gap-2 border-t pt-3">
                <Button variant="outline" size="sm" onClick={() => fileInput.current?.click()}>
                  <Plus />
                  Add more files
                </Button>
                <div className="min-w-0 flex-1" />
                <Button disabled={busy} onClick={() => void startPreparation()}>
                  <Play />
                  {busy ? "Starting…" : `Start preparation · ${staged.length} file${staged.length === 1 ? "" : "s"}`}
                </Button>
              </div>
            </>
          )}
          <input
            ref={fileInput}
            type="file"
            multiple
            accept="audio/wav,audio/flac,.zip"
            className="hidden"
            onChange={(event) => { onFilesPicked(event.target.files); event.currentTarget.value = ""; }}
          />
          {error && <p className="text-xs text-destructive">{error}</p>}
        </PanelBody>
      </div>

      {showLog && <PreparationPanel project={project} onRetry={onRetry} />}

      {project.tracks.length > 0 && (
        <PreparedTrackTree project={project} configuration={configuration} onOpenTrack={onOpenTrack} />
      )}
    </div>
  );
}
