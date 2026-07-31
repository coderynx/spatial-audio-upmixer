import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Configuration, Job } from "@/api";
import { TEST_SERVED_CONSTANTS } from "@/features/projects/engineConstants.fixture";
import { useJobs } from "./useJobs";

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listJobs: vi.fn(async (): Promise<Job[]> => []),
      getConfiguration: vi.fn(async (): Promise<Configuration> => ({
        defaults: {},
        manifest_keys: {},
        choices: {
          channel_layouts: [], output_types: [], output_subtypes: [],
          sample_rates: [], modes: [], spatial_profiles: [],
          stem_eq_profiles: [], stems: [],
          eq_profiles: [], compressor_profiles: [], bass_profiles: [],
        },
        constants: TEST_SERVED_CONSTANTS,
        capabilities: {
          stem_separation: {
            available: false, backend: null, accelerated: false,
            accelerator_detected: false, accelerator_issue: null,
            platform: "test", install_message: null,
          },
        },
      })),
    },
  };
});

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  // vi.restoreAllMocks() only restores vi.spyOn spies; the `listJobs`/
  // `getConfiguration` mocks here are vi.fn()s created once by the hoisted
  // vi.mock factory, so their call history needs an explicit clear.
  vi.clearAllMocks();
});

describe("useJobs polling", () => {
  // Regression: `poll` gates the 2s interval so the jobs list isn't
  // fetched-and-discarded on routes (like the project preview page) that
  // never render `jobs` — see App.tsx's `jobsRoute || storageRoute` gate.
  it("fetches once on mount but never polls when poll is false", async () => {
    const { api } = await import("@/api");
    const listJobs = api.listJobs as unknown as ReturnType<typeof vi.fn>;

    renderHook(() => useJobs(false));
    await act(async () => { await Promise.resolve(); });
    expect(listJobs).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(listJobs).toHaveBeenCalledTimes(1);
  });

  it("polls every 2s when poll is true", async () => {
    const { api } = await import("@/api");
    const listJobs = api.listJobs as unknown as ReturnType<typeof vi.fn>;

    renderHook(() => useJobs(true));
    await act(async () => { await Promise.resolve(); });
    // Mount fetch (refresh()) + the poll effect's immediate refresh(true).
    expect(listJobs).toHaveBeenCalledTimes(2);

    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(listJobs).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(listJobs).toHaveBeenCalledTimes(5);
  });

  it("fetches immediately when poll flips from false to true, without waiting for the interval", async () => {
    const { api } = await import("@/api");
    const listJobs = api.listJobs as unknown as ReturnType<typeof vi.fn>;

    const { rerender } = renderHook(({ poll }) => useJobs(poll), { initialProps: { poll: false } });
    await act(async () => { await Promise.resolve(); });
    expect(listJobs).toHaveBeenCalledTimes(1);

    rerender({ poll: true });
    await act(async () => { await Promise.resolve(); });
    expect(listJobs).toHaveBeenCalledTimes(2);

    // No advance yet — the immediate refresh must not have waited 2s.
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(listJobs).toHaveBeenCalledTimes(2);
  });
});
