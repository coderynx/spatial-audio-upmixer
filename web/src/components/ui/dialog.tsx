import * as DialogPrimitive from "@radix-ui/react-dialog"
import { X } from "lucide-react"
import * as React from "react"
import { cn } from "@/lib/utils"

export const Dialog = DialogPrimitive.Root
export const DialogTrigger = DialogPrimitive.Trigger
export const DialogClose = DialogPrimitive.Close
export const DialogPortal = DialogPrimitive.Portal
export const DialogOverlay = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Overlay>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>>(({ className, ...props }, ref) => <DialogPrimitive.Overlay ref={ref} className={cn("fixed inset-0 z-50 bg-black/70 data-[state=open]:animate-in data-[state=closed]:animate-out", className)} {...props} />)
export const DialogContent = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Content>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>>(({ className, children, ...props }, ref) => <DialogPortal><DialogOverlay /><DialogPrimitive.Content ref={ref} className={cn("fixed left-1/2 top-1/2 z-50 grid max-h-[92vh] w-[calc(100%-2rem)] max-w-4xl -translate-x-1/2 -translate-y-1/2 gap-4 overflow-y-auto rounded-lg border bg-background p-6 shadow-lg", className)} {...props}>{children}<DialogPrimitive.Close className="absolute right-4 top-4 rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring"><X className="h-4 w-4" /><span className="sr-only">Close</span></DialogPrimitive.Close></DialogPrimitive.Content></DialogPortal>)
export const DialogHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("flex flex-col space-y-1.5 text-left", className)} {...props} />
export const DialogTitle = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Title>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>>(({ className, ...props }, ref) => <DialogPrimitive.Title ref={ref} className={cn("text-xl font-semibold leading-none tracking-tight", className)} {...props} />)
export const DialogDescription = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Description>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>>(({ className, ...props }, ref) => <DialogPrimitive.Description ref={ref} className={cn("text-sm text-muted-foreground", className)} {...props} />)
