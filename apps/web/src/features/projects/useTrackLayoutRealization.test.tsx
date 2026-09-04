import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectTrack } from "@/api";
import { normalizeManifest } from "@/lib/manifest";
import { resolveTrackLayoutManifest, useTrackLayoutRealization } from "./useTrackLayoutRealization";

const panner = {
  maxElevationDeg: () => 45,
  presetTreatments: () => ({}),
  placementRoute: () => ({ FL: .5, FR: .5 }),
};

vi.mock("./wasmEngine/panner", () => ({
  NEUTRAL_PLACEMENT: { azimuth_deg: 0, elevation_deg: 0, width_deg: 0, object_size: 0 },
  loadPanner: vi.fn(async () => panner),
}));

const track = {
  id: "track-a",
  layouts: ["7.1.4"],
  layout_overrides: { "7.1.4": { mixing: { bed_trim_db: 2 } } },
  scene_overrides: {},
} as unknown as ProjectTrack;

function renderRealization(save = vi.fn(async () => {})) {
  const base = normalizeManifest({ mixing: { bed_trim_db: 1 } });
  return renderHook(() => useTrackLayoutRealization({
    projectId: "project-a", projectManifest: base, track, layout: "7.1.4", channels: ["FL", "FR"], save, onError: () => {},
  }));
}

describe("Track Layout Realization", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("resolves the selected layout over the project manifest", () => {
    const manifest = resolveTrackLayoutManifest(normalizeManifest({ mixing: { bed_trim_db: 1 } }), track, "7.1.4");
    expect(manifest?.mixing.bed_trim_db).toBe(2);
    expect(manifest?.mixing.channel_layout).toBe("7.1.4");
  });

  it("retains a failed draft until retry succeeds", async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(undefined);
    const { result } = renderRealization(save);
    const manifest = result.current.manifest!;

    act(() => result.current.update({ ...manifest, mixing: { ...manifest.mixing, bed_trim_db: 3 } }));
    await act(async () => { await vi.advanceTimersByTimeAsync(350); });

    expect(result.current.manifest?.mixing.bed_trim_db).toBe(3);
    expect(result.current.saveFailed).toBe(true);
    await act(async () => { result.current.retry(); await Promise.resolve(); });
    expect(save).toHaveBeenCalledTimes(2);
    expect(result.current.hasUncommittedChanges).toBe(false);
  });

  it("derives routing when placement changes", async () => {
    const { result } = renderRealization();
    await act(async () => { await Promise.resolve(); });

    act(() => result.current.setPlacement("Vocals", { azimuth_deg: 20, elevation_deg: 0, width_deg: 0, object_size: 0 }));
    expect(result.current.manifest?.mixing.stem_placement.Vocals).toMatchObject({ azimuth_deg: 20 });
    expect(result.current.manifest?.mixing.stem_routing.Vocals).toEqual({ FL: .5, FR: .5 });
  });

  it("commits undo through the realization", async () => {
    const save = vi.fn(async () => {});
    const { result } = renderRealization(save);
    const manifest = result.current.manifest!;

    act(() => result.current.update({ ...manifest, mixing: { ...manifest.mixing, bed_trim_db: 3 } }));
    await act(async () => { await vi.advanceTimersByTimeAsync(350); });
    act(() => result.current.history.undo());
    await act(async () => { await vi.advanceTimersByTimeAsync(350); });

    expect(save).toHaveBeenCalledTimes(2);
    expect(result.current.manifest?.mixing.bed_trim_db).toBe(2);
  });
});
