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
    id: "track-1", position: 0, status: "ready", progress: 1, manifest_overrides: {}, scene_overrides: {},
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
      saveProjectTrack: vi.fn(async () => project),
      retryProject: vi.fn(async () => project),
      resolveStemRouting: vi.fn(async () => ({})),
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
    await waitFor(() => expect(api.exportProject).toHaveBeenCalledWith("project-1"));
  });

  it("puts transport position on the timeline instead of a second seek bar in the transport", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    expect(screen.queryByRole("slider", { name: "Preview position" })).not.toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Timeline position" })).toBeInTheDocument();
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

    await waitFor(() => expect(api.saveProject).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.saveProject).mock.calls.at(-1)!;
    const saved = payload.manifest as unknown as { mixing: { stem_rebalance: Record<string, number> } };
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
    await waitFor(() => expect(api.saveProject).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.saveProject).mock.calls.at(-1)!;
    const saved = payload.manifest as unknown as { mixing: { stem_rebalance: Record<string, number> } };
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

  it("puts the stem title above the position controls, and drops the fader's own nameplate below them", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mixer" }));
    await user.click(screen.getByRole("button", { name: "Vocals" }));

    const inspectorFader = screen.getByRole("slider", { name: "Selected stem gain" });
    const title = screen.getByText("enabled").closest("p")!;
    const frontBackLabel = screen.getByText("Front");

    expect(title.textContent).toContain("Vocals");
    // DOCUMENT_POSITION_FOLLOWING (4): title precedes the Front/Back slider.
    expect(title.compareDocumentPosition(frontBackLabel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // ...which precedes the fader — position/EQ sit between the title and it.
    expect(frontBackLabel.compareDocumentPosition(inspectorFader) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("writes mastering edits to the selected track, independent of the mixing tab's edit scope", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    // Mixing's edit scope stays on its default ("project") — mastering must
    // still save per-track regardless, since it has no project-wide mode.
    await user.click(screen.getByRole("button", { name: /Mastering/ }));
    fireEvent.click(screen.getByRole("switch", { name: "Loudness" }));

    await waitFor(() => expect(api.saveProjectTrack).toHaveBeenCalled());
    const [, , payload] = vi.mocked(api.saveProjectTrack).mock.calls.at(-1)!;
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
    const lfeSlider = screen.getByRole("slider", { name: "LFE send" });
    fireEvent.keyDown(lfeSlider, { key: "ArrowUp" });

    await waitFor(() => expect(api.saveProject).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.saveProject).mock.calls.at(-1)!;
    const saved = payload.manifest as unknown as { mixing: { stem_routing: Record<string, Record<string, number>> } };
    expect(saved.mixing.stem_routing.Vocals.LFE).toBeCloseTo(0.01);
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

    await waitFor(() => expect(api.saveProject).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.saveProject).mock.calls.at(-1)!;
    const saved = payload.manifest as unknown as { mixing: { stem_enabled: Record<string, boolean> } };
    expect(saved.mixing.stem_enabled.Vocals).toBe(false);
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
