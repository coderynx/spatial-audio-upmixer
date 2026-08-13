function filenameFrom(response: Response): string {
  const disposition = response.headers.get("Content-Disposition") ?? ""
  const match = /filename="([^"]+)"/.exec(disposition)
  return match?.[1] ?? "download.zip"
}

/** Stream *url* to disk while reporting progress against *totalBytes* (a
 * caller-known sum, since the server can't send Content-Length for a zip
 * it's writing on the fly). Buffers the decoded stream in memory before
 * triggering the save — fine for a track's worth of stems.
 * ponytail: swap for showSaveFilePicker() streaming if stem sets ever
 * outgrow a browser tab. */
export async function downloadWithProgress(
  url: string,
  totalBytes: number,
  onProgress: (fraction: number) => void,
): Promise<void> {
  const response = await fetch(url)
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail))
  }
  const reader = response.body!.getReader()
  const chunks: Uint8Array[] = []
  let received = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    chunks.push(value)
    received += value.length
    onProgress(totalBytes > 0 ? Math.min(received / totalBytes, 1) : 0)
  }
  const href = URL.createObjectURL(new Blob(chunks, { type: "application/zip" }))
  const anchor = Object.assign(document.createElement("a"), { href, download: filenameFrom(response) })
  anchor.click()
  URL.revokeObjectURL(href)
}
