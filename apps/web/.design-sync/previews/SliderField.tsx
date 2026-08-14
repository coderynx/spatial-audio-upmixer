import * as React from "react";
import { SliderField } from "upmixer-web";

export const Fields = () => {
  const [width, setWidth] = React.useState(0.42);
  const [amb, setAmb] = React.useState(0.75);
  return (
    <div className="w-72 space-y-3">
      <SliderField label="Center width" value={width} min={0} max={1} step={0.01} onChange={setWidth} />
      <SliderField label="Ambience" value={amb} min={0} max={1} step={0.01} onChange={setAmb} />
    </div>
  );
};
