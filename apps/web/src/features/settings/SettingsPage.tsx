import * as React from "react";
import { Cpu, ListTree, Palette } from "lucide-react";
import type { Configuration } from "@/api";
import { EmptyState } from "@/app/EmptyState";
import { useHeaderTitle } from "@/app/HeaderSlot";
import { InspectorGroup, InspectorRow } from "@/app/InspectorRow";
import { Panel, PanelBody, PanelHeader } from "@/app/Panel";
import { SegmentedControl } from "@/app/SegmentedControl";
import { StatusBar, StatusCell, StatusSeparator, StatusSpacer } from "@/app/StatusBar";
import { Toolbar, ToolbarSpacer } from "@/app/Toolbar";
import { Workspace, WorkspaceScroll } from "@/app/Workspace";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useTheme } from "@/theme";
import { cn } from "@/lib/utils";

type Section = "appearance" | "node" | "defaults";

const SECTIONS = [
  { value: "appearance" as const, label: "Appearance", icon: Palette },
  { value: "node" as const, label: "Processing node", icon: Cpu },
  { value: "defaults" as const, label: "Defaults", icon: ListTree },
];

const THEMES = [
  { value: "system" as const, label: "System" },
  { value: "light" as const, label: "Light" },
  { value: "dark" as const, label: "Dark" },
];

// Rendered straight from the CSS custom properties, so these swatches always
// show what the running theme actually resolves to.
const ACCENTS = [
  { name: "Accent", token: "--primary", hint: "systemBlue" },
  { name: "Success", token: "--success", hint: "systemGreen" },
  { name: "Warning", token: "--warning", hint: "systemOrange" },
  { name: "Destructive", token: "--destructive", hint: "systemRed" },
];

const SURFACES = [
  { name: "Background", token: "--background", hint: "canvas" },
  { name: "Card", token: "--card", hint: "chrome" },
  { name: "Muted", token: "--muted", hint: "panel" },
  { name: "Secondary", token: "--secondary", hint: "control" },
];

