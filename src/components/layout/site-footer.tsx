import Link from "next/link";
import { siteConfig } from "@/config/site";
import { Container } from "../ui";

export function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-line py-10 text-sm text-muted">
      <Container className="space-y-6">
        <div className="flex flex-wrap justify-between gap-6">
          <div>
            <p className="font-semibold text-fg">{siteConfig.name}</p>
            <p className="mt-1 max-w-sm">{siteConfig.tagline}</p>
          </div>
          <nav aria-label="Footer">
            <ul className="grid grid-cols-2 gap-x-10 gap-y-2">
              <li><Link className="hover:text-fg" href="/markets">Markets</Link></li>
              <li><Link className="hover:text-fg" href="/research">Research</Link></li>
              <li><Link className="hover:text-fg" href="/pricing">Pricing</Link></li>
              <li><Link className="hover:text-fg" href="/about">Methodology &amp; disclosures</Link></li>
            </ul>
          </nav>
        </div>
        <p className="max-w-4xl text-xs leading-relaxed text-faint">{siteConfig.disclaimer}</p>
        <p className="text-xs text-faint">
          Market data provided by third-party providers and may be delayed or inaccurate. Charts use{" "}
          <a className="underline hover:text-fg" href="https://www.tradingview.com/lightweight-charts/" rel="noopener noreferrer" target="_blank">
            TradingView Lightweight Charts™
          </a>
          . © {new Date().getUTCFullYear()} {siteConfig.name}.
        </p>
      </Container>
    </footer>
  );
}
