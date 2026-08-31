import { describe, expect, it } from "vitest";

import type { ProjectStem } from "@/api";
import { TEST_ENGINE_CONSTANTS } from "../engineConstants.fixture";
import { resolveStemMixes } from "./stemMix";

describe("resolveStemMixes", () => {
  it("seeds authored objects at unity until exact route normalization lands", () => {
    const stems = resolveStemMixes({
      stems: [
        { id: "v", stem_key: "Vocals" } as ProjectStem,
        { id: "b", stem_key: "Bass" } as ProjectStem,
      ],
      scene: { stems: {} },
      mix: {
        bed_trim_db: 3,
        stem_rebalance: { Vocals: 1, Bass: 2 },
        stem_routing: { Vocals: { C: 1 }, Bass: { C: 1 } },
        stem_object_mode: { Vocals: "linked-stereo" },
        stem_placement: {
          Vocals: { azimuth_deg: 0, elevation_deg: 0, width_deg: 0, object_size: 0 },
        },
      },
      stemEqTaps: new Map(),
      constants: TEST_ENGINE_CONSTANTS,
    });

    expect(stems[0].routeScale).toBe(1);
    expect(stems[0].rebalanceDb).toBe(1);
    expect(stems[1].routeScale).toBeCloseTo(
      1 / TEST_ENGINE_CONSTANTS.channelGains.center,
      12,
    );
    expect(stems[1].rebalanceDb).toBe(5);
  });
});
