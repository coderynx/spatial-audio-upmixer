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
    <div className="space-y-2.5 rounded-lg border p-3">
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
        className="min-h-80 w-full rounded-lg border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed focus:outline-none focus:ring-2 focus:ring-ring/60"
      />
      {rawError && <p className="text-xs text-destructive">{rawError}</p>}
    </div>
  );
}
