import { Label } from "@/components/ui/label";

export function AdvancedSection({
  rawManifest,
  rawError,
  onChange,
}: {
  rawManifest: string;
  rawError: string | null;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-3 rounded-md border p-4">
      <div>
        <Label htmlFor="manifest-json">Complete job manifest</Label>
        <p className="mt-1 text-xs text-muted-foreground">
          Every CLI manifest key is accepted. Server-owned input, output, and
          cache paths are injected at execution.
        </p>
      </div>
      <textarea
        id="manifest-json"
        spellCheck={false}
        value={rawManifest}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-80 w-full rounded-md border bg-muted/30 p-4 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-ring"
      />
      {rawError && <p className="text-xs text-destructive">{rawError}</p>}
    </div>
  );
}
