import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { type Project, projectKindLabel } from "@/content/projects";
import { ProjectArt } from "@/components/visuals/project-art";
import { Badge } from "@/components/ui/badge";

export function ProjectCard({
  project,
  headingLevel = "h3",
}: {
  project: Project;
  headingLevel?: "h2" | "h3";
}) {
  const Heading = headingLevel;
  return (
    <article className="group relative flex h-full flex-col">
      <div className="relative aspect-[16/10] overflow-hidden rounded-2xl border border-line bg-midnight transition-colors duration-500 ease-out group-hover:border-violet/40">
        <ProjectArt
          variant={project.art}
          className="h-full w-full transition-transform duration-700 ease-out group-hover:scale-[1.025]"
        />
        <div className="absolute top-3 left-3 flex gap-2">
          <Badge
            tone={project.kind === "concept" ? "warning" : "cyan"}
            className="bg-obsidian/80 backdrop-blur"
          >
            {projectKindLabel(project.kind)}
          </Badge>
        </div>
      </div>
      <div className="mt-5 flex items-start justify-between gap-4">
        <div>
          <p className="font-mono text-[0.7rem] tracking-[0.16em] text-subtle uppercase">
            {project.category}
          </p>
          <Heading className="mt-2 font-display text-xl font-semibold tracking-tight text-ink sm:text-2xl">
            <Link href={`/work/${project.slug}`} className="after:absolute after:inset-0 after:content-['']">
              {project.title}
            </Link>
          </Heading>
          <p className="mt-2 max-w-prose text-[0.95rem] leading-relaxed text-muted">{project.summary}</p>
        </div>
        <span
          aria-hidden="true"
          className="mt-6 inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-line-strong text-muted transition-colors group-hover:border-violet-soft/60 group-hover:text-ink"
        >
          <ArrowUpRight className="h-4 w-4" />
        </span>
      </div>
    </article>
  );
}
