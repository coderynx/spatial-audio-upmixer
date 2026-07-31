import * as React from "react";
import { Label, Input, Switch } from "upmixer-web";

export const WithInput = () => (
  <div className="w-64 space-y-1.5">
    <Label htmlFor="lufs">Target loudness</Label>
    <Input id="lufs" defaultValue="-14 LUFS" />
  </div>
);

export const WithControl = () => (
  <div className="flex w-64 items-center justify-between">
    <Label htmlFor="tp">True-peak limiting</Label>
    <Switch id="tp" defaultChecked />
  </div>
);
