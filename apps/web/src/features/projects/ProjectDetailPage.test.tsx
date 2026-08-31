import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, type Asset, type Configuration, type Project } from "@/api";
import { HeaderSlotProvider, useHeaderSlot } from "@/app/HeaderSlot";
import { ProjectDetailPage } from "./ProjectDetailPage";

const asset: Asset = {
  id: "asset-1", position: 0, filename: "track.wav", relative_path: "track.wav",
  size_bytes: 1, title: "Track One", artist: null, album: null, release_date: null,
  track_number: null, duration_seconds: 30, sample_rate: 48000, channels: 2, audio_url: "/track.wav",
};

const project: Project = {
  id: "project-1", import_id: "import-1", name: "Editable master", notes: null, status: "ready", progress: 1,
  status_message: "Project stems ready", progress_log: [], manifest: {}, scene: {}, view_state: {}, requested_stems: ["Vocals"],
  prepared_stems: ["Vocals"], stem_generation: 1, preview_quality: "high", revision: 1, error: null,
  created_at: "2026-01-01T12:00:00Z", updated_at: "2026-01-01T12:01:00Z",
  tracks: [{
    id: "track-1", position: 0, status: "ready", progress: 1, layouts: ["7.1.4"], layout_overrides: {}, scene_overrides: {},
    source_preview_url: null, error: null, asset,
    peaks_url: null, peaks_bins: 0, peaks_stem_keys: [], peaks_duration_seconds: null,
    stems: [{ id: "stem-1", stem_key: "Vocals", sample_rate: 48000, channels: 2, size_bytes: 1, audio_url: "/vocals.wav", preview_url: null }],
  }],
  exports: [],
};

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(async () => project),
      exportProject: vi.fn(async () => ({ id: "job-1" })),
      saveProject: vi.fn(async () => project),
      saveProjectTrackLayout: vi.fn(async () => project),
      retryProject: vi.fn(async () => project),
      reprepareProjectStems: vi.fn(async () => project),
      saveProjectViewState: vi.fn(async () => undefined),
    },
  };
});

// Mirrors AppShell's header rendering — ProjectDetailPage pushes its title
// into the shared header slot instead of rendering its own <h1>, so the
// test needs a consumer for that title to show up in the DOM.
function HeaderOutlet() {
  const { node } = useHeaderSlot();
  return <>{node}</>;
}

function renderPage(configuration: Configuration | null = null) {
  return render(
    <HeaderSlotProvider>
      <MemoryRouter initialEntries={["/projects/project-1"]}>
        <HeaderOutlet />
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectDetailPage configuration={configuration} />} />
        </Routes>
      </MemoryRouter>
    </HeaderSlotProvider>,
  );
}

function channelCount() {
  return screen.getByText("Channels").parentElement?.textContent?.replace("Channels", "").trim();
}

// Saves are debounced, so calls recorded by one test would otherwise still be
// the newest entry when the next test inspects `saveProject.mock.calls`.
beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => {
  try { window.localStorage.clear(); } catch { /* storage unavailable */ }
});

