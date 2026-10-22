"use client";

import { useState } from "react";
import { AnimatePresence, LazyMotion, MotionConfig, domAnimation, m } from "motion/react";
import type { Project, ProjectCategory } from "@/content/projects";
import { ProjectCard } from "@/components/content/project-card";
import { cn } from "@/lib/cn";

type Filter = "All" | ProjectCategory;

/** Portfolio grid with an accessible category filter (toggle buttons + live result count). */
export function WorkGrid({ projects, categories }: { projects: Project[]; categories: ProjectCategory[] }) {
  const [filter, setFilter] = useState<Filter>("All");
  const visible = filter === "All" ? projects : projects.filter((p) => p.category === filter);
  const filters: Filter[] = ["All", ...categories.filter((c) => projects.some((p) => p.category === c))];

  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <div role="group" aria-label="Filter projects by category" className="flex flex-wrap gap-2">
          {filters.map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
              className={cn(
                "min-h-10 rounded-full border px-4 text-sm transition-colors",
                filter === f
                  ? "border-violet/60 bg-violet/15 text-ink"
                  : "border-line-strong text-muted hover:border-violet-soft/40 hover:text-ink",
              )}
            >
              {f}
            </button>
          ))}
        </div>
        <p className="mt-4 text-sm text-subtle" aria-live="polite">
          Showing {visible.length} {visible.length === 1 ? "project" : "projects"}
          {filter !== "All" && ` in ${filter}`}
        </p>
        <ul className="mt-10 grid gap-x-8 gap-y-14 md:grid-cols-2">
          <AnimatePresence mode="popLayout" initial={false}>
            {visible.map((p) => (
              <m.li
                key={p.slug}
                layout
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.98 }}
                transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
              >
                <ProjectCard project={p} headingLevel="h2" />
              </m.li>
            ))}
          </AnimatePresence>
        </ul>
      </MotionConfig>
    </LazyMotion>
  );
}
