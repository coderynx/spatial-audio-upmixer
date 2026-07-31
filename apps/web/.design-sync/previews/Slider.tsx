import * as React from "react";
import { Slider, Label } from "upmixer-web";

export const Default = () => (
  <div className="w-72">
    <Slider defaultValue={[60]} min={0} max={100} step={1} />
  </div>
);

export const Labeled = () => (
  <div className="w-72 space-y-4">
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>Center width</Label>
        <span className="font-mono text-[11px] tabular-nums">0.42</span>
      </div>
      <Slider defaultValue={[42]} min={0} max={100} step={1} />
    </div>
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>Ambience</Label>
        <span className="font-mono text-[11px] tabular-nums">0.75</span>
      </div>
      <Slider defaultValue={[75]} min={0} max={100} step={1} />
    </div>
  </div>
);
