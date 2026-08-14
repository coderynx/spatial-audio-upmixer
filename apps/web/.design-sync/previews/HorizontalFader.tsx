import * as React from "react";
import { HorizontalFader } from "upmixer-web";

function fmt(db: number) {
  return db <= -60 ? "−∞ dB" : `${db.toFixed(1)} dB`.replace("-", "−");
}

export const TrackVolume = () => {
  const [db, setDb] = React.useState(-6);
  return (
    <div className="w-72 space-y-3 rounded-md border bg-card p-3">
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>Guitar</span>
        <span className="tabular-nums">{fmt(db)}</span>
      </div>
      <HorizontalFader
        label="Guitar volume"
        value={db}
        min={-60}
        max={6}
        step={0.5}
        valueText={fmt(db)}
        onChange={setDb}
        onReset={() => setDb(0)}
      />
    </div>
  );
};

export const Rack = () => {
  const [levels, setLevels] = React.useState({ vocals: -3, bass: -8, drums: -5 });
  const set = (k: keyof typeof levels) => (v: number) =>
    setLevels((p) => ({ ...p, [k]: v }));
  return (
    <div className="w-72 space-y-3 rounded-md border bg-card p-3">
      {(["vocals", "bass", "drums"] as const).map((k) => (
        <div key={k} className="space-y-1">
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span className="capitalize">{k}</span>
            <span className="tabular-nums">{fmt(levels[k])}</span>
          </div>
          <HorizontalFader
            label={`${k} volume`}
            value={levels[k]}
            min={-60}
            max={6}
            step={0.5}
            valueText={fmt(levels[k])}
            onChange={set(k)}
          />
        </div>
      ))}
    </div>
  );
};
