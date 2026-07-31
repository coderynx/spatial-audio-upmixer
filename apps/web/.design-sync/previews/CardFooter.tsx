import * as React from "react";
import { Card, CardContent, CardFooter, Button } from "upmixer-web";

export const InCard = () => (
  <Card className="w-72">
    <CardContent className="pt-3 text-sm text-muted-foreground">
      Ready to export.
    </CardContent>
    <CardFooter className="gap-2">
      <Button size="sm">Open</Button>
      <Button size="sm" variant="outline">
        Export
      </Button>
    </CardFooter>
  </Card>
);
