import * as React from "react";
import { SelectField } from "upmixer-web";

export const Fields = () => {
  const [fmt, setFmt] = React.useState("714");
  const [rate, setRate] = React.useState("48000");
  return (
    <div className="w-64 space-y-3">
      <SelectField
        label="Output format"
        value={fmt}
        onChange={setFmt}
        options={[
          { value: "stereo", label: "Stereo" },
          { value: "51", label: "5.1" },
          { value: "714", label: "7.1.4 Atmos" },
        ]}
      />
      <SelectField
        label="Sample rate"
        value={rate}
        onChange={setRate}
        hint="Matches the source when possible"
        options={[
          { value: "44100", label: "44.1 kHz" },
          { value: "48000", label: "48 kHz" },
          { value: "96000", label: "96 kHz" },
        ]}
      />
    </div>
  );
};
