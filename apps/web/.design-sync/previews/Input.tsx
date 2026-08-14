import * as React from "react";
import { Input, Label } from "upmixer-web";

export const Default = () => (
  <div className="w-64 space-y-1.5">
    <Label htmlFor="name">Project name</Label>
    <Input id="name" defaultValue="Ambient Session" />
  </div>
);

export const States = () => (
  <div className="w-64 space-y-2">
    <Input placeholder="Search projects…" />
    <Input type="number" defaultValue={44100} />
    <Input disabled defaultValue="Locked while rendering" />
  </div>
);
