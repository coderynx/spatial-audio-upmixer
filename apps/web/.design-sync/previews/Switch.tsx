import * as React from "react";
import { Switch, Label } from "upmixer-web";

export const States = () => (
  <div className="flex items-center gap-6">
    <Switch defaultChecked aria-label="On" />
    <Switch aria-label="Off" />
    <Switch disabled defaultChecked aria-label="Disabled on" />
    <Switch disabled aria-label="Disabled off" />
  </div>
);

export const WithLabel = () => (
  <div className="w-64 space-y-3">
    <div className="flex items-center justify-between">
      <Label htmlFor="s1">Binaural render</Label>
      <Switch id="s1" defaultChecked />
    </div>
    <div className="flex items-center justify-between">
      <Label htmlFor="s2">LFE channel</Label>
      <Switch id="s2" />
    </div>
  </div>
);
