import { describe, expect, it } from "vitest";

import { withReferenceMatchParams } from "./audioEngine";

describe("withReferenceMatchParams", () => {
  it("appends strength/max_db as the first query params", () => {
    expect(withReferenceMatchParams("/api/v1/projects/1/reference-match/fir", 0.5, 4)).toBe(
      "/api/v1/projects/1/reference-match/fir?strength=0.5&max_db=4",
    );
  });

  it("appends with & when the base url already carries a query param", () => {
    expect(withReferenceMatchParams("/fir?v=2", 1, 6)).toBe("/fir?v=2&strength=1&max_db=6");
  });
});
