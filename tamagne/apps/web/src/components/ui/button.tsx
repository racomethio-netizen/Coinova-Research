import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-md text-sm font-semibold transition-colors focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-emerald-600 disabled:pointer-events-none disabled:opacity-60 min-h-11 px-4",
  {
    variants: {
      variant: {
        default: "bg-emerald-700 text-white hover:bg-emerald-600",
        navy: "bg-navy-900 text-white hover:bg-navy-800",
        outline: "border border-line bg-white text-ink hover:bg-mist",
        ghost: "text-ink hover:bg-mist",
        link: "min-h-0 px-0 text-emerald-700 underline-offset-4 hover:underline",
      },
      size: { default: "", sm: "min-h-9 px-3 text-xs", lg: "min-h-12 px-6 text-base" },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}
