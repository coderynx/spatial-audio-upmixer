import * as React from "react";
import { AudioWaveform, ChevronDown, ChevronUp, Loader2, SlidersHorizontal } from "lucide-react";
import type { Project, StemRouting } from "@/api";
import { SegmentedControl } from "@/app/SegmentedControl";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { Manifest } from "@/lib/manifest";
import { stemColors } from "@/lib/stems";
import { cn } from "@/lib/utils";
import ChannelMeters from "./ChannelMeters";
import { StripResizeHandle } from "./ChannelStrip";
import ElevationView from "./ElevationView";
import HazeView from "./HazeView";
import { LoudnessMeterPanel } from "./LoudnessMeters";
import { MixerView } from "./MixerView";
import StereoPanoramaView from "./StereoPanoramaView";
import { TimelineView } from "./TimelineView";
import {
  HAZE_MIN_WIDTH,
  LOUDNESS_DEFAULT_WIDTH,
  METERS_GROUP_DEFAULT_SHARE,
  METERS_GROUP_MIN_WIDTH,
  PANE_DEFAULT_HEIGHT,
  PANE_MIN_HEIGHT,
} from "./projectDetailLayout";
import type { useColumnLayout } from "./useColumnLayout";
import type { usePaneLayout } from "./usePaneLayout";
import type { useStemPreview, OutputMode } from "./useStemPreview";
import type { useTrackPeaks } from "./useTrackPeaks";
import type { ProjectViewState } from "./projectViewState";

const PANE_SEGMENTS = [
  { value: "timeline" as const, label: "Timeline", icon: AudioWaveform },
  { value: "mixer" as const, label: "Mixer", icon: SlidersHorizontal },
];

type Preview = ReturnType<typeof useStemPreview>;

export type PreviewPanelProps = {
  containerRef: React.Ref<HTMLElement>;
  preview: Preview;
  pane: ReturnType<typeof usePaneLayout>;
  columns: ReturnType<typeof useColumnLayout>;
  project: Project;
  trackManifest: Manifest | null;
  viewState: ProjectViewState;
  channels: string[];
  routing: StemRouting;
  outputMode: OutputMode;
  stereoLayout: boolean;
  stemChannelCounts: Record<string, number>;
  selectedStem: string | null;
  onSelectStem: (stem: string | null) => void;
  onHazeIntensity: (value: number) => void;
  onElevationIntensity: (value: number) => void;
  orderedStems: string[];
  silentStems: string[];
  previewStemCount: number;
  peaks: ReturnType<typeof useTrackPeaks>["peaks"];
  peaksLoading: boolean;
  peaksDuration: number;
  draggedStem: string | null;
  onDragStart: (stem: string) => void;
  onDragEnd: () => void;
  onDropOn: (target: string) => void;
  onToggleMute: (stem: string) => void;
  onToggleSolo: (stem: string) => void;
  onGain: (stem: string, gain: number) => void;
  onAnchorStrength: (value: number) => void;
  onCommitScrub: (value: number) => void;
};

function PreviewStatus({ preview, project, previewStemCount }: Pick<PreviewPanelProps, "preview" | "project" | "previewStemCount">) {
  const row = "flex shrink-0 items-center gap-2 rounded-md border bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground";
  if (preview.error) return <p className="shrink-0 text-[11px] text-destructive">{preview.error}</p>;
  return <>
    {preview.supported && !preview.ready && previewStemCount > 0 && (
      <div className={row}>
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
        <span className="flex-1">Preparing preview — decoding stems…</span>
        <Progress value={preview.loadProgress * 100} className="w-24" />
        <span className="w-9 shrink-0 text-right tabular-nums">{Math.round(preview.loadProgress * 100)}%</span>
      </div>
    )}
    {preview.ready && preview.measuring && (
      <div className={row}>
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
        <span className="flex-1">Preparing preview — calibrating loudness…</span>
        <Progress value={preview.measureProgress * 100} className="w-24" />
        <span className="w-9 shrink-0 text-right tabular-nums">{Math.round(preview.measureProgress * 100)}%</span>
      </div>
    )}
    {preview.previewLimitedTo !== null && (
      <div className={row}>
        <span>Preview limited to first {Math.floor(preview.previewLimitedTo)} s — export renders full length.</span>
      </div>
    )}
    {project.reference_match_pending && (
      <div className={row}>
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
        <span className="flex-1">Preparing reference EQ match — preview using original EQ until ready…</span>
      </div>
    )}
  </>;
}

