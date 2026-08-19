import * as React from "react";
import { Panel, PanelBody, PanelHeader } from "@/app/Panel";
import { Switch } from "@/components/ui/switch";

/** One mastering effect. The header switch is the effect's power button, the
 * way a plug-in bypasses: it replaces the "None" entry every profile picker
 * used to carry and the standalone enable toggles that used to sit in the
 * body. Placed on the trailing edge, matching Apple's settings rows and the
 * `Switch` position in `ToggleField`. */
export function EffectPanel({
  title,
  enabled,
  onEnabledChange,
  toggleDisabled = false,
  status,
  children,
}: {
  title: string;
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  toggleDisabled?: boolean;
  status?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Panel>
      <PanelHeader
        title={title}
        actions={
          <>
            {status}
            <Switch
              aria-label={title}
              checked={enabled}
              disabled={toggleDisabled}
              onCheckedChange={onEnabledChange}
            />
          </>
        }
      />
      <PanelBody className="space-y-2.5 overflow-visible">{children}</PanelBody>
    </Panel>
  );
}

export const POT_GRID = "grid grid-cols-[repeat(auto-fit,minmax(76px,1fr))] gap-3";

/** Splits a panel body into named runs of controls. Reuses the table-header
 * micro-label rather than introducing a heading style of its own. */
export function FieldGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2.5 border-t pt-2.5 first:border-t-0 first:pt-0">
      <p className="text-[11px] font-semibold uppercase tracking-[.06em] text-muted-foreground">
        {title}
      </p>
      {children}
    </div>
  );
}

export function titleCase(value: string) {
  return value
    .split("-")
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ");
}
