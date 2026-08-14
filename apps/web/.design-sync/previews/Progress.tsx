import * as React from "react";
import { Progress } from "upmixer-web";

export const Levels = () => (
  <div className="w-72 space-y-4">
    <div className="space-y-1">
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>Separating stems</span>
        <span>25%</span>
      </div>
      <Progress value={25} />
    </div>
    <div className="space-y-1">
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>Upmixing</span>
        <span>68%</span>
      </div>
      <Progress value={68} />
    </div>
    <div className="space-y-1">
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>Mastering</span>
        <span>100%</span>
      </div>
      <Progress value={100} />
    </div>
  </div>
);
