import * as React from "react";
import { ToggleField } from "upmixer-web";

export const Toggles = () => {
  const [v, setV] = React.useState({ tp: true, norm: false });
  return (
    <div className="w-80 space-y-2">
      <ToggleField
        label="True-peak limiting"
        description="Catches inter-sample peaks above the ceiling before export."
        checked={v.tp}
        onChange={(c) => setV((p) => ({ ...p, tp: c }))}
      />
      <ToggleField
        label="Loudness normalization"
        description="Renders to the BS.1770 integrated target instead of leaving gain untouched."
        checked={v.norm}
        onChange={(c) => setV((p) => ({ ...p, norm: c }))}
      />
    </div>
  );
};
