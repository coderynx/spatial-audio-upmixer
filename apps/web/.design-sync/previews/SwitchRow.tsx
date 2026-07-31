import * as React from "react";
import { SwitchRow } from "upmixer-web";

export const Rows = () => {
  const [v, setV] = React.useState({ bin: true, lfe: false, dither: true });
  return (
    <div className="w-72 divide-y rounded-md border bg-card px-3">
      <SwitchRow label="Binaural render" checked={v.bin} onChange={(c) => setV((p) => ({ ...p, bin: c }))} />
      <SwitchRow label="LFE channel" hint="Low-frequency effects" checked={v.lfe} onChange={(c) => setV((p) => ({ ...p, lfe: c }))} />
      <SwitchRow label="Dithering" checked={v.dither} disabled onChange={(c) => setV((p) => ({ ...p, dither: c }))} />
    </div>
  );
};
