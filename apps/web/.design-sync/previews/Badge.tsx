import * as React from "react";
import { Badge } from "upmixer-web";

export const Variants = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Badge>Rendering</Badge>
    <Badge variant="secondary">7.1.4</Badge>
    <Badge variant="success">In spec</Badge>
    <Badge variant="warning">Clipping</Badge>
    <Badge variant="destructive">Failed</Badge>
    <Badge variant="outline">Draft</Badge>
  </div>
);
