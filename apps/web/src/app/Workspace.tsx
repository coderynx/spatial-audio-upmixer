import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Pro-app page anatomy: toolbar / rail / canvas / inspector / status bar.
 *
 * The whole workspace fills exactly one viewport below the top bar and never
 * scrolls at page level — each region owns its own scroll, the way Logic Pro
 * and Final Cut lay out their panes. Regions are butted against each other
 * with 1px separators instead of gutters, so no gap is ever dead space. */
export function Workspace({
  toolbar,
  rail,
  inspector,
  status,
  children,
  className,
}: {
  toolbar?: ReactNode;
  rail?: ReactNode;
  inspector?: ReactNode;
  status?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex h-[calc(100vh-var(--topbar-h))] w-full flex-col overflow-hidden bg-background", className)}>
      {toolbar}
      <div className="flex min-h-0 flex-1">
        {rail && (
          <div className="hidden w-60 shrink-0 flex-col border-r bg-card xl:flex">
            {rail}
          </div>
        )}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
        {inspector && (
          <div className="hidden w-80 shrink-0 flex-col border-l bg-card xl:flex">
            {inspector}
          </div>
        )}
      </div>
      {status}
    </div>
  );
}

/** Scrolling region for a workspace column. Use for rail/inspector bodies and
 * for the canvas whenever its content can exceed the viewport. */
export function WorkspaceScroll({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("min-h-0 flex-1 overflow-y-auto", className)}>{children}</div>;
}
