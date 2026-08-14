import * as React from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  Button,
  Label,
  Input,
} from "upmixer-web";

export const ExportDialog = () => (
  <Dialog open>
    <DialogContent className="w-[520px] max-w-none translate-x-0 translate-y-0 left-auto top-auto relative p-5">
      <DialogHeader>
        <DialogTitle>Export master</DialogTitle>
        <DialogDescription>
          Render the mastered mix to a delivery file.
        </DialogDescription>
      </DialogHeader>
      <div className="mt-4 space-y-1.5">
        <Label htmlFor="fname">File name</Label>
        <Input id="fname" defaultValue="ambient_session_714.wav" />
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" size="sm">
          Cancel
        </Button>
        <Button size="sm">Export</Button>
      </div>
    </DialogContent>
  </Dialog>
);
