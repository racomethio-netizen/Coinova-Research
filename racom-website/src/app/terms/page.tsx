import type { Metadata } from "next";
import { site } from "@/config/site";
import { pageMetadata } from "@/lib/seo";
import { EntityName, LegalField, LegalPage } from "@/components/content/legal-page";

export const metadata: Metadata = pageMetadata({
  title: "Terms of Use",
  description: `Terms governing the use of the ${site.name} website.`,
  path: "/terms",
});

export default function TermsPage() {
  return (
    <LegalPage title="Terms of Use" intro="The terms that apply when you use this website.">
      <h2>About these terms</h2>
      <p>
        This website is operated by <EntityName />, trading as {site.name}. By using the website you agree to
        these terms. If you do not agree, please do not use the website.
      </p>

      <h2>Use of the website</h2>
      <ul>
        <li>You may browse and share the website’s content for personal and internal business purposes.</li>
        <li>
          You must not misuse the website — for example by attempting to gain unauthorised access, interfering
          with its operation or submitting spam through the inquiry form.
        </li>
      </ul>

      <h2>Information on this website</h2>
      <p>
        Content is provided for general information. Projects marked as concepts are self-initiated
        explorations and not client engagements; products marked as examples or concepts are not available for
        purchase. Nothing on this website is a binding offer. Any engagement is governed by a separate written
        agreement.
      </p>

      <h2>Intellectual property</h2>
      <p>
        The {site.name} name, logo, design and content of this website belong to <EntityName /> unless stated
        otherwise. You may not use them without permission.
      </p>

      <h2>Links to other websites</h2>
      <p>We are not responsible for the content or practices of websites we link to.</p>

      <h2>Liability</h2>
      <p>
        To the extent permitted by law, we are not liable for losses arising from use of this website.{" "}
        <LegalField value={null} label="Liability wording to be confirmed by legal counsel" />
      </p>

      <h2>Governing law</h2>
      <p>
        These terms are governed by the laws of{" "}
        <LegalField value={site.legal.governingLaw} label="Governing law / jurisdiction" />.
      </p>

      <h2>Contact</h2>
      <p>
        Questions about these terms: <LegalField value={site.contact.email} label="Contact email" />.
      </p>
    </LegalPage>
  );
}
