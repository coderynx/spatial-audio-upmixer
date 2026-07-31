import * as React from "react";
import { NumberField } from "upmixer-web";

export const Fields = () => {
  const [lufs, setLufs] = React.useState<number | null>(-14);
  const [tp, setTp] = React.useState<number | null>(-1);
  return (
    <div className="w-64 space-y-3">
      <NumberField label="Target loudness" value={lufs} step={0.5} suffix="LUFS" onChange={setLufs} />
      <NumberField label="True peak" value={tp} step={0.1} suffix="dBTP" hint="Ceiling for the limiter" onChange={setTp} />
    </div>
  );
};
