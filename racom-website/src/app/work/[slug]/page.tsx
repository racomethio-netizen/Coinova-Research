import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getProject, projectKindLabel, projects } from "@/content/projects";
import { services } from "@/content/services";
import { pageMetadata } from "@/lib/seo";
import { Badge } from "@/components/ui/badge";
import { Breadcrumbs, PageHero } from "@/components/ui/page-hero";
import { Eyebrow } from "@/components/ui/section";
import { ProjectArt } from "@/components/visuals/project-art";
import { ProjectCard } from "@/components/content/project-card";
import { FinalCta } from "@/components/home/final-cta";

export const dynamicParams = false;

export function generateStaticParams() {
  return projects.map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: PageProps<"/work/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const project = getProject(slug);
  if (!project) return {};
  const prefix = project.kind === "concept" ? "Concept: " : "";
  return pageMetadata({
    title: `${prefix}${project.title}`,
    description: project.summary,
    path: `/work/${project.slug}`,
  });
}

const categoryService: Record<string, string> = {
  "Web Application": "web-development",
  SaaS: "saas-product-development",
  "AI Automation": "ai-automation",
  Integration: "api-systems-integration",
};

export default async function ProjectPage({ params }: PageProps<"/work/[slug]">) {
  const { slug } = await params;
  const project = getProject(slug);
  if (!project) notFound();
  const related = projects.filter((p) => p.slug !== project.slug).slice(0, 2);
  const serviceSlug = categoryService[project.category];
  const service = services.find((s) => s.slug === serviceSlug);

  return (
    <>
      <PageHero eyebrow={project.category} title={project.title} description={project.summary}>
        <div className="flex flex-wrap gap-2">
          <Badge tone={project.kind === "concept" ? "warning" : "cyan"}>
            {projectKindLabel(project.kind)}
          </Badge>
          {project.client && <Badge>Client: {project.client}</Badge>}
        </div>
      </PageHero>

      <div className="container-page py-14 sm:py-20">
        <Breadcrumbs items={[{ label: "Work", href: "/work" }, { label: project.title }]} />
        <div className="overflow-hidden rounded-2xl border border-line">
          <ProjectArt variant={project.art} className="aspect-[16/10] w-full" />
        </div>
        {project.kind === "concept" && (
          <p className="mt-4 text-sm text-subtle">
            Illustration of the concept’s interface direction. This is a self-initiated concept, not a client
            engagement.
          </p>
        )}

        <div className="mt-16 grid gap-14 lg:grid-cols-12">
          <div className="space-y-14 lg:col-span-8">
            <section aria-labelledby="overview">
              <h2 id="overview" className="font-display text-2xl font-semibold tracking-tight text-ink">
                Overview
              </h2>
              <p className="mt-4 text-lg leading-relaxed text-muted">{project.overview}</p>
            </section>
            <section aria-labelledby="problem">
              <h2 id="problem" className="font-display text-2xl font-semibold tracking-tight text-ink">
                The problem
              </h2>
              <p className="mt-4 text-lg leading-relaxed text-muted">{project.problem}</p>
            </section>
            <section aria-labelledby="approach">
              <h2 id="approach" className="font-display text-2xl font-semibold tracking-tight text-ink">
                Our approach
              </h2>
              <ol className="mt-6 space-y-0">
                {project.approach.map((a, i) => (
                  <li key={a} className="grid grid-cols-[2.5rem_1fr] gap-4 border-t border-line py-5">
                    <span className="font-mono text-sm text-violet-soft">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <p className="leading-relaxed text-muted">{a}</p>
                  </li>
                ))}
              </ol>
            </section>
            <section aria-labelledby="results">
              <h2 id="results" className="font-display text-2xl font-semibold tracking-tight text-ink">
                Results
              </h2>
              {project.results.length > 0 ? (
                <ul className="mt-4 space-y-2 text-lg text-muted">
                  {project.results.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              ) : (
                <p className="mt-4 rounded-xl border border-line bg-midnight px-5 py-4 leading-relaxed text-muted">
                  {project.kind === "concept"
                    ? "This is a concept project, so there are no client results to report."
                    : "Results will be published once they are measured and approved by the client."}
                </p>
              )}
            </section>
          </div>
          <aside className="lg:col-span-4" aria-label="Project details">
            <div className="surface space-y-8 rounded-2xl p-7 lg:sticky lg:top-28">
              <div>
                <Eyebrow>Technology</Eyebrow>
                <ul className="mt-4 flex flex-wrap gap-2">
                  {project.stack.map((t) => (
                    <li
                      key={t}
                      className="rounded-full border border-line-strong px-3 py-1 text-sm text-muted"
                    >
                      {t}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <Eyebrow>What it explores</Eyebrow>
                <ul className="mt-4 space-y-2.5">
                  {project.highlights.map((h) => (
                    <li key={h} className="flex gap-2.5 text-sm leading-relaxed text-muted">
                      <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan" />
                      {h}
                    </li>
                  ))}
                </ul>
              </div>
              {service && (
                <div>
                  <Eyebrow>Related service</Eyebrow>
                  <Link
                    href={`/services/${service.slug}`}
                    className="mt-2 inline-block py-1.5 text-sm font-medium text-violet-soft hover:text-ink"
                  >
                    {service.title} →
                  </Link>
                </div>
              )}
            </div>
          </aside>
        </div>

        {related.length > 0 && (
          <section aria-labelledby="more-work" className="mt-24 border-t border-line pt-16">
            <h2 id="more-work" className="font-display text-2xl font-semibold tracking-tight text-ink">
              More work
            </h2>
            <div className="mt-10 grid gap-x-8 gap-y-14 md:grid-cols-2">
              {related.map((p) => (
                <ProjectCard key={p.slug} project={p} />
              ))}
            </div>
          </section>
        )}
      </div>
      <FinalCta
        title="Want something similar, built for your business?"
        description="Tell us about your context and constraints. We will tell you honestly whether and how we can help."
        cta="Discuss a Similar Project"
      />
    </>
  );
}
