import type { Metadata } from "next";
import Link from "next/link";
import { Clock, Mail, MessageSquare, ShieldCheck } from "lucide-react";
import { site } from "@/config/site";
import { products } from "@/content/products";
import { pageMetadata } from "@/lib/seo";
import { PageHero } from "@/components/ui/page-hero";
import { ContactForm } from "@/components/contact/contact-form";

export const metadata: Metadata = pageMetadata({
  title: "Start a Project",
  description:
    "Tell Racom Technology about your software, web, AI automation or product project. We reply with honest questions and a suggested next step.",
  path: "/contact",
});

const steps = [
  {
    icon: MessageSquare,
    title: "You share the idea",
    text: "A few details about what you want to build or improve.",
  },
  {
    icon: Clock,
    title: "We review it carefully",
    text: "We read every inquiry and reply with questions or a next step.",
  },
  {
    icon: ShieldCheck,
    title: "No obligation",
    text: "An initial conversation costs nothing and commits you to nothing.",
  },
];

export default async function ContactPage({ searchParams }: PageProps<"/contact">) {
  const params = await searchParams;
  const service = typeof params.service === "string" ? params.service : undefined;
  const productSlug = typeof params.product === "string" ? params.product : undefined;
  const product = products.find((p) => p.slug === productSlug);
  const defaultMessage = product ? `I'm interested in the ${product.name} product idea. ` : undefined;

  return (
    <>
      <PageHero
        eyebrow="Start a project"
        title="Tell us what you want to build."
        description="Share a few details about your project. The more context you give, the more useful our first reply will be."
      />
      <div className="container-page grid gap-14 py-14 sm:py-20 lg:grid-cols-12">
        <div className="relative lg:col-span-7">
          <h2 className="sr-only">Project inquiry form</h2>
          <ContactForm
            defaultService={service}
            defaultMessage={defaultMessage}
            contactEmail={site.contact.email}
          />
        </div>
        <aside className="lg:col-span-5" aria-label="What happens next">
          <div className="surface rounded-2xl p-7 sm:p-8 lg:sticky lg:top-28">
            <h2 className="font-display text-xl font-semibold tracking-tight text-ink">What happens next</h2>
            <ol className="mt-6 space-y-6">
              {steps.map((s) => (
                <li key={s.title} className="flex gap-4">
                  <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-line-strong bg-elevated text-violet-soft">
                    <s.icon aria-hidden="true" className="h-4.5 w-4.5" />
                  </span>
                  <div>
                    <p className="font-medium text-ink">{s.title}</p>
                    <p className="mt-0.5 text-sm leading-relaxed text-muted">{s.text}</p>
                  </div>
                </li>
              ))}
            </ol>
            {(site.contact.email || site.contact.phone || site.contact.location) && (
              <div className="mt-8 space-y-2 border-t border-line pt-6 text-sm text-muted">
                <p className="font-medium text-ink">Prefer another channel?</p>
                {site.contact.email && (
                  <p className="flex items-center gap-2">
                    <Mail aria-hidden="true" className="h-4 w-4 text-violet-soft" />
                    <a className="hover:text-ink" href={`mailto:${site.contact.email}`}>
                      {site.contact.email}
                    </a>
                  </p>
                )}
                {site.contact.phone && <p>{site.contact.phone}</p>}
                {site.contact.location && <p>{site.contact.location}</p>}
              </div>
            )}
            <p className="mt-8 border-t border-line pt-6 text-xs leading-relaxed text-subtle">
              We only use the details you send to reply to your inquiry. See our{" "}
              <Link href="/privacy" className="text-violet-soft underline underline-offset-2 hover:text-ink">
                Privacy Policy
              </Link>
              .
            </p>
          </div>
        </aside>
      </div>
    </>
  );
}
