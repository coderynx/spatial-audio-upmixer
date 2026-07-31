import * as React from "react";
import { StemSelectorGrid } from "upmixer-web";

export const Grid = () => {
  const [selected, setSelected] = React.useState<string[]>(["vocals", "drums", "bass"]);
  return (
    <div className="w-96">
      <StemSelectorGrid
        available={["vocals", "drums", "bass", "guitar", "piano", "other"]}
        selected={selected}
        onChange={setSelected}
      />
    </div>
  );
};
