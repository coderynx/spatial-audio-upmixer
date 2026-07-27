import * as React from "react";
import type { ReactNode } from "react";
import {
  AudioLines,
  Database,
  Gauge,
  HardDrive,
  Layers3,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Settings2,
} from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import type { Configuration } from "@/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { HeaderSlotProvider, useHeaderSlot } from "./HeaderSlot";
import { ThemeToggle } from "./ThemeToggle";

const SIDEBAR_COLLAPSED_KEY = "upmixer.sidebar-collapsed";

/** Processing-node state, rendered into the global status bar so machine
 * state is always visible without occupying a sidebar block. */
function CapabilityStatus({ configuration }: { configuration: Configuration | null }) {
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
      ? stem.install_message || "Install separation support to enable stem jobs."
      : stem.accelerated
        ? "Accelerated separation selected automatically."
        : stem.accelerator_issue || "No compatible accelerator detected.";
  return (
    <span className="flex min-w-0 items-center gap-1.5" title={`${title} — ${description}`}>
      <span
        className={cn(
          "h-1.5 w-1.5 shrink-0 rounded-full",
          stem?.accelerated ? "bg-success" : stem?.available ? "bg-primary" : "bg-warning",
        )}
      />
      <span className="truncate">{title}</span>
    </span>
  );
}

export function AppShell({
  children,
  configuration,
  onRefresh,
  onCreate,
  createLabel,
}: {
  children: ReactNode;
  configuration: Configuration | null;
  onRefresh: () => void;
  onCreate?: () => void;
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

const NAV = [
  { label: "Projects", icon: Layers3, href: "/projects" },
  { label: "Jobs", icon: Gauge, href: "/jobs" },
  { label: "Stem cache", icon: Database, href: "/stem-cache" },
  { label: "Storage", icon: HardDrive, href: "/storage" },
  { label: "Settings", icon: Settings2, href: "/settings" },
];

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
  onCreate?: () => void;
  createLabel?: string;
}) {
  const location = useLocation();
  const { node: headerNode } = useHeaderSlot();
  const [collapsed, setCollapsed] = React.useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1",
  );
  React.useEffect(() => {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);
  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      <aside
        className={cn(
          "hidden shrink-0 flex-col border-r bg-card transition-[width] duration-150 lg:flex",
          collapsed ? "w-12" : "w-56",
        )}
      >
        <div className={cn("flex h-[var(--topbar-h)] shrink-0 items-center gap-2.5 border-b", collapsed ? "justify-center px-1" : "px-3")}>
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <AudioLines className="h-4 w-4" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <p className="truncate text-[13px] font-semibold leading-tight tracking-tight">Upmixer</p>
              <p className="truncate text-[10px] font-medium uppercase leading-tight tracking-[.12em] text-muted-foreground">
                Studio
              </p>
            </div>
          )}
        </div>
        <nav className="min-h-0 flex-1 overflow-y-auto p-2">
          {!collapsed && (
            <p className="px-2 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-[.1em] text-muted-foreground">
              Workspace
            </p>
          )}
          {NAV.map((item) => {
            const active = location.pathname.startsWith(item.href);
            return (
              <Link
                key={item.label}
                to={item.href}
                aria-current={active ? "page" : undefined}
                title={collapsed ? item.label : undefined}
                className={cn(
                  "mb-0.5 flex h-8 w-full items-center gap-2.5 rounded-md px-2 text-left text-[13px] transition-colors",
                  collapsed && "justify-center px-0",
                  active
                    ? "bg-primary/15 font-medium text-primary"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <item.icon className="h-4 w-4 shrink-0" />
                {!collapsed && <span className="truncate">{item.label}</span>}
              </Link>
            );
          })}
        </nav>
        {/* Same height as Workspace's StatusBar so the two bottom bars share
            one continuous baseline across the sidebar/content seam. */}
        <div className={cn("flex h-8 shrink-0 items-center border-t px-1.5", collapsed ? "justify-center" : "justify-between")}>
          {!collapsed && <ThemeToggle />}
          <Button
            variant="ghost"
            size="icon"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
          </Button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-[var(--topbar-h)] shrink-0 items-center justify-between gap-3 border-b bg-card px-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="lg:hidden">
              <AudioLines className="h-4 w-4 text-primary" />
            </div>
            <div className="min-w-0">{headerNode}</div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <span className="mr-2 hidden text-[11px] text-muted-foreground sm:flex">
              <CapabilityStatus configuration={configuration} />
            </span>
            <Button variant="ghost" size="icon" aria-label="Refresh" onClick={onRefresh}>
              <RefreshCw />
            </Button>
            <div className="lg:hidden">
              <ThemeToggle />
            </div>
            {onCreate && createLabel && (
              <Button className="ml-1" onClick={onCreate}>
                {createLabel}
              </Button>
            )}
          </div>
        </header>
        <div className="min-h-0 flex-1">{children}</div>
      </div>
    </div>
  );
}
