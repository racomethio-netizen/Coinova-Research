import * as React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-line bg-white p-5 shadow-sm sm:p-6", className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn("text-lg font-semibold", className)} {...props} />;
}

export function Alert({ className, tone = "info", ...props }: React.HTMLAttributes<HTMLDivElement> & { tone?: "info" | "error" | "success" }) {
  const tones = {
    info: "border-navy-700/20 bg-mist text-ink",
    error: "border-red-700/40 bg-red-50 text-red-900",
    success: "border-emerald-600/40 bg-emerald-100 text-emerald-700",
  };
  return <div role={tone === "error" ? "alert" : "status"} className={cn("rounded-lg border p-4 text-sm", tones[tone], className)} {...props} />;
}
