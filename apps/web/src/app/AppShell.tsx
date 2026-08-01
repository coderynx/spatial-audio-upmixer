import * as React from "react";
import type { ReactNode } from "react";
import {
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
import logoMark from "@/assets/logo-mark.svg";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { HeaderSlotProvider, useHeaderSlot } from "./HeaderSlot";
import { SeparationToggle } from "./SeparationToggle";
import { ThemeToggle } from "./ThemeToggle";

const SIDEBAR_COLLAPSED_KEY = "upmixer.sidebar-collapsed";

export function AppShell({
  children,
  onRefresh,
  onCreate,
  createLabel,
}: {
  children: ReactNode;
  onRefresh?: () => void;
  onCreate?: () => void;
  createLabel?: string;
}) {
  return (
    <HeaderSlotProvider>
      <AppShellLayout onRefresh={onRefresh} onCreate={onCreate} createLabel={createLabel}>
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
  onRefresh,
  onCreate,
  createLabel,
}: {
  children: ReactNode;
  onRefresh?: () => void;
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
          <img src={logoMark} alt="" className="h-7 w-7 shrink-0" />

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
          <div className="flex min-w-0 flex-1 items-center gap-2.5">
            <div className="lg:hidden">
              <img src={logoMark} alt="" className="h-5 w-5" />
            </div>
            {/* `flex-1` so a page whose own header content wants to centre
                something (`ProjectDetailPage`'s stage tabs, via the same
                three-column `minmax(0,1fr)_auto_minmax(0,1fr)` grid
                `Transport` uses) gets the bar's true full width to centre
                against, not just its own intrinsic content width. */}
            <div className="min-w-0 flex-1 self-stretch">{headerNode}</div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <SeparationToggle collapsed />
            {onRefresh && (
              <Button variant="ghost" size="icon" aria-label="Refresh" onClick={onRefresh}>
                <RefreshCw />
              </Button>
            )}
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
