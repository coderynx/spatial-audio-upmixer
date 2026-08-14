import * as React from "react";
import { Pot } from "upmixer-web";

export const CompressorRow = () => {
  const [vals, setVals] = React.useState({ thr: -18, ratio: 4, atk: 12, rel: 120 });
  const set = (k: keyof typeof vals) => (v: number) =>
    setVals((p) => ({ ...p, [k]: v }));
  return (
    <div className="flex gap-5 rounded-md border bg-card p-4">
      <Pot label="Threshold" value={vals.thr} min={-40} max={0} step={1} suffix=" dB" onChange={set("thr")} />
      <Pot label="Ratio" value={vals.ratio} min={1} max={20} step={0.5} suffix=":1" onChange={set("ratio")} />
      <Pot label="Attack" value={vals.atk} min={0} max={100} step={1} suffix=" ms" onChange={set("atk")} />
      <Pot label="Release" value={vals.rel} min={10} max={500} step={5} suffix=" ms" onChange={set("rel")} />
    </div>
  );
};

export const Sizes = () => {
  const [a, setA] = React.useState(0.6);
  const [b, setB] = React.useState(0.6);
  return (
    <div className="flex items-center gap-5 rounded-md border bg-card p-4">
      <Pot label="Mix" value={a} min={0} max={1} step={0.01} onChange={setA} />
      <Pot label="Mix" value={b} min={0} max={1} step={0.01} size="sm" onChange={setB} />
    </div>
  );
};
