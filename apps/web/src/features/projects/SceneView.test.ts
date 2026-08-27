import { describe, expect, it } from "vitest";
import { isObjectStem, projectScenePoint } from "./SceneView";

describe("scene projection", () => {
  it("keeps the room centre centred and enlarges nearer objects", () => {
    const camera = { yaw: 0, pitch: 22 * Math.PI / 180 };
    const centre = projectScenePoint({ x: 0, y: 0.5, z: 0 }, 400, 300, camera);
    const near = projectScenePoint({ x: 0, y: 0.5, z: 0.5 }, 400, 300, camera);
    const far = projectScenePoint({ x: 0, y: 0.5, z: -0.5 }, 400, 300, camera);

    expect(centre.x).toBeCloseTo(200);
    expect(centre.y).toBeCloseTo(150);
    expect(near.scale).toBeGreaterThan(far.scale);
  });
});

describe("scene stem kinds", () => {
  it("keeps routed-only stems as beds, including zone-keyed stems", () => {
    const objects = new Set(["Vocals", "Crash", "Bass", "Backing Vocals"]);

    expect(isObjectStem("Vocals@front", objects)).toBe(true);
    expect(isObjectStem("Crash", objects)).toBe(true);
    expect(isObjectStem("Bass", objects)).toBe(false);
    expect(isObjectStem("Backing Vocals", objects)).toBe(false);
    expect(isObjectStem("Crowd", objects)).toBe(false);
    expect(isObjectStem("Other", new Set(["Other"]))).toBe(false);
  });
});
