import type { Metadata } from "next";
import Link from "next/link";
import { site } from "@/config/site";
import { pageMetadata } from "@/lib/seo";
import { EntityName, LegalField, LegalPage } from "@/components/content/legal-page";

export const metadata: Metadata = pageMetadata({
  title: "Privacy Policy",
  description: `How ${site.name} handles personal information submitted through this website.`,
  path: "/privacy",
});

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy Policy"
      intro="How we collect, use and protect the personal information you share with us through this website."
    >
      <h2>Who we are</h2>
      <p>
        This website is operated by <EntityName />, trading as {site.name} (“we”, “us”). Registered address:{" "}
        <LegalField value={site.legal.registeredAddress} label="Registered address" />. For privacy questions,
        contact us at <LegalField value={site.contact.email} label="Privacy contact email" />.
      </p>

      <h2>What we collect</h2>
      <p>We collect only what is needed to respond to you:</p>
      <ul>
        <li>
          <strong>Project inquiries.</strong> When you use the{" "}
          <Link href="/contact">project inquiry form</Link>, we receive your name, work email and project
          description, and optionally your company, location, budget range and preferred timeline.
        </li>
        <li>
          <strong>Technical data.</strong> Our hosting provider processes technical information such as IP
          addresses and request logs to deliver the website securely. We use IP addresses temporarily to limit
          abuse of the inquiry form.
        </li>
      </ul>
      <p>
        This website does not use advertising or analytics cookies.{" "}
        <LegalField value={null} label="Update this sentence if analytics are added" />
      </p>

      <h2>How we use it</h2>
      <ul>
        <li>to reply to your inquiry and discuss a potential project;</li>
        <li>to keep a record of our correspondence;</li>
        <li>to protect the website and form against spam and abuse.</li>
      </ul>
      <p>We do not sell your personal information or use it for unrelated marketing.</p>

      <h2>Service providers</h2>
      <p>
        Inquiries are delivered to us by email through a transactional email provider (currently Resend) and
        the website is hosted by <LegalField value={null} label="Hosting provider" />. These providers process
        data on our behalf and may store it in other countries.{" "}
        <LegalField value={null} label="Describe safeguards for international transfers" />
      </p>

      <h2>How long we keep it</h2>
      <p>
        We keep inquiry details for as long as needed to handle the inquiry and any resulting engagement, and
        then for <LegalField value={null} label="Retention period" /> unless a longer period is required by
        law.
      </p>

      <h2>Your rights</h2>
      <p>
        Depending on where you live, you may have the right to access, correct or delete your personal
        information, or to object to its use. To make a request, contact us using the details above. You may
        also have the right to complain to a data protection authority.
      </p>

      <h2>Changes</h2>
      <p>We will update this page if our practices change, and revise the “last updated” date above.</p>
    </LegalPage>
  );
}