describe("ProjectDetailPage tabs", () => {
  it("defaults to the Mixing tab with preview and routing graph visible", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: /Mixing/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Routing preset")).toBeInTheDocument();
    // The preset carries the whole placement; there is no intensity blend.
    expect(screen.queryByText("Intensity")).not.toBeInTheDocument();
    // Preview transport and speaker graph render regardless of tab.
    expect(screen.getByRole("button", { name: /^(Play|Pause)$/i })).toBeInTheDocument();
  });

  it("switches to the Mastering tab", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /Mastering/ }));

    expect(screen.getByText("Spectral EQ")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Loudness" })).toBeInTheDocument();
    expect(screen.getByText("Reference EQ match")).toBeInTheDocument();
    // Preview transport and speaker graph stay mounted on the Mastering tab.
    expect(screen.getByRole("button", { name: /^(Play|Pause)$/i })).toBeInTheDocument();
  });

  it("switches to the Delivery tab and exports from there", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /Delivery/ }));

    expect(screen.getByText("Format")).toBeInTheDocument();
    expect(screen.getByText("Normalize output")).toBeInTheDocument();
    const exportButton = screen.getByRole("button", { name: /Export project/ });
    fireEvent.click(exportButton);
    // One export renders one layout: the selected one.
    await waitFor(() => expect(api.exportProject).toHaveBeenCalledWith("project-1", "7.1.4"));
  });

  it("configures stem selection and cleanup before re-preparing", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Prepare" }));
    await user.click(screen.getByRole("button", { name: "Re-prepare stems" }));
    expect(screen.getByRole("dialog", { name: "Re-prepare stems" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Bass" }));
    await user.click(screen.getByRole("button", { name: "Re-prepare stems" }));

    await waitFor(() => expect(api.reprepareProjectStems).toHaveBeenCalledWith("project-1", {
      stems: ["Vocals", "Bass"], stem_bleed_reduction: false,
    }));
  });

  it("puts transport position on the timeline instead of a second seek bar in the transport", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    expect(screen.queryByRole("slider", { name: "Preview position" })).not.toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Timeline position" })).toBeInTheDocument();
  });

  it("switches the merged spatial card between haze, elevation, and scene", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: "Haze" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("slider", { name: "Resize Haze view" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Elevation" }));

    expect(screen.getByRole("button", { name: "Elevation" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("slider", { name: "Resize spatial view" })).toBeInTheDocument();
    await waitFor(() => expect(api.saveProjectViewState).toHaveBeenLastCalledWith("project-1", expect.objectContaining({ spatial_view: "elevation" })));

    await user.click(screen.getByRole("button", { name: "Scene" }));

    expect(screen.getByRole("button", { name: "Scene" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText(/3D object scene/)).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Scene intensity" })).toBeInTheDocument();
    await waitFor(() => expect(api.saveProjectViewState).toHaveBeenLastCalledWith("project-1", expect.objectContaining({ spatial_view: "scene" })));
  });

  it("switches the bottom pane to the mixer and back, and collapses it", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    expect(screen.getByRole("group", { name: "Mixer" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Monitor level" })).toBeInTheDocument();
    expect(screen.queryByRole("slider", { name: "Timeline position" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Collapse bottom pane" }));
    expect(screen.queryByRole("group", { name: "Mixer" })).not.toBeInTheDocument();
  });

  it("writes a mixer fader move to the same stem_rebalance field the inspector uses", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    const fader = screen.getByRole("slider", { name: "Vocals gain" });
    fireEvent.keyDown(fader, { key: "ArrowDown" });

    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalled());
    const [, , , payload] = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)!;
    const saved = payload.manifest_overrides as unknown as { mixing: { stem_rebalance: Record<string, number> } };
    expect(saved.mixing.stem_rebalance.Vocals).toBeCloseTo(-0.1);
  });

  it("keeps the selected stem's fader in the inspector, distinct from the mixer's copy of the same control", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    await user.click(screen.getByRole("button", { name: "Vocals" }));

    // One control per accessible name — the mixer strip and the inspector's
    // always-accessible copy are the same field but must not collide as the
    // same name (design spec §8).
    expect(screen.getAllByRole("slider", { name: "Vocals gain" })).toHaveLength(1);
    const inspectorFader = screen.getByRole("slider", { name: "Selected stem gain" });
    expect(inspectorFader).toBeInTheDocument();

    fireEvent.keyDown(inspectorFader, { key: "ArrowUp" });
    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalled());
    const [, , , payload] = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)!;
    const saved = payload.manifest_overrides as unknown as { mixing: { stem_rebalance: Record<string, number> } };
    expect(saved.mixing.stem_rebalance.Vocals).toBeCloseTo(0.1);
  });

  it("stays out of the inspector before a stem is selected", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    expect(screen.getByText("Select a stem to edit its sends.")).toBeInTheDocument();
    expect(screen.queryByRole("slider", { name: "Selected stem gain" })).not.toBeInTheDocument();
  });

  it("moved the source anchor control into the mixer, not the inspector", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    expect(screen.queryByRole("slider", { name: "Source anchor" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    const fader = screen.getByRole("slider", { name: "Source anchor blend" });
    expect(fader).toBeInTheDocument();

    // Not blue — the selected-stem highlight in the same rack is already
    // `bg-primary`, and the anchor strip must not read as another selection.
    const strip = fader.closest("[class*='border-success']");
    expect(strip).not.toBeNull();
    expect(strip?.className).not.toMatch(/border-primary|bg-primary/);
  });

  it("resizes one mixer strip from its own handle without touching its neighbours", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    const vocalsHandle = screen.getByRole("slider", { name: "Resize Vocals strip" });
    // The M button and the fader's meter have no inline width of their own,
    // so `closest` walks past them straight to the strip's root — unlike the
    // fader itself, which does carry an inline width and would match itself.
    const muteButton = screen.getByRole("button", { name: "Mute Vocals" });
    const anchorFader = screen.getByRole("slider", { name: "Source anchor blend" });
    const widthOf = (node: Element) => Number.parseFloat((node.closest("[style*='width']") as HTMLElement).style.width);

    const vocalsBefore = widthOf(muteButton);
    const anchorBefore = widthOf(anchorFader);

    fireEvent.keyDown(vocalsHandle, { key: "ArrowRight" });

    expect(widthOf(muteButton)).toBeGreaterThan(vocalsBefore);
    // Untouched — each strip's handle only resizes that one strip.
    expect(widthOf(anchorFader)).toBe(anchorBefore);
  });

  it("gives the anchor and master strips their own resize handles too", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));

    expect(screen.getByRole("slider", { name: "Resize Anchor strip" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Resize Master strip" })).toBeInTheDocument();
  });

  it("stacks muted effects over an object panner in the mixer and inspector", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    await user.click(screen.getByRole("button", { name: "Vocals" }));

    const inspectorFader = screen.getByRole("slider", { name: "Selected stem gain" });
    const mixerPanner = screen.getByRole("button", { name: "Object panner Vocals" });
    const inspectorPanner = screen.getAllByRole("button", { name: /^Object panner$/ }).at(-1)!;
    const [mixerEq, inspectorEq] = screen.getAllByRole("button", { name: "Open stem EQ" });

    expect(inspectorPanner).toBeInTheDocument();
    expect(mixerEq).toHaveClass("bg-muted");
    expect(inspectorEq).toHaveClass("bg-muted");
    expect(screen.getAllByRole("button", { name: "Enable EQ" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Enable EQ" })[0]).not.toBeDisabled();
    expect(mixerEq.compareDocumentPosition(mixerPanner) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(inspectorEq.compareDocumentPosition(inspectorPanner) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(inspectorPanner.compareDocumentPosition(inspectorFader) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const inspectorNameplate = screen.getAllByTitle("Vocals — stereo").at(-1)!;
    expect(inspectorFader.compareDocumentPosition(inspectorNameplate) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("opens a bed panner above the bed fader", async () => {
    const bedProject: Project = {
      ...project,
      requested_stems: ["Bass"],
      prepared_stems: ["Bass"],
      tracks: [{
        ...project.tracks[0],
        stems: [{ id: "stem-bass", stem_key: "Bass", sample_rate: 48000, channels: 2, size_bytes: 1, audio_url: "/bass.wav", preview_url: null }],
      }],
    };
    vi.mocked(api.getProject).mockResolvedValue(bedProject);
    const config = {
      choices: { layout_channels: { "7.1.4": ["FL", "FR", "C", "LFE", "SL", "SR", "TFL", "TFR"] } },
    } as unknown as Configuration;
    const user = userEvent.setup();
    renderPage(config);
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    const panner = screen.getByRole("button", { name: "Bed panner Bass" });
    const fader = screen.getByRole("slider", { name: "Bass gain" });
    expect(panner.compareDocumentPosition(fader) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.click(panner);
    expect(screen.getByRole("dialog", { name: "Bed panner Bass" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Diversity" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "LFE level" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Ambience to rear" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Ambience to height" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Height crossover" })).toBeInTheDocument();
    expect(screen.queryByRole("slider", { name: "LFE send" })).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("slider", { name: "Ambience to height" }), { key: "ArrowUp" });
    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalled());
    const [, , , payload] = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)!;
    const saved = payload.manifest_overrides as unknown as {
      mixing: { stem_ambient_height: Record<string, number> };
    };
    expect(saved.mixing.stem_ambient_height.Bass).toBeCloseTo(0.01);
    vi.mocked(api.getProject).mockResolvedValue(project);
  });

  it("writes mastering edits to the selected track's selected layout", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /Mastering/ }));
    fireEvent.click(screen.getByRole("switch", { name: "Loudness" }));

    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalled());
    const [, , layout, payload] = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)!;
    expect(layout).toBe("7.1.4");
    const savedOverrides = payload.manifest_overrides as unknown as { mastering: { loudness: { normalize: boolean } } };
    expect(savedOverrides.mastering.loudness.normalize).toBe(false);
  });

  it("shows an LFE send slider for a layout with an LFE channel and writes it to stem_routing", async () => {
    const config = {
      choices: {
        layout_channels: {
          "7.1.4": ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"],
        },
      },
    } as unknown as Configuration;
    const user = userEvent.setup();
    renderPage(config);
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    await user.click(screen.getByRole("button", { name: "Vocals" }));
    await user.click(screen.getAllByRole("button", { name: /^Object panner$/ }).at(-1)!);
    const lfeSlider = screen.getByRole("slider", { name: "LFE send" });
    fireEvent.keyDown(lfeSlider, { key: "ArrowUp" });

    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalled());
    const [, , , payload] = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)!;
    const saved = payload.manifest_overrides as unknown as { mixing: { stem_routing: Record<string, Record<string, number>> } };
    expect(saved.mixing.stem_routing.Vocals.LFE).toBeCloseTo(0.01);
  });

  it("writes an ambience send to the mixing block the export reads", async () => {
    const config = {
      choices: {
        layout_channels: {
          "7.1.4": ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"],
        },
      },
    } as unknown as Configuration;
    const user = userEvent.setup();
    renderPage(config);
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    await user.click(screen.getByRole("button", { name: "Vocals" }));
    await user.click(screen.getAllByRole("button", { name: /^Object panner$/ }).at(-1)!);
    fireEvent.keyDown(screen.getByRole("slider", { name: "Ambience to height" }), { key: "ArrowUp" });

    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalled());
    const [, , , payload] = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)!;
    const saved = payload.manifest_overrides as unknown as {
      mixing: { stem_ambient_height: Record<string, number> };
    };
    expect(saved.mixing.stem_ambient_height.Vocals).toBeCloseTo(0.01);
  });

  it("writes the downmix lock to the mixing block", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    await user.click(screen.getByRole("switch", { name: "Downmix lock" }));

    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalled());
    const [, , , payload] = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)!;
    const saved = payload.manifest_overrides as unknown as { mixing: { spatial_downmix_lock: boolean } };
    expect(saved.mixing.spatial_downmix_lock).toBe(true);
  });

  it("hides the LFE send slider for a layout without an LFE channel", async () => {
    const config = {
      choices: { layout_channels: { "7.1.4": ["FL", "FR"] } },
    } as unknown as Configuration;
    const user = userEvent.setup();
    renderPage(config);
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    await user.click(screen.getByRole("button", { name: "Vocals" }));

    expect(screen.queryByRole("slider", { name: "LFE send" })).not.toBeInTheDocument();
  });

  it("sources the channel list solely from configuration — none before it loads, the served set after", async () => {
    // Backend order for 7.1.4 is back-before-side (upmixer/formats.py::FORMAT_MAP),
    // deliberately unlike the old hardcoded fallback's side-before-back literal.
    const config = {
      choices: {
        layout_channels: {
          "7.1.4": ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"],
        },
      },
    } as unknown as Configuration;

    const withoutConfig = renderPage(null);
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());
    expect(channelCount()).toBe("0");
    withoutConfig.unmount();

    renderPage(config);
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());
    expect(channelCount()).toBe("12");
  });
});

