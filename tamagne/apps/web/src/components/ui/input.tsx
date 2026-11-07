import * as React from "react";
import { cn } from "@/lib/utils";

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "block min-h-11 w-full rounded-md border border-line bg-white px-3 text-base text-ink placeholder:text-muted/70",
        "focus-visible:border-emerald-600 focus-visible:outline-2 focus-visible:outline-emerald-600",
        "aria-invalid:border-red-700 aria-invalid:outline-red-700",
        className,
      )}
      {...props}
    />
  );
}

export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "block min-h-11 w-full rounded-md border border-line bg-white px-3 text-base text-ink",
        "focus-visible:border-emerald-600 focus-visible:outline-2 focus-visible:outline-emerald-600",
        className,
      )}
      {...props}
    />
  );
}
