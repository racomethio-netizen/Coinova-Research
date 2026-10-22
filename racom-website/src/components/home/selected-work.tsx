import { projects } from "@/content/projects";
import { ProjectCard } from "@/components/content/project-card";
import { ArrowLink } from "@/components/ui/button";
import { Section, SectionHeading } from "@/components/ui/section";

export function SelectedWork() {
  return (
    <Section labelledBy="work-title" className="border-t border-line">
      <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
        <SectionHeading
          id="work-title"
          index="02"
          eyebrow="Selected work"
          title="Concepts that show how we think."
          description="Self-initiated concept projects exploring real problems — each one clearly labelled as a concept, not a client engagement."
        />
        <ArrowLink href="/work" className="shrink-0">
          View all work
        </ArrowLink>
      </div>
      <div className="mt-14 grid gap-x-8 gap-y-14 md:grid-cols-2">
        {projects.slice(0, 4).map((p) => (
          <ProjectCard key={p.slug} project={p} />
        ))}
      </div>
    </Section>
  );
}