describe("ProjectDetailPage output mode on layout switch", () => {
  it("falls back to binaural at the flat profile when switching to a layout the output device can't carry natively", async () => {
    // No window.AudioContext is stubbed in this suite, so the preview
    // engine is unsupported and maxChannels stays at its 2-channel default
    // (see useStemPreview's `maxChannels` state) — exactly the reported bug:
    // a 2-channel output device, a project switched from stereo to a
    // multichannel layout, and "native" left selected from the stereo state.
    const config = {
      choices: { layout_channels: { "5.1": ["FL", "FR", "C", "LFE", "SL", "SR"] } },
    } as unknown as Configuration;
    // The track carries both layouts; selection starts on the first, and the
    // tracks panel is where a layout switch now happens.
    vi.mocked(api.getProject).mockResolvedValue({
      ...project,
      manifest: { mixing: { channel_layout: "stereo" } },
      tracks: [{ ...project.tracks[0], layouts: ["stereo", "5.1"] }],
    });
    const user = userEvent.setup();
    renderPage(config);
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "5.1" }));

    await waitFor(() => {
      const [, payload] = vi.mocked(api.saveProjectViewState).mock.calls.at(-1)!;
      expect(payload).toMatchObject({ output_mode: "binaural", spatial_profile: "flat" });
    });
  });

});

