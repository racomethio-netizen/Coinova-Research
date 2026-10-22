import type { Metadata } from "next";
import { projectCategories, projects } from "@/content/projects";
import { pageMetadata } from "@/lib/seo";
import { PageHero } from "@/components/ui/page-hero";
import { Section } from "@/components/ui/section";
import { WorkGrid } from "@/components/work/work-grid";
import { FinalCta } from "@/components/home/final-cta";

export const metadata: Metadata = pageMetadata({
  title: "Work",
  description:
    "Concept projects from Racom Technology exploring web applications, SaaS, AI automation and systems integration. Concepts are clearly labelled and are not client engagements.",
  path: "/work",
});

export default function WorkPage() {
  const hasClientWork = projects.some((p) => p.kind === "client");
  return (
    <>
      <PageHero
        eyebrow="Work"
        title="Selected work and concepts."
        description="A look at how we approach real problems. Projects marked “Concept project” are self-initiated explorations — they are not client engagements and report no client results."
      />
      <Section className="pt-14 sm:pt-16">
        {!hasClientWork && (
          <p className="mb-10 rounded-xl border border-warning/30 bg-warning/[0.06] px-5 py-4 text-sm leading-relaxed text-muted">
            <strong className="font-medium text-warning">Note:</strong> every project currently shown is a
            concept. Client case studies will be published here once clients approve them.
          </p>
        )}
        <WorkGrid projects={projects} categories={projectCategories} />
      </Section>
      <FinalCta title="Have a project like these in mind?" />
    </>
  );
}