export function SettingsPage({ configuration }: { configuration: Configuration | null }) {
  const [section, setSection] = React.useState<Section>("appearance");
  const [sample, setSample] = React.useState(0.62);
  const [sampleOn, setSampleOn] = React.useState(true);
  const { theme, setTheme } = useTheme();
  useHeaderTitle(React.useMemo(() => <span className="text-[13px] font-semibold">Settings</span>, []));
  const stem = configuration?.capabilities.stem_separation;
  const choices = configuration?.choices;

  return (
    <Workspace
      toolbar={
        <Toolbar>
          <span className="text-[13px] font-medium">{SECTIONS.find((item) => item.value === section)?.label}</span>
          <ToolbarSpacer />
          {stem && (
            <Badge variant={stem.accelerated ? "success" : stem.available ? "secondary" : "warning"}>
              {stem.accelerated ? "Accelerated" : stem.available ? "CPU" : "Unavailable"}
            </Badge>
          )}
        </Toolbar>
      }
      rail={
        <WorkspaceScroll className="p-2">
          <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-[.1em] text-muted-foreground">Sections</p>
          {SECTIONS.map((item) => (
            <button
              key={item.value}
              type="button"
              aria-current={section === item.value ? "page" : undefined}
              onClick={() => setSection(item.value)}
              className={cn(
                "mb-0.5 flex h-8 w-full items-center gap-2.5 rounded-md px-2 text-left text-[13px] transition-colors",
                section === item.value
                  ? "bg-primary/15 font-medium text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              <item.icon className="h-4 w-4 shrink-0" />
              <span className="truncate">{item.label}</span>
            </button>
          ))}
        </WorkspaceScroll>
      }
      inspector={
        <WorkspaceScroll>
          <InspectorGroup title="About">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {section === "appearance"
                ? "Appearance is stored in this browser only and never travels with a project or manifest."
                : section === "node"
                  ? "The processing node is detected on the server. Accelerator selection is automatic — jobs run on CPU when no compatible device is found."
                  : "Defaults are reported by the server and describe every value a manifest may take. Per-project overrides live in Project settings."}
            </p>
          </InspectorGroup>
          {section === "node" && stem && (
            <InspectorGroup title="Detection">
              <InspectorRow label="Available" value={stem.available ? "Yes" : "No"} />
              <InspectorRow label="Backend" value={stem.backend || "—"} />
              <InspectorRow label="Accelerated" value={stem.accelerated ? "Yes" : "No"} />
              <InspectorRow label="Device found" value={stem.accelerator_detected ? "Yes" : "No"} />
              <InspectorRow label="Platform" value={stem.platform} />
            </InspectorGroup>
          )}
          {section === "defaults" && choices && (
            <InspectorGroup title="Counts">
              <InspectorRow label="Channel layouts" value={choices.channel_layouts.length} />
              <InspectorRow label="Output types" value={choices.output_types.length} />
              <InspectorRow label="Sample rates" value={choices.sample_rates.length} />
              <InspectorRow label="Stems" value={choices.stems.length} />
              <InspectorRow label="Manifest keys" value={Object.keys(configuration.manifest_keys).length} />
            </InspectorGroup>
          )}
        </WorkspaceScroll>
      }
      status={
        <StatusBar>
          <StatusCell label="Theme" value={theme} />
          <StatusSeparator />
          <StatusCell label="Node" value={stem ? (stem.backend || "cpu") : "detecting"} />
          <StatusSpacer />
          {!configuration && <span>Loading configuration…</span>}
        </StatusBar>
      }
    >
      {!configuration ? (
        <EmptyState icon={Cpu} title="Loading configuration…" />
      ) : section === "appearance" ? (
        <WorkspaceScroll className="grid auto-rows-min grid-cols-1 gap-2 p-3 2xl:grid-cols-2">
          <Panel className="2xl:col-span-2">
            <PanelHeader title="Theme" />
            <PanelBody>
              <div className="flex items-center justify-between gap-3">
                <p className="text-[11px] leading-relaxed text-muted-foreground">
                  System follows the operating system appearance. Both palettes use Apple system colours.
                </p>
                <SegmentedControl aria-label="Theme" segments={THEMES} value={theme} onChange={setTheme} />
              </div>
            </PanelBody>
          </Panel>
          <Panel>
            <PanelHeader title="Accent & semantic colours" />
            <PanelBody className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
              {ACCENTS.map((token) => (
                <Swatch key={token.name} {...token} />
              ))}
            </PanelBody>
          </Panel>
          <Panel>
            <PanelHeader title="Surfaces" />
            <PanelBody className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
              {SURFACES.map((token) => (
                <Swatch key={token.name} {...token} />
              ))}
            </PanelBody>
          </Panel>
          <Panel className="2xl:col-span-2">
            <PanelHeader title="Controls" />
            <PanelBody className="space-y-3">
              <div className="flex flex-wrap items-center gap-1.5">
                <Button>Primary</Button>
                <Button variant="secondary">Secondary</Button>
                <Button variant="outline">Outline</Button>
                <Button variant="ghost">Ghost</Button>
                <Button variant="success">Success</Button>
                <Button variant="warning">Warning</Button>
                <Button variant="destructive">Destructive</Button>
                <Button disabled>Disabled</Button>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge>Default</Badge>
                <Badge variant="secondary">Secondary</Badge>
                <Badge variant="success">Success</Badge>
                <Badge variant="warning">Warning</Badge>
                <Badge variant="destructive">Destructive</Badge>
                <Badge variant="outline">Outline</Badge>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <label className="block text-[11px] text-muted-foreground">
                  Slider
                  <Slider className="mt-2" min={0} max={1} step={0.01} value={[sample]} onValueChange={([next]) => setSample(next)} />
                </label>
                <div className="text-[11px] text-muted-foreground">
                  Progress
                  <Progress className="mt-2.5" value={sample * 100} />
                </div>
                <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                  Switch
                  <Switch aria-label="Sample switch" checked={sampleOn} onCheckedChange={setSampleOn} />
                </div>
              </div>
            </PanelBody>
          </Panel>
        </WorkspaceScroll>
      ) : section === "node" ? (
        <WorkspaceScroll className="space-y-2 p-3">
          <Panel>
            <PanelHeader title="Stem separation" />
            <PanelBody className="space-y-2">
              <p className="text-[13px]">
                {!stem
                  ? "Detecting processing node."
                  : !stem.available
                    ? "Stem separation is not installed on this node."
                    : stem.accelerated
                      ? `Accelerated separation via ${stem.backend === "cuda" ? "NVIDIA CUDA" : "Apple MPS"}.`
                      : "Separation runs on CPU."}
              </p>
              {stem?.install_message && (
                <p className="rounded-md border border-warning/30 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
                  {stem.install_message}
                </p>
              )}
              {stem?.accelerator_issue && (
                <p className="rounded-md border bg-muted/40 p-2 text-[11px] leading-relaxed text-muted-foreground">
                  {stem.accelerator_issue}
                </p>
              )}
            </PanelBody>
          </Panel>
        </WorkspaceScroll>
      ) : (
        <WorkspaceScroll className="grid auto-rows-min grid-cols-1 gap-2 p-3 2xl:grid-cols-2">
          <ChoicePanel title="Channel layouts" values={choices?.channel_layouts} />
          <ChoicePanel title="Output types" values={choices?.output_types} />
          <ChoicePanel title="Bit depths" values={choices?.output_subtypes} />
          <ChoicePanel title="Sample rates" values={choices?.sample_rates.map((value) => `${value / 1000} kHz`)} />
          <ChoicePanel title="Stems" values={choices?.stems} />
          <ChoicePanel title="Routing presets" values={choices?.stem_routing_presets} />
          <ChoicePanel title="Stem EQ profiles" values={choices?.stem_eq_profiles} />
          <ChoicePanel title="Mastering EQ profiles" values={choices?.eq_profiles} />
          <ChoicePanel title="Compressor profiles" values={choices?.compressor_profiles} />
          <ChoicePanel title="Bass profiles" values={choices?.bass_profiles} />
          <ChoicePanel title="Binaural profiles" values={choices?.binaural_profiles} />
          <Panel className="2xl:col-span-2">
            <PanelHeader title={`Manifest keys · ${Object.keys(configuration.manifest_keys).length}`} />
            <PanelBody className="p-0">
              <table className="w-full text-left text-[13px]">
                <tbody>
                  {Object.entries(configuration.manifest_keys).map(([key, description]) => (
                    <tr key={key} className="border-b last:border-0">
                      <td className="whitespace-nowrap px-3 py-1 align-top font-mono text-[11px]">{key}</td>
                      <td className="px-3 py-1 text-[11px] text-muted-foreground">{description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </PanelBody>
          </Panel>
        </WorkspaceScroll>
      )}
    </Workspace>
  );
}

function Swatch({ name, token, hint }: { name: string; token: string; hint: string }) {
  return (
    <div className="min-w-0">
      <div className="h-9 w-full rounded-md border" style={{ backgroundColor: `hsl(var(${token}))` }} />
      <p className="mt-1 truncate text-[11px] font-medium">{name}</p>
      <p className="truncate text-[10px] text-muted-foreground">{hint}</p>
    </div>
  );
}

function ChoicePanel({ title, values }: { title: string; values?: string[] }) {
  if (!values?.length) return null;
  return (
    <Panel>
      <PanelHeader title={`${title} · ${values.length}`} />
      <PanelBody>
        <div className="flex flex-wrap gap-1">
          {values.map((value) => (
            <span key={value} className="rounded-md border bg-muted/40 px-1.5 py-0.5 text-[11px]">
              {value}
            </span>
          ))}
        </div>
      </PanelBody>
    </Panel>
  );
}
