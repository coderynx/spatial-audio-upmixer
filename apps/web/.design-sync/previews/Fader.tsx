import * as React from "react";
import { Fader } from "upmixer-web";

const MONITOR_TICKS = [0, -6, -12, -24, -48];

function fmt(db: number) {
  return db <= -60 ? "−∞ dB" : `${db.toFixed(1)} dB`.replace("-", "−");
}

export const VolumeFader = () => {
  const [db, setDb] = React.useState(-6);
  return (
    <div className="flex h-56 items-stretch gap-3 rounded-md border bg-card p-3">
      <div className="w-10">
        <Fader
          className="h-full"
          label="Monitor"
          value={db}
          min={-60}
          max={0}
          step={0.5}
          detent={0}
          ticks={MONITOR_TICKS}
          valueText={fmt(db)}
          onChange={setDb}
          onReset={() => setDb(0)}
        />
      </div>
      <div className="flex flex-col justify-end">
        <span className="text-xs tabular-nums text-muted-foreground">{fmt(db)}</span>
      </div>
    </div>
  );
};

export const FaderRack = () => {
  const [levels, setLevels] = React.useState([-3, -6, -9, -12]);
  const labels = ["L", "C", "R", "LFE"];
  return (
    <div className="flex h-56 gap-2 rounded-md border bg-card p-3">
      {levels.map((db, i) => (
        <div key={labels[i]} className="flex w-9 flex-col items-center gap-1">
          <div className="w-full flex-1">
            <Fader
              className="h-full"
              label={labels[i]}
              value={db}
              min={-60}
              max={0}
              step={0.5}
              detent={0}
              ticks={MONITOR_TICKS}
              valueText={fmt(db)}
              onChange={(v) =>
                setLevels((prev) => prev.map((p, j) => (j === i ? v : p)))
              }
            />
          </div>
          <span className="text-[11px] text-muted-foreground">{labels[i]}</span>
        </div>
      ))}
    </div>
  );
};
