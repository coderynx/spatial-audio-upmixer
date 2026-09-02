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
import { getCurrentWindow } from "@tauri-apps/api/window";
import logoMark from "@/assets/logo-mark.svg";
import { Button } from "@/components/ui/button";
import { isTauriRuntime } from "@/runtime";
import { cn } from "@/lib/utils";
import { HeaderSlotProvider, useHeaderSlot } from "./HeaderSlot";
import { ThemeToggle } from "./ThemeToggle";

const SIDEBAR_COLLAPSED_KEY = "upmixer.sidebar-collapsed";

function dragWindow(event: React.MouseEvent<HTMLElement>) {
  if (
    event.button === 0
    && event.target instanceof Element
    && !event.target.closest("a, button, input, select, textarea, [role=button]")
  ) {
    void (event.detail === 2 ? getCurrentWindow().toggleMaximize() : getCurrentWindow().startDragging());
  }
}

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
    <div className="flex h-screen w-full flex-col overflow-hidden bg-background">
      <header
        onMouseDown={isTauriRuntime ? dragWindow : undefined}
        className={cn(
          "flex h-[var(--topbar-h)] shrink-0 items-center border-b px-3 py-2",
          isTauriRuntime ? "bg-accent/50" : "bg-card",
        )}
      >
        <div className={cn(
          "flex h-full shrink-0 items-center gap-2.5",
          isTauriRuntime && collapsed ? "w-20 justify-center px-1" : collapsed ? "w-12 justify-center px-1" : "w-56 px-3",
          isTauriRuntime && !collapsed && "pl-[88px]",
        )}>
          {!isTauriRuntime && <img src={logoMark} alt="" className="h-7 w-7 shrink-0" />}
          {!collapsed && (
            <div className="min-w-0">
              <p className="truncate text-[13px] font-semibold leading-tight tracking-tight">Upmixer</p>
              <p className="truncate text-[10px] font-medium uppercase leading-tight tracking-[.12em] text-muted-foreground">Studio</p>
            </div>
          )}
        </div>
        <div className="flex min-w-0 flex-1 items-center justify-between gap-3">
          <div className="min-w-0 flex-1 self-stretch">{headerNode}</div>
          <div className="flex shrink-0 items-center gap-1">
            {onRefresh && (
              <Button
                variant="ghost"
                size="icon"
                aria-label="Refresh"
                onClick={onRefresh}
              >
                <RefreshCw />
              </Button>
            )}
            <div className="lg:hidden">
              <ThemeToggle className={isTauriRuntime ? "bg-secondary hover:bg-accent" : undefined} />
            </div>
            {onCreate && createLabel && <Button className="ml-1" onClick={onCreate}>{createLabel}</Button>}
          </div>
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
      <aside
        className={cn(
          "z-10 hidden shrink-0 flex-col bg-card transition-[width] duration-150 lg:flex",
          !isTauriRuntime && "border-r",
          collapsed ? "w-12" : "w-56",
        )}
      >
        <nav className={cn("min-h-0 flex-1 overflow-y-auto p-2", isTauriRuntime && "border-r")}>
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
        <div
          className={cn(
            "flex h-8 shrink-0 items-center border-t px-1.5",
            isTauriRuntime && "border-r",
            collapsed ? "justify-center" : "justify-between",
          )}
        >
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
        <div className="min-h-0 flex-1">{children}</div>
      </div>
      </div>
    </div>
  );
}
