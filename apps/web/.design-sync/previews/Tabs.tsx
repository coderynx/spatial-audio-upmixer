import * as React from "react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "upmixer-web";

export const Sections = () => (
  <Tabs defaultValue="upmix" className="w-96">
    <TabsList>
      <TabsTrigger value="upmix">Upmix</TabsTrigger>
      <TabsTrigger value="master">Master</TabsTrigger>
      <TabsTrigger value="export">Export</TabsTrigger>
    </TabsList>
    <TabsContent value="upmix" className="text-sm text-muted-foreground">
      Coherence-based STFT analysis splits direct and ambient energy across the
      target layout.
    </TabsContent>
    <TabsContent value="master" className="text-sm text-muted-foreground">
      Spectral EQ, bus compression, BS.1770 loudness, and true-peak limiting.
    </TabsContent>
    <TabsContent value="export" className="text-sm text-muted-foreground">
      ADM-BWF for Atmos delivery, or interleaved WAV per channel layout.
    </TabsContent>
  </Tabs>
);