describe("ProjectDetailPage keyboard shortcuts", () => {
  it("switches the bottom pane to the mixer from the keyboard", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    fireEvent.keyDown(document.body, { key: "x" });
    expect(screen.getByRole("group", { name: "Mixer" })).toBeInTheDocument();
  });

  it("opens the shortcut reference with ? and closes it with Escape", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    fireEvent.keyDown(document.body, { key: "?" });
    expect(screen.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
  });

  it("mutes the selected stem from the keyboard", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    await user.click(screen.getByRole("button", { name: "Vocals" }));

    fireEvent.keyDown(document.body, { key: "m" });

    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalled());
    const [, , , payload] = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)!;
    const saved = payload.manifest_overrides as unknown as { mixing: { stem_enabled: Record<string, boolean> } };
    expect(saved.mixing.stem_enabled.Vocals).toBe(false);
  });

  it("undoes and redoes the last mix edit with Ctrl+Z / Ctrl+Shift+Z", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    await user.click(screen.getByRole("button", { name: "Vocals" }));

    fireEvent.keyDown(document.body, { key: "m" });
    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalledTimes(1));

    // jsdom reports an empty navigator.platform, so IS_MAC is false under
    // vitest even when the suite runs on a real Mac — undo/redo must be
    // reachable via ctrlKey here, not metaKey.
    fireEvent.keyDown(document.body, { key: "z", ctrlKey: true });
    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalledTimes(2));
    const undonePayload = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)![3];
    const undone = undonePayload.manifest_overrides as unknown as { mixing: { stem_enabled: Record<string, boolean> } };
    expect(undone.mixing.stem_enabled.Vocals).not.toBe(false);

    fireEvent.keyDown(document.body, { key: "z", ctrlKey: true, shiftKey: true });
    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalledTimes(3));
    const redonePayload = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)![3];
    const redone = redonePayload.manifest_overrides as unknown as { mixing: { stem_enabled: Record<string, boolean> } };
    expect(redone.mixing.stem_enabled.Vocals).toBe(false);
  });

  it("toggles the master bypass button by click and by the B key", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    const button = screen.getByRole("button", { name: "Bypass master chain" });
    expect(button).toHaveAttribute("aria-pressed", "false");

    await user.click(button);
    expect(button).toHaveAttribute("aria-pressed", "true");

    fireEvent.keyDown(document.body, { key: "b" });
    expect(button).toHaveAttribute("aria-pressed", "false");
  });

  it("leaves the rename field alone — typing shortcut letters into it mutates nothing", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Settings" }));
    const nameField = screen.getByLabelText(/project name/i);
    await user.clear(nameField);
    await user.type(nameField, "mssx?");

    expect(screen.queryByRole("group", { name: "Mixer" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
    expect(api.saveProject).not.toHaveBeenCalled();
  });
});

