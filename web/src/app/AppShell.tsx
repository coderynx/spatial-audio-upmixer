import * as React from "react";
import type { ReactNode } from "react";
import {
  AudioLines,
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

function CapabilityStatus({
  configuration,
  collapsed,
}: {
  configuration: Configuration | null;
  collapsed: boolean;
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
  const dot = (
    <span
      className={cn(
        "h-2 w-2 shrink-0 rounded-full",
        stem?.accelerated
          ? "bg-emerald-500"
          : stem?.available
            ? "bg-blue-500"
            : "bg-amber-500",
      )}
    />
  );
  if (collapsed) {
    return (
      <div
        className="flex items-center justify-center rounded-md border bg-muted/20 p-2"
        title={`${title} — ${description}`}
      >
        {dot}
      </div>
    );
  }
  return (
    <div className="rounded-md border bg-muted/20 p-3">
      <div className="flex items-center gap-2 text-xs font-medium">
        {dot}
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
  const [collapsed, setCollapsed] = React.useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1",
  );
  React.useEffect(() => {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);
  const nav = [
    { label: "Projects", icon: Layers3, href: "/projects", active: location.pathname.startsWith("/projects") },
    { label: "Jobs", icon: Gauge, href: "/jobs", active: location.pathname.startsWith("/jobs") },
    { label: "Stem cache", icon: Layers3 },
    { label: "Storage", icon: HardDrive },
    { label: "Settings", icon: Settings2 },
  ];
  return (
    <div className="min-h-screen bg-background">
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-30 hidden border-r bg-background transition-[width] duration-150 lg:block",
          collapsed ? "w-14" : "w-60",
        )}
      >
        <div className={cn("flex h-14 items-center gap-3 border-b", collapsed ? "justify-center px-2" : "px-5")}>
          <div className="rounded-md bg-primary p-2 text-primary-foreground">
            <AudioLines className="h-5 w-5" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <p className="font-semibold tracking-tight">Upmixer</p>
              <p className="text-[10px] font-medium uppercase tracking-[.16em] text-muted-foreground">
                Processing console
              </p>
            </div>
          )}
        </div>
        <nav className="space-y-1 p-3">
          {nav.map((item) => (
            item.href ? <Link key={item.label} to={item.href}
              aria-current={item.active ? "page" : undefined}
              title={collapsed ? item.label : undefined}
              className={cn(
                "flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm",
                collapsed && "justify-center px-0",
                item.active ? "bg-accent font-medium text-accent-foreground" : "text-muted-foreground hover:bg-muted",
              )}
            ><item.icon className="h-4 w-4 shrink-0" />{!collapsed && <span>{item.label}</span>}</Link> : <button
              key={item.label}
              type="button"
              disabled={!item.active}
              aria-disabled={!item.active}
              title={collapsed ? item.label : undefined}
              className={cn(
                "flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm",
                collapsed && "justify-center px-0",
                item.active
                  ? "bg-accent font-medium text-accent-foreground"
                  : "cursor-not-allowed text-muted-foreground opacity-60",
              )}
            >
              <item.icon className="h-4 w-4 shrink-0" />
              {!collapsed && <span>{item.label}</span>}
              {!collapsed && !item.active && (
                <span className="ml-auto text-[10px] uppercase tracking-wide">
                  Soon
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className={cn("absolute bottom-12", collapsed ? "left-2 right-2" : "left-3 right-3")}>
          <CapabilityStatus configuration={configuration} collapsed={collapsed} />
        </div>
        <div className={cn("absolute bottom-3 flex", collapsed ? "left-2 right-2 justify-center" : "left-3 right-3 justify-end")}>
          <Button
            variant="ghost"
            size="icon"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          </Button>
        </div>
      </aside>
      <div className={cn(collapsed ? "lg:pl-14" : "lg:pl-60")}>
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b bg-background px-4 sm:px-6">
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
