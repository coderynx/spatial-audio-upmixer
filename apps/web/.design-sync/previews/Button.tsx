import * as React from "react";
import { Button } from "upmixer-web";

export const Variants = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button>Render</Button>
    <Button variant="secondary">Preview</Button>
    <Button variant="outline">Import audio</Button>
    <Button variant="ghost">Reset</Button>
    <Button variant="destructive">Delete project</Button>
    <Button variant="success">Export ready</Button>
    <Button variant="warning">Clipping</Button>
    <Button variant="link">Learn more</Button>
  </div>
);

export const Sizes = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button size="sm">Small</Button>
    <Button size="default">Default</Button>
    <Button size="lg">Large</Button>
    <Button size="icon" aria-label="Play">▶</Button>
  </div>
);

export const States = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button>Enabled</Button>
    <Button disabled>Disabled</Button>
    <Button variant="outline" disabled>
      Rendering…
    </Button>
  </div>
);