export function PreviewPanel(props: PreviewPanelProps) {
  const {
    containerRef, preview, pane, columns, project, trackManifest, viewState,
    channels, routing, outputMode, stereoLayout, stemChannelCounts, selectedStem, onSelectStem,
    onHazeIntensity, onElevationIntensity, orderedStems, silentStems, previewStemCount,
    peaks, peaksLoading, peaksDuration, draggedStem, onDragStart, onDragEnd, onDropOn,
    onToggleMute, onToggleSolo, onGain, onAnchorStrength, onCommitScrub,
  } = props;
  const {
    paneView, paneHeight, changePane, resizePaneTo,
    beginPaneResize, movePaneResize, endPaneResize, paneResizeKeys,
  } = pane;
  const {
    rowRef, rowSize, hazeExtra, setHazeExtra, elevationExtra, setElevationExtra,
    loudnessExtra, setLoudnessExtra, commitColumnExtra, hazeMaxWidth, hazeWidth,
    groupMaxWidth, groupWidth, loudnessFloor, loudnessCeil, loudnessWidth,
  } = columns;
  const showLoudness = preview.supported && previewStemCount > 0;
  const paneStyle = React.useMemo(() => ({ height: paneHeight }), [paneHeight]);

  return <section ref={containerRef} className="flex min-h-0 flex-col">
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-2">
      <PreviewStatus preview={preview} project={project} previewStemCount={previewStemCount} />
      <div ref={rowRef} className={cn("flex min-h-0 gap-2", paneView ? "min-h-[180px] flex-1" : "flex-[3]")}>
        {!stereoLayout && <div className="relative min-h-0 shrink-0" style={{ width: hazeWidth }}>
          <HazeView channels={channels} routing={routing} selectedStem={selectedStem} colors={stemColors} channelCounts={stemChannelCounts} onSelectStem={onSelectStem} stemSpectrum={preview.stemSpectrum} speakerEnabled={preview.speakerEnabled} onToggleSpeaker={preview.toggleSpeaker} active={preview.playing} intensity={viewState.hazeIntensity} onIntensity={onHazeIntensity} className="h-full w-full" />
          <StripResizeHandle
            label="Resize Haze view"
            value={hazeExtra}
            onChange={setHazeExtra}
            onCommit={(px) => { setHazeExtra(px); commitColumnExtra("haze", px); }}
            min={HAZE_MIN_WIDTH - rowSize.height}
            max={hazeMaxWidth - rowSize.height}
          />
        </div>}
        <div className="relative min-h-0 min-w-0 flex-1">
          {stereoLayout
            ? <StereoPanoramaView channels={channels} routing={routing} selectedStem={selectedStem} colors={stemColors} channelCounts={stemChannelCounts} onSelectStem={onSelectStem} stemSpectrum={preview.stemSpectrum} speakerEnabled={preview.speakerEnabled} onToggleSpeaker={preview.toggleSpeaker} active={preview.playing} intensity={viewState.hazeIntensity} onIntensity={onHazeIntensity} className="h-full w-full" />
            : <ElevationView channels={channels} routing={routing} selectedStem={selectedStem} colors={stemColors} channelCounts={stemChannelCounts} stemSpectrum={preview.stemSpectrum} speakerEnabled={preview.speakerEnabled} onToggleSpeaker={preview.toggleSpeaker} active={preview.playing} intensity={viewState.elevationIntensity} onIntensity={onElevationIntensity} className="h-full w-full" />}
          <StripResizeHandle
            label="Resize Elevation view"
            value={elevationExtra}
            onChange={setElevationExtra}
            onCommit={(px) => { setElevationExtra(px); commitColumnExtra("elevation", px); }}
            min={METERS_GROUP_DEFAULT_SHARE - groupMaxWidth}
            max={METERS_GROUP_DEFAULT_SHARE - METERS_GROUP_MIN_WIDTH}
          />
        </div>
        <div className="flex min-h-0 shrink-0 gap-2" style={{ width: groupWidth }}>
          <div className="relative min-h-0 min-w-0 flex-1">
            <ChannelMeters channels={channels} channelLevels={preview.channelLevels} headphoneLevels={preview.headphoneLevels} speakerEnabled={preview.speakerEnabled} onToggleSpeaker={preview.toggleSpeaker} outputMode={outputMode} active={preview.playing} className="h-full w-full" />
            {showLoudness && (
              <StripResizeHandle
                label="Resize Loudness meter"
                value={loudnessExtra}
                onChange={setLoudnessExtra}
                onCommit={(px) => { setLoudnessExtra(px); commitColumnExtra("loudness", px); }}
                min={LOUDNESS_DEFAULT_WIDTH - loudnessCeil}
                max={LOUDNESS_DEFAULT_WIDTH - loudnessFloor}
              />
            )}
          </div>
          {showLoudness && (
            <div className="min-h-0 shrink-0" style={{ width: loudnessWidth }}>
              <LoudnessMeterPanel
                loudness={preview.loudness}
                masterMeters={preview.masterMeters}
                headphoneLevels={preview.headphoneLevels}
                active={preview.playing}
                bypassed={viewState.masteringBypassed || viewState.matchBypassed}
                outputMode={outputMode}
                channelCount={channels.length}
                className="h-full w-full"
              />
            </div>
          )}
        </div>
      </div>
    </div>
    {paneView && (
      <div
        role="separator"
        aria-label="Resize bottom pane"
        aria-orientation="horizontal"
        aria-valuenow={paneHeight}
        aria-valuemin={PANE_MIN_HEIGHT}
        tabIndex={0}
        onPointerDown={beginPaneResize}
        onPointerMove={movePaneResize}
        onPointerUp={endPaneResize}
        onPointerCancel={endPaneResize}
        onKeyDown={paneResizeKeys}
        onDoubleClick={() => resizePaneTo(PANE_DEFAULT_HEIGHT)}
        className="group flex h-2 shrink-0 cursor-row-resize touch-none items-center justify-center border-t bg-muted/40 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60"
      >
        <span className="h-0.5 w-8 rounded-full bg-border transition-colors group-hover:bg-muted-foreground" aria-hidden="true" />
      </div>
    )}
    <div className={cn("flex h-8 shrink-0 items-center gap-2 bg-card px-2", !paneView && "border-t")}>
      <SegmentedControl
        aria-label="Bottom pane"
        size="sm"
        segments={PANE_SEGMENTS}
        value={(paneView ?? "") as "timeline" | "mixer"}
        onChange={changePane}
      />
      <div className="min-w-0 flex-1" />
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6"
        aria-label={paneView ? "Collapse bottom pane" : "Show bottom pane"}
        aria-expanded={Boolean(paneView)}
        onClick={() => changePane(paneView ? null : "timeline")}
      >
        {paneView ? <ChevronDown /> : <ChevronUp />}
      </Button>
    </div>
    {paneView && <div className="h-2 shrink-0 bg-muted/40" aria-hidden="true" />}
    {paneView === "timeline" && (
      <TimelineView
        className="shrink-0 border-t"
        style={paneStyle}
        stems={orderedStems}
        peaks={peaks}
        loading={peaksLoading}
        pending={Boolean(project.peaks_pending)}
        mutedStems={silentStems}
        enabled={trackManifest?.mixing.stem_enabled || {}}
        solo={trackManifest?.mixing.stem_solo || []}
        onToggleMute={onToggleMute}
        onToggleSolo={onToggleSolo}
        gains={trackManifest?.mixing.stem_rebalance || {}}
        onGain={onGain}
        stemLevels={preview.stemLevels}
        stemChannelCounts={stemChannelCounts}
        draggedStem={draggedStem}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDropOn={onDropOn}
        selectedStem={selectedStem}
        onSelectStem={onSelectStem}
        duration={preview.duration || peaksDuration}
        currentTimeRef={preview.currentTimeRef}
        playing={preview.playing}
        disabled={!preview.supported || !previewStemCount}
        onBeginScrub={preview.beginScrub}
        onScrubTo={preview.scrubTo}
        onCommitScrub={onCommitScrub}
      />
    )}
    {paneView === "mixer" && trackManifest && (
      <MixerView
        className="shrink-0 border-t"
        style={paneStyle}
        stems={orderedStems}
        stemChannels={stemChannelCounts}
        selectedStem={selectedStem}
        onSelectStem={onSelectStem}
        gains={trackManifest.mixing.stem_rebalance}
        onGain={onGain}
        enabled={trackManifest.mixing.stem_enabled}
        solo={trackManifest.mixing.stem_solo}
        onToggleMute={onToggleMute}
        onToggleSolo={onToggleSolo}
        stemLevels={preview.stemLevels}
        anchorStrength={trackManifest.mixing.stem_source_anchor_strength}
        onAnchorStrength={onAnchorStrength}
        headphoneLevels={preview.headphoneLevels}
        volume={preview.volume}
        onVolume={preview.setVolume}
        muted={preview.muted}
        onToggleMasterMute={preview.toggleMute}
        active={preview.playing}
        disabled={!previewStemCount}
      />
    )}
  </section>;
}
