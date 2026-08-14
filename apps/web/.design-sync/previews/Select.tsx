import * as React from "react";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
  Label,
} from "upmixer-web";

export const Pickers = () => (
  <div className="w-64 space-y-3">
    <div className="space-y-1.5">
      <Label>Output format</Label>
      <Select>
        <SelectTrigger>
          <SelectValue placeholder="Choose a format…" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="stereo">Stereo</SelectItem>
          <SelectItem value="51">5.1</SelectItem>
          <SelectItem value="714">7.1.4 Atmos</SelectItem>
        </SelectContent>
      </Select>
    </div>
    <div className="space-y-1.5">
      <Label>Separation model</Label>
      <Select>
        <SelectTrigger>
          <SelectValue placeholder="Select a model…" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="htdemucs">HT-Demucs</SelectItem>
          <SelectItem value="mdx">MDX-Net</SelectItem>
        </SelectContent>
      </Select>
    </div>
  </div>
);
