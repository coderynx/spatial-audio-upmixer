import { describe, expect, it } from "vitest";
import { bedLobeIntensity, isObjectStem, projectScenePoint, sceneSpeakerPosition, smoothSceneLevel, zoomSceneCamera } from "./SceneView";

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

describe("scene speakers", () => {
  it("shows the non-positional LFE channel at the listener", () => {
    expect(sceneSpeakerPosition("LFE")).toEqual({ x: 0, y: 0, z: 0 });
  });
});

describe("bed lobes", () => {
  it("keeps stronger routed channels visible and hides muted channels", () => {
    expect(bedLobeIntensity(0.2, 1, false)).toBeGreaterThan(bedLobeIntensity(0.2, 0.25, false));
    expect(bedLobeIntensity(0.2, 1, true)).toBe(0);
  });
});

describe("scene intensity", () => {
  it("eases colour changes instead of stepping to the meter value", () => {
    expect(smoothSceneLevel(0, 1, 1 / 60)).toBeGreaterThan(0);
    expect(smoothSceneLevel(0, 1, 1 / 60)).toBeLessThan(1);
  });
});

describe("scene zoom", () => {
  it("zooms within the camera's usable range", () => {
    expect(zoomSceneCamera({ yaw: 0, pitch: 0, distance: 4 }, -1000).distance).toBeLessThan(4);
    expect(zoomSceneCamera({ yaw: 0, pitch: 0, distance: 2 }, -1000).distance).toBe(2);
    expect(zoomSceneCamera({ yaw: 0, pitch: 0, distance: 7 }, 1000).distance).toBe(7);
  });
});
