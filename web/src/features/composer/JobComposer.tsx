import * as React from "react";
import {
  FileAudio,
  FolderOpen,
  Layers3,
  Play,
  RefreshCw,
  UploadCloud,
} from "lucide-react";
import {
  api,
  type Configuration,
  type ImportPreview,
  type Job,
  type MasteringReference,
} from "@/api";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatBytes } from "@/lib/format";
import {
  defaultManifest,
  normalizeManifest,
  type Manifest,
} from "@/lib/manifest";
import { droppedItems, type UploadItem } from "@/lib/uploads";
import { AlbumOverview } from "./AlbumOverview";
import { ManifestEditor } from "./ManifestEditor";

export function JobComposer({
  open,
  onOpenChange,
  remix,
  configuration,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (value: boolean) => void;
  remix: Job | null;
  configuration: Configuration | null;
  onCreated: () => void;
}) {
  const [items, setItems] = React.useState<UploadItem[]>([]);
  const [preview, setPreview] = React.useState<ImportPreview | null>(null);
  const [step, setStep] = React.useState<"upload" | "configure">("upload");
  const [name, setName] = React.useState("New spatial master");
  const [manifest, setManifest] = React.useState<Manifest>(defaultManifest);
  const [rawManifest, setRawManifest] = React.useState(
    JSON.stringify(defaultManifest, null, 2),
  );
  const [rawError, setRawError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [masteringReference, setMasteringReference] =
    React.useState<MasteringReference | null>(null);
  const [referenceUploading, setReferenceUploading] = React.useState(false);
  const [referenceError, setReferenceError] = React.useState<string | null>(
    null,
  );
  const fileInput = React.useRef<HTMLInputElement>(null);
  const folderInput = React.useRef<HTMLInputElement>(null);
  const separation = configuration?.capabilities.stem_separation;
  const stemUnavailable =
    manifest.engine.mode === "stem" && separation?.available === false;
  React.useEffect(() => {
    if (!open) return;
    setError(null);
    setRawError(null);
    if (remix) {
      const next = normalizeManifest(remix.manifest);
      setManifest(next);
      setRawManifest(JSON.stringify(next, null, 2));
      setName(`${remix.name} remix`);
      setMasteringReference(remix.mastering_reference || null);
      setReferenceError(null);
      setReferenceUploading(false);
      setStep("configure");
      setPreview(null);
      let active = true;
      void api
        .getImport(remix.import_id)
        .then((result) => {
          if (active) setPreview(result);
        })
        .catch((nextError) => {
          if (active) setError((nextError as Error).message);
        });
      return () => {
        active = false;
      };
    }
    const next = normalizeManifest(
      defaultManifest as unknown as Record<string, unknown>,
    );
    setManifest(next);
    setRawManifest(JSON.stringify(next, null, 2));
    setName("New spatial master");
    setStep("upload");
    setPreview(null);
    setItems([]);
    setMasteringReference(null);
    setReferenceError(null);
    setReferenceUploading(false);
  }, [open, remix]);
  React.useEffect(() => {
    if (!open || remix || separation?.available !== false) return;
    setManifest((current) =>
      current.engine.mode === "stem"
        ? { ...current, engine: { ...current.engine, mode: "realtime" } }
        : current,
    );
  }, [open, remix, separation?.available]);
  React.useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, [open]);
  const importFiles = async (nextItems: UploadItem[]) => {
    if (!nextItems.length) return;
    setItems(nextItems);
    setBusy(true);
    setError(null);
    try {
      const result = await api.upload(nextItems);
      setPreview(result);
      setName(
        result.title ? `${result.title} spatial master` : "New spatial master",
      );
      setStep("configure");
    } catch (nextError) {
      setError((nextError as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const submit = async (start: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const payload = {
        name,
        manifest: manifest as unknown as Record<string, unknown>,
        mastering_reference_id: masteringReference?.id || null,
        start,
      };
      if (remix) await api.cloneJob(remix.id, payload);
      else if (preview)
        await api.createJob({ import_id: preview.id, ...payload });
      else throw new Error("Upload source audio first");
      onOpenChange(false);
      onCreated();
    } catch (nextError) {
      setError((nextError as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const uploadMasteringReference = async (file: File) => {
    const importId = preview?.id || remix?.import_id;
    if (!importId) {
      setReferenceError("Upload source audio before choosing a reference.");
      return;
    }
    setReferenceUploading(true);
    setReferenceError(null);
    try {
      setMasteringReference(await api.uploadMasteringReference(importId, file));
    } catch (nextError) {
      setReferenceError((nextError as Error).message);
    } finally {
      setReferenceUploading(false);
    }
  };
  const updateRaw = (value: string) => {
    setRawManifest(value);
    try {
      setManifest(
        normalizeManifest(JSON.parse(value) as Record<string, unknown>),
      );
      setRawError(null);
    } catch (nextError) {
      setRawError((nextError as Error).message);
    }
  };
  const selectFiles = (files: FileList | null) => {
    if (files)
      void importFiles(
        Array.from(files).map((file) => ({
          file,
          path: file.webkitRelativePath || file.name,
        })),
      );
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {remix ? "Create cached remix" : "Create upmix job"}
          </DialogTitle>
          <DialogDescription>
            {remix
              ? "Reuse compatible separated stems while changing mixing or mastering settings."
              : step === "upload"
                ? "1. Import source audio"
                : "2. Configure render settings"}
          </DialogDescription>
        </DialogHeader>
        {step === "upload" ? (
          <div className="space-y-4">
            <div
              onDragOver={(event) => event.preventDefault()}
              onDrop={async (event) => {
                event.preventDefault();
                try {
                  await importFiles(await droppedItems(event));
                } catch (nextError) {
                  setError((nextError as Error).message);
                }
              }}
              className="flex min-h-56 flex-col items-center justify-center rounded-md border border-dashed p-6 text-center"
            >
              <div className="mb-4 rounded-md bg-muted p-3">
                {busy ? (
                  <RefreshCw className="h-7 w-7 animate-spin" />
                ) : (
                  <UploadCloud className="h-7 w-7" />
                )}
              </div>
              <h3 className="font-semibold">
                {busy ? "Importing audio" : "Drop audio, album folder, or ZIP"}
              </h3>
              <p className="mt-1 max-w-md text-sm text-muted-foreground">
                WAV and FLAC files upload immediately. Album metadata and
                artwork are detected automatically.
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => fileInput.current?.click()}
                >
                  <FileAudio />
                  Choose files or ZIP
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => folderInput.current?.click()}
                >
                  <FolderOpen />
                  Choose album folder
                </Button>
              </div>
              <input
                ref={fileInput}
                className="hidden"
                type="file"
                multiple
                accept="audio/wav,audio/flac,.zip"
                onChange={(event) => {
                  selectFiles(event.target.files);
                  event.currentTarget.value = "";
                }}
              />
              <input
                ref={folderInput}
                className="hidden"
                type="file"
                multiple
                onChange={(event) => {
                  selectFiles(event.target.files);
                  event.currentTarget.value = "";
                }}
              />
            </div>
            {items.length > 0 && (
              <div className="flex items-center justify-between rounded-md border bg-muted/30 p-3">
                <div>
                  <p className="text-sm font-medium">
                    {items.length} item{items.length === 1 ? "" : "s"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatBytes(
                      items.reduce((total, item) => total + item.file.size, 0),
                    )}
                  </p>
                </div>
                {error && (
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => void importFiles(items)}
                  >
                    <RefreshCw />
                    Retry
                  </Button>
                )}
              </div>
            )}
            {error && (
              <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <section className="space-y-3">
              <div>
                <h3 className="text-base font-semibold">Source</h3>
                <p className="text-xs text-muted-foreground">
                  Metadata and source audio preview.
                </p>
              </div>
              {preview ? (
                <AlbumOverview preview={preview} />
              ) : (
                <div className="h-48 animate-pulse rounded-md border bg-muted/40" />
              )}
            </section>
            {remix && (
              <div className="flex items-center gap-3 rounded-md border bg-muted/30 p-3">
                <Layers3 className="text-muted-foreground" />
                <div>
                  <p className="text-sm font-medium">Stem cache connected</p>
                  <p className="text-xs text-muted-foreground">
                    Compatible stems from “{remix.name}” will be reused.
                  </p>
                </div>
              </div>
            )}
            <section className="space-y-3">
              <div>
                <h3 className="text-base font-semibold">Job configuration</h3>
                <p className="text-xs text-muted-foreground">
                  User-controlled manifest settings.
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="job-name">Job name</Label>
                <Input
                  id="job-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
            </section>
            <ManifestEditor
              manifest={manifest}
              setManifest={setManifest}
              configuration={configuration}
              rawManifest={rawManifest}
              rawError={rawError}
              onRawChange={updateRaw}
              masteringReference={masteringReference}
              referenceUploading={referenceUploading}
              referenceError={referenceError}
              onReferenceUpload={(file) => void uploadMasteringReference(file)}
              onReferenceClear={() => {
                setMasteringReference(null);
                setReferenceError(null);
              }}
            />
            {stemUnavailable && (
              <p className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">
                {separation?.install_message}
              </p>
            )}
            {error && (
              <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </p>
            )}
            <div className="sticky -bottom-5 -mb-5 z-20 isolate flex flex-wrap justify-end gap-2 border-t bg-background pb-4 pt-3">
              <Button
                variant="outline"
                disabled={busy || referenceUploading || Boolean(rawError)}
                onClick={() => void submit(false)}
              >
                Save paused
              </Button>
              <Button
                disabled={
                  busy ||
                  referenceUploading ||
                  Boolean(rawError) ||
                  stemUnavailable
                }
                onClick={() => void submit(true)}
              >
                {busy ? <RefreshCw className="animate-spin" /> : <Play />}Start
                upmix
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
