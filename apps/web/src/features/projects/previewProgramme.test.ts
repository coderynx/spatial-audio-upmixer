import type { ProjectStem } from "@/api";
import { describe, expect, it } from "vitest";
import { createPreviewMonitor, createPreviewProgramme } from "./previewProgramme";

const stems = [{ id: "vocal", stem_key: "Vocals", preview_url: "/stems/vocal.wav", channels: 1 }] as ProjectStem[];

describe("Preview Programme", () => {
  it("keeps source identity separate from resolved mix changes", () => {
    const initial = createPreviewProgramme({ stems, mix: { bed_trim_db: 0 }, sourcePreviewUrl: null, layoutChannels: ["FL", "FR"] });
    const edited = createPreviewProgramme({ stems, mix: { bed_trim_db: 3 }, sourcePreviewUrl: null, layoutChannels: ["FL", "FR"] });

    expect(edited.sourceKey).toBe(initial.sourceKey);
    expect(edited.key).not.toBe(initial.key);
  });

  it("keeps monitor-only choices outside the programme", () => {
    const programme = createPreviewProgramme({ stems, mix: {}, sourcePreviewUrl: null, layoutChannels: ["FL", "FR"] });
    const monitor = createPreviewMonitor({ outputMode: "transaural", speakerEnabled: { FL: true, FR: false } });

    expect(programme).not.toHaveProperty("outputMode");
    expect(monitor).toMatchObject({ outputMode: "transaural", speakerEnabled: { FR: false } });
  });
});
