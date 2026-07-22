import * as React from "react"
import { cn } from "@/lib/utils"

export function Progress({ value = 0, className }: { value?: number; className?: string }) {
  return <div className={cn("relative h-2 w-full overflow-hidden rounded-full bg-primary/15", className)} role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}>
    <div className="h-full bg-primary transition-all duration-500" style={{ transform: `translateX(-${100 - value}%)` }} />
  </div>
}
