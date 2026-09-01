import { describe, expect, it } from "vitest";
import { apiUrl, normalizeApiUrls, resolveApiResourceUrl } from "@/api";
import { normalizeServerUrl } from "@/runtime";

describe("desktop server URLs", () => {
  it("preserves an application root path for requests", () => {
    expect(apiUrl("/api/v1/configuration", "http://localhost:8000/upmixer"))
      .toBe("http://localhost:8000/upmixer/api/v1/configuration");
  });

  it("resolves server resource URLs without duplicating the root path", () => {
    expect(resolveApiResourceUrl("/upmixer/api/v1/audio", "http://localhost:8000/upmixer"))
      .toBe("http://localhost:8000/upmixer/api/v1/audio");
    expect(resolveApiResourceUrl("assets/audio.wav", "http://localhost:8000/upmixer"))
      .toBe("http://localhost:8000/upmixer/assets/audio.wav");
  });

  it("normalizes nested API URL fields and leaves absolute URLs intact", () => {
    expect(normalizeApiUrls({ tracks: [{ audio_url: "/audio.wav" }], cover_url: "https://cdn.test/cover.jpg" }, "http://localhost:8000"))
      .toEqual({ tracks: [{ audio_url: "http://localhost:8000/audio.wav" }], cover_url: "https://cdn.test/cover.jpg" });
  });

  it("rejects unsafe processing-node URL forms", () => {
    expect(() => normalizeServerUrl("file:///tmp/server")).toThrow("HTTP or HTTPS");
    expect(() => normalizeServerUrl("http://user:secret@localhost:8000")).toThrow("Credentials");
    expect(() => normalizeServerUrl("http://localhost:8000?token=secret")).toThrow("query string");
  });
});
