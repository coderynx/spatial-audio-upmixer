// Browser fetch loader; kept decoupled from the golden-diff harness's disk-read loader.
export async function fetchDecodeFilterPart(ctx: BaseAudioContext, partName: string): Promise<AudioBuffer> {
  const response = await fetch(`/hrir/${partName}.wav`);
  if (!response.ok) throw new Error(`Decode filter part missing: ${partName}.wav`);
  const data = await response.arrayBuffer();
  return ctx.decodeAudioData(data);
}

// Same rationale as fetchDecodeFilterPart above, for the transaural XTC filter WAV.
export async function fetchXtcFilterSet(ctx: BaseAudioContext, name: string): Promise<AudioBuffer> {
  const response = await fetch(`/xtc/${name}.wav`);
  if (!response.ok) throw new Error(`Crosstalk filter set missing: ${name}.wav`);
  const data = await response.arrayBuffer();
  return ctx.decodeAudioData(data);
}

export async function loadBuffer(ctx: AudioContext, url: string): Promise<AudioBuffer> {
  const response = await fetch(url);
  if (!response.ok) throw new Error("Preview stem could not be loaded");
  const data = await response.arrayBuffer();
  return ctx.decodeAudioData(data);
}
