import * as React from "react";
import { NullablePotField } from "upmixer-web";

export const InheritedAndSet = () => {
  const [set, setSet] = React.useState<number | null>(-3);
  const [inherited, setInherited] = React.useState<number | null>(null);
  return (
    <div className="flex gap-6 rounded-md border bg-card p-4">
      <NullablePotField label="Trim" value={set} defaultValue={0} min={-12} max={12} step={0.5} suffix=" dB" onChange={setSet} />
      <NullablePotField label="Trim" value={inherited} defaultValue={0} min={-12} max={12} step={0.5} suffix=" dB" onChange={setInherited} />
    </div>
  );
};
