import * as React from "react";
import type { Configuration, MasteringReference } from "@/api";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Manifest } from "@/lib/manifest";
import { AdvancedSection } from "./sections/AdvancedSection";
import { MasteringSection } from "./sections/MasteringSection";
import { OutputSection } from "./sections/OutputSection";
import { ProcessingSection } from "./sections/ProcessingSection";
import { SpatialSection } from "./sections/SpatialSection";
import { StemsSection } from "./sections/StemsSection";

export function ManifestEditor({
  manifest,
  setManifest,
  configuration,
  rawManifest,
  rawError,
  onRawChange,
  masteringReference,
  referenceUploading,
  referenceError,
  onReferenceUpload,
  onReferenceClear,
}: {
  manifest: Manifest;
  setManifest: React.Dispatch<React.SetStateAction<Manifest>>;
  configuration: Configuration | null;
  rawManifest: string;
  rawError: string | null;
  onRawChange: (value: string) => void;
  masteringReference: MasteringReference | null;
  referenceUploading: boolean;
  referenceError: string | null;
  onReferenceUpload: (file: File) => void;
  onReferenceClear: () => void;
}) {
  return (
    <Tabs
      defaultValue="output"
      onValueChange={(value) => {
        if (value === "advanced")
          onRawChange(JSON.stringify(manifest, null, 2));
      }}
    >
      <TabsList className="grid h-auto w-full grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
        <TabsTrigger value="output">Output</TabsTrigger>
        <TabsTrigger value="spatial">Spatial</TabsTrigger>
        <TabsTrigger value="stems">Stems</TabsTrigger>
        <TabsTrigger value="mastering">Mastering</TabsTrigger>
        <TabsTrigger value="processing">Processing</TabsTrigger>
        <TabsTrigger value="advanced">Advanced</TabsTrigger>
      </TabsList>
      <TabsContent value="output">
        <OutputSection
          manifest={manifest}
          setManifest={setManifest}
          configuration={configuration}
        />
      </TabsContent>
      <TabsContent value="spatial">
        <SpatialSection
          manifest={manifest}
          setManifest={setManifest}
          configuration={configuration}
        />
      </TabsContent>
      <TabsContent value="stems">
        <StemsSection
          manifest={manifest}
          setManifest={setManifest}
          configuration={configuration}
        />
      </TabsContent>
      <TabsContent value="mastering">
        <MasteringSection
          manifest={manifest}
          setManifest={setManifest}
          configuration={configuration}
          masteringReference={masteringReference}
          referenceUploading={referenceUploading}
          referenceError={referenceError}
          onReferenceUpload={onReferenceUpload}
          onReferenceClear={onReferenceClear}
        />
      </TabsContent>
      <TabsContent value="processing">
        <ProcessingSection
          manifest={manifest}
          setManifest={setManifest}
          configuration={configuration}
        />
      </TabsContent>
      <TabsContent value="advanced">
        <AdvancedSection
          rawManifest={rawManifest}
          rawError={rawError}
          onChange={onRawChange}
        />
      </TabsContent>
    </Tabs>
  );
}