describe("ProjectDetailPage track/layout tree", () => {
  const multiLayout = {
    ...project,
    tracks: [{
      ...project.tracks[0],
      layouts: ["7.1.4", "stereo"],
      layout_overrides: {
        "7.1.4": { mastering: { compressor: { ratio: 2 } } },
        stereo: { mastering: { compressor: { ratio: 8 } } },
      },
    }],
  };

  it("lists a layout row per layout the track carries", async () => {
    vi.mocked(api.getProject).mockResolvedValue(multiLayout);
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: "7.1.4" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "stereo" })).toBeInTheDocument();
    // The first layout is selected until the user picks another.
    expect(screen.getByRole("button", { name: "7.1.4" })).toHaveAttribute("aria-current", "true");
  });

  it("saves an edit against the layout selected in the tree", async () => {
    vi.mocked(api.getProject).mockResolvedValue(multiLayout);
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "stereo" }));
    await user.click(screen.getByRole("button", { name: /Mastering/ }));
    fireEvent.click(screen.getByRole("switch", { name: "Loudness" }));

    await waitFor(() => expect(api.saveProjectTrackLayout).toHaveBeenCalled());
    const [, , layout] = vi.mocked(api.saveProjectTrackLayout).mock.calls.at(-1)!;
    expect(layout).toBe("stereo");
  });

  it("shows the selected layout's own mix, not another layout's", async () => {
    vi.mocked(api.getProject).mockResolvedValue(multiLayout);
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /Mastering/ }));
    const ratioOn714 = screen.getByRole("slider", { name: /ratio/i }).getAttribute("aria-valuenow");

    await user.click(screen.getByRole("button", { name: "stereo" }));
    const ratioOnStereo = screen.getByRole("slider", { name: /ratio/i }).getAttribute("aria-valuenow");

    expect(ratioOn714).toBe("2");
    expect(ratioOnStereo).toBe("8");
  });
});
