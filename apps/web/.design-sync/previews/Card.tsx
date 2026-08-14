import * as React from "react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
  Button,
  Badge,
} from "upmixer-web";

export const ProjectCard = () => (
  <Card className="w-80">
    <CardHeader>
      <CardTitle>Ambient Session</CardTitle>
      <CardDescription>Stereo · 44.1 kHz · 3:42</CardDescription>
    </CardHeader>
    <CardContent className="text-sm text-muted-foreground">
      Upmixed to 7.1.4 with stem separation. Mastered to −14 LUFS with
      true-peak limiting at −1 dBTP.
    </CardContent>
    <CardFooter className="gap-2">
      <Button size="sm">Open</Button>
      <Button size="sm" variant="outline">
        Export
      </Button>
    </CardFooter>
  </Card>
);

export const StatCard = () => (
  <Card className="w-64">
    <CardHeader className="flex-row items-center justify-between space-y-0">
      <CardTitle className="text-sm font-medium text-muted-foreground">
        Integrated loudness
      </CardTitle>
      <Badge variant="success">In spec</Badge>
    </CardHeader>
    <CardContent>
      <div className="text-2xl font-semibold tabular-nums">−14.0 LUFS</div>
      <p className="text-xs text-muted-foreground">Target −14 · TP −1.0 dBTP</p>
    </CardContent>
  </Card>
);
