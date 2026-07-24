import type { ReactNode } from "react";
import {
  AudioLines,
  Gauge,
  HardDrive,
  Layers3,
  RefreshCw,
  Settings2,
} from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import type { Configuration } from "@/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { HeaderSlotProvider, useHeaderSlot } from "./HeaderSlot";
import { ThemeToggle } from "./ThemeToggle";

function CapabilityStatus({
  configuration,
}: {
  configuration: Configuration | null;
}) {
  const stem = configuration?.capabilities.stem_separation;
  const title = !stem
    ? "Detecting processing node"
    : !stem.available
      ? "Stem engine unavailable"
      : stem.accelerated
        ? `${stem.backend === "cuda" ? "NVIDIA CUDA" : "Apple MPS"} available`
        : "CPU processing";
  const description = !stem
    ? "Checking capabilities."
    : !stem.available
      ? stem.install_message ||
        "Install separation support to enable stem jobs."
      : stem.accelerated
        ? "Accelerated separation selected automatically."
        : stem.accelerator_issue || "No compatible accelerator detected.";
  return (
    <div className="rounded-md border bg-muted/20 p-3">
      <div className="flex items-center gap-2 text-xs font-medium">
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            stem?.accelerated
              ? "bg-emerald-500"
              : stem?.available
                ? "bg-blue-500"
                : "bg-amber-500",
          )}
        />
        {title}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {description}
      </p>
    </div>
  );
}

export function AppShell({
  children,
  configuration,
  onRefresh,
  onCreate,
  createLabel = "New job",
}: {
  children: ReactNode;
  configuration: Configuration | null;
  onRefresh: () => void;
  onCreate: () => void;
  createLabel?: string;
}) {
  return (
    <HeaderSlotProvider>
      <AppShellLayout
        configuration={configuration}
        onRefresh={onRefresh}
        onCreate={onCreate}
        createLabel={createLabel}
      >
        {children}
      </AppShellLayout>
    </HeaderSlotProvider>
  );
}

function AppShellLayout({
  children,
  configuration,
  onRefresh,
  onCreate,
  createLabel,
}: {
  children: ReactNode;
  configuration: Configuration | null;
  onRefresh: () => void;
  onCreate: () => void;
  createLabel: string;
}) {
  const location = useLocation();
  const { node: headerNode } = useHeaderSlot();
  const nav = [
    { label: "Projects", icon: Layers3, href: "/projects", active: location.pathname.startsWith("/projects") },
    { label: "Jobs", icon: Gauge, href: "/jobs", active: location.pathname.startsWith("/jobs") },
    { label: "Stem cache", icon: Layers3 },
    { label: "Storage", icon: HardDrive },
    { label: "Settings", icon: Settings2 },
  ];
  return (
    <div className="min-h-screen bg-background">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r bg-background lg:block">
        <div className="flex h-16 items-center gap-3 border-b px-5">
          <div className="rounded-md bg-primary p-2 text-primary-foreground">
            <AudioLines className="h-5 w-5" />
          </div>
          <div>
            <p className="font-semibold tracking-tight">Upmixer</p>
            <p className="text-[10px] font-medium uppercase tracking-[.16em] text-muted-foreground">
              Processing console
            </p>
          </div>
        </div>
        <nav className="space-y-1 p-3">
          {nav.map((item) => (
            item.href ? <Link key={item.label} to={item.href}
              aria-current={item.active ? "page" : undefined}
              className={cn(
                "flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm",
                item.active ? "bg-accent font-medium text-accent-foreground" : "text-muted-foreground hover:bg-muted",
              )}
            ><item.icon className="h-4 w-4" /><span>{item.label}</span></Link> : <button
              key={item.label}
              type="button"
              disabled={!item.active}
              aria-disabled={!item.active}
              className={cn(
                "flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm",
                item.active
                  ? "bg-accent font-medium text-accent-foreground"
                  : "cursor-not-allowed text-muted-foreground opacity-60",
              )}
            >
              <item.icon className="h-4 w-4" />
              <span>{item.label}</span>
              {!item.active && (
                <span className="ml-auto text-[10px] uppercase tracking-wide">
                  Soon
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="absolute bottom-4 left-3 right-3">
          <CapabilityStatus configuration={configuration} />
        </div>
      </aside>
      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b bg-background px-4 sm:px-7">
          <div className="flex min-w-0 items-center gap-3">
            <div className="lg:hidden">
              <AudioLines className="h-5 w-5" />
            </div>
            <div className="min-w-0">{headerNode}</div>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              aria-label="Refresh"
              onClick={onRefresh}
            >
              <RefreshCw />
            </Button>
            <ThemeToggle />
            <Button className="ml-2" onClick={onCreate}>
              {createLabel}
            </Button>
          </div>
        </header>
        {children}
      </div>
    </div>
  );
}
