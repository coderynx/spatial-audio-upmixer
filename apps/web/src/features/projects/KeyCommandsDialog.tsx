import * as React from "react";
import { Keyboard } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { IS_MAC, KEY_COMMANDS, keyCommandCaps, type KeyCommandGroup } from "./keyCommands";

const GROUP_ORDER: KeyCommandGroup[] = ["Transport", "Mixer", "Help"];

function KeyCap({ cap }: { cap: { glyph: string; word: string } }) {
  return (
    <kbd className="inline-flex min-w-[1.4em] items-center justify-center rounded-md border bg-secondary px-1.5 py-px text-center font-mono text-[11px] font-medium text-foreground">
      <span aria-hidden="true">{cap.glyph}</span>
      <span className="sr-only">{cap.word}</span>
    </kbd>
  );
}

/** Reference card for the project view's Logic Pro-style key commands (see
 * `keyCommands.ts`) — renders both its own status-bar trigger and the
 * dialog, so mounting one element gives the page both the affordance and
 * the content. Purely presentational: it reads the shared binding table but
 * never `useKeyCommands`, so it stays a leaf the page can render regardless
 * of which stage is active. */
export function KeyCommandsDialog({
  open,
  onOpenChange,
  mac = IS_MAC,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mac?: boolean;
}) {
  const groups = React.useMemo(
    () => GROUP_ORDER.map((group) => [group, KEY_COMMANDS.filter((command) => command.group === group)] as const),
    [],
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <button
          type="button"
          aria-label="Keyboard shortcuts"
          title="Keyboard shortcuts (?)"
          className="flex shrink-0 items-center gap-1 rounded-md px-1 text-[11px] uppercase tracking-[.06em] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <Keyboard className="h-3 w-3" aria-hidden="true" />Keys
        </button>
      </DialogTrigger>
      <DialogContent data-key-commands className="w-[min(760px,92vw)]">
        <div className="max-h-[80vh] overflow-auto p-4">
          <DialogHeader>
            <DialogTitle>Keyboard shortcuts</DialogTitle>
            <DialogDescription>Logic Pro key commands mapped to the project view.</DialogDescription>
          </DialogHeader>
          <div className="mt-4 grid gap-x-8 gap-y-4 sm:grid-cols-2">
            {groups.map(([group, commands]) => (
              <section key={group}>
                <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-[.06em] text-muted-foreground">{group}</h3>
                <dl>
                  {commands.map((command) => (
                    <div key={command.id} className={cn("flex items-baseline justify-between gap-3 py-0.5")}>
                      <dt className="text-[13px]">{command.label}</dt>
                      <dd className="flex shrink-0 items-center gap-1">
                        {keyCommandCaps(command, mac).map((cap, index) => (
                          <KeyCap key={index} cap={cap} />
                        ))}
                        {command.numpad && <span className="text-[11px] text-muted-foreground">keypad</span>}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
