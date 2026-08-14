import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { downloadWithProgress } from "./download";

function streamOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  let index = 0;
  return new ReadableStream({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(chunks[index++]);
      } else {
        controller.close();
      }
    },
  });
}

describe("downloadWithProgress", () => {
  let anchorClick: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    anchorClick = vi.fn();
    const realCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const element = realCreateElement(tag);
      if (tag === "a") (element as HTMLAnchorElement).click = anchorClick as unknown as () => void;
      return element;
    });
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:mock"), revokeObjectURL: vi.fn() });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("reports increasing progress ending at 1 and downloads with the server filename", async () => {
    const chunks = [new Uint8Array(4), new Uint8Array(6)];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(streamOf(chunks), {
          status: 200,
          headers: { "Content-Disposition": 'attachment; filename="Song-stems.zip"' },
        }),
      ),
    );

    const fractions: number[] = [];
    await downloadWithProgress("/api/v1/projects/p/tracks/t/stems/archive", 10, (fraction) => fractions.push(fraction));

    expect(fractions).toEqual([0.4, 1]);
    expect(anchorClick).toHaveBeenCalledOnce();
  });

  it("throws the server's detail message on a failed response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Track has no stems to download" }), { status: 409 })),
    );

    await expect(downloadWithProgress("/api/v1/projects/p/tracks/t/stems/archive", 0, () => {})).rejects.toThrow(
      "Track has no stems to download",
    );
  });
});
