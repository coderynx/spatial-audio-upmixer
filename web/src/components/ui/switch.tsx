import * as SwitchPrimitive from "@radix-ui/react-switch"
import * as React from "react"
import { cn } from "@/lib/utils"

export const Switch = React.forwardRef<React.ElementRef<typeof SwitchPrimitive.Root>, React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>>(({ className, ...props }, ref) => <SwitchPrimitive.Root className={cn("peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent bg-input transition-colors data-[state=checked]:bg-primary", className)} {...props} ref={ref}><SwitchPrimitive.Thumb className="pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg transition-transform data-[state=checked]:translate-x-4" /></SwitchPrimitive.Root>)
