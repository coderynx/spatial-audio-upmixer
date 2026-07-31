import * as React from "react";
import { StemTargetButton } from "upmixer-web";

export const Stems = () => {
  const [active, setActive] = React.useState<Record<string, boolean>>({
    vocals: true,
    drums: true,
    bass: false,
    guitar: false,
  });
  const toggle = (s: string) => setActive((p) => ({ ...p, [s]: !p[s] }));
  return (
    <div className="grid w-72 grid-cols-2 gap-2">
      {["vocals", "drums", "bass", "guitar"].map((s) => (
        <StemTargetButton key={s} stem={s} active={active[s]} onClick={() => toggle(s)} />
      ))}
    </div>
  );
};
