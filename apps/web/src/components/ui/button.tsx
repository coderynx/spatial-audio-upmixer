import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-40 [&_svg]:size-3.5",
  { variants: {
    variant: {
      default: "bg-primary text-primary-foreground hover:bg-primary/85",
      destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/85",
      success: "bg-success text-success-foreground hover:bg-success/85",
      warning: "bg-warning text-warning-foreground hover:bg-warning/85",
      outline: "border border-input bg-transparent hover:bg-accent hover:text-accent-foreground",
      secondary: "bg-secondary text-secondary-foreground hover:bg-accent",
      ghost: "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
      link: "text-primary underline-offset-4 hover:underline",
    },
    size: { default: "h-7 px-3 text-[13px]", sm: "h-6 rounded-[6px] px-2.5 text-xs", lg: "h-9 px-5 text-sm", icon: "h-7 w-7" },
  }, defaultVariants: { variant: "default", size: "default" } },
)

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> { asChild?: boolean }
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, ...props }, ref) => {
  const Comp = asChild ? Slot : "button"
  return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
})
Button.displayName = "Button"
