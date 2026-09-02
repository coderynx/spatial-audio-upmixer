import { describe, expect, it } from "vitest";
import type { ProjectTrack } from "@/api";
import { deliveryTargetLabel } from "./deliveryTargets";

const track = { layouts: ["7.1.2"], layout_overrides: { "7.1.2": { format: { delivery_profile: "atmos-music" } } } } as unknown as ProjectTrack;

describe("deliveryTargetLabel", () => {
  it("uses the saved profile name and layout", () => {
    expect(deliveryTargetLabel(track, "7.1.2")).toBe("Dolby Atmos Music ADM-BWF · 7.1.2");
  });
});
