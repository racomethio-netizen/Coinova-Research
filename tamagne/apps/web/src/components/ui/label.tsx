import * as React from "react";
import { cn } from "@/lib/utils";

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("mb-1.5 block text-sm font-medium text-ink", className)} {...props} />;
}

/** Error text linked to its field via aria-describedby. Prefixed with "Error:" so meaning isn't colour-only. */
export function FieldError({ id, children }: { id: string; children?: React.ReactNode }) {
  if (!children) return null;
  return (
    <p id={id} className="mt-1.5 text-sm font-medium text-red-700" role="alert">
      {children}
    </p>
  );
}
