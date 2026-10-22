import type { ProjectArtVariant } from "@/content/projects";
import type { ProductArtVariant } from "@/content/products";
import { cn } from "@/lib/cn";

/**
 * Original abstract illustrations for concept projects and products.
 * Lightweight SVG compositions in the brand palette — they suggest the kind of
 * interface each concept explores without pretending to be real screenshots.
 */

const V = "#7565FF";
const VS = "#A89FFF";
const C = "#29D8F2";
const LINE = "rgba(255,255,255,0.08)";
const PANEL = "#141827";
const PANEL2 = "#1C2133";

function Frame({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <svg viewBox="0 0 480 300" fill="none" className="h-full w-full" role="img" aria-label={label}>
      <defs>
        <pattern id={`grid-${label.length}`} width="24" height="24" patternUnits="userSpaceOnUse">
          <path d="M24 0H0V24" stroke="rgba(255,255,255,0.035)" />
        </pattern>
        <radialGradient id={`light-${label.length}`} cx="75%" cy="0%" r="90%">
          <stop offset="0" stopColor={V} stopOpacity="0.28" />
          <stop offset="1" stopColor={V} stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="480" height="300" fill="#0E111B" />
      <rect width="480" height="300" fill={`url(#grid-${label.length})`} />
      <rect width="480" height="300" fill={`url(#light-${label.length})`} />
      {children}
    </svg>
  );
}

/** A floating application window. */
function Window({
  x,
  y,
  w,
  h,
  children,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  children?: React.ReactNode;
}) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect width={w} height={h} rx="10" fill={PANEL} stroke="rgba(255,255,255,0.1)" />
      <g fill="rgba(255,255,255,0.14)">
        <circle cx="14" cy="13" r="2.5" />
        <circle cx="23" cy="13" r="2.5" />
        <circle cx="32" cy="13" r="2.5" />
      </g>
      <path d={`M0 26H${w}`} stroke={LINE} />
      {children}
    </g>
  );
}

function Dashboard() {
  return (
    <Frame label="Illustration: invoicing dashboard concept">
      <Window x={56} y={42} w={368} h={230}>
        <rect x="16" y="40" width="70" height="8" rx="4" fill="rgba(255,255,255,0.18)" />
        <rect x="16" y="56" width="110" height="18" rx="4" fill="rgba(255,255,255,0.75)" />
        <g transform="translate(16 92)">
          {[0, 1, 2].map((i) => (
            <g key={i} transform={`translate(${i * 112} 0)`}>
              <rect width="100" height="46" rx="8" fill={PANEL2} stroke={LINE} />
              <rect x="10" y="10" width="40" height="5" rx="2.5" fill="rgba(255,255,255,0.2)" />
              <rect
                x="10"
                y="24"
                width={[64, 52, 70][i]}
                height="10"
                rx="3"
                fill={i === 1 ? C : VS}
                opacity="0.9"
              />
            </g>
          ))}
        </g>
        <path
          d="M16 214 C60 200 80 170 120 176 S190 204 228 170 S300 150 352 156"
          stroke={V}
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <path
          d="M16 214 C60 200 80 170 120 176 S190 204 228 170 S300 150 352 156 V218 H16Z"
          fill={V}
          opacity="0.12"
        />
        <circle cx="228" cy="170" r="4.5" fill={C} />
      </Window>
    </Frame>
  );
}

function Dispatch() {
  return (
    <Frame label="Illustration: delivery dispatch concept">
      <g stroke="rgba(255,255,255,0.12)" strokeWidth="1.5">
        <path d="M40 230 C120 210 140 120 230 130 S340 70 440 80" strokeDasharray="4 6" />
        <path d="M60 80 C130 100 170 200 260 200 S380 240 440 220" strokeDasharray="4 6" />
      </g>
      <path d="M40 230 C120 210 140 120 230 130" stroke={C} strokeWidth="2.5" strokeLinecap="round" />
      {[
        [40, 230, C],
        [230, 130, VS],
        [440, 80, VS],
        [60, 80, VS],
        [260, 200, V],
        [440, 220, V],
      ].map(([x, y, c], i) => (
        <g key={i}>
          <circle cx={x as number} cy={y as number} r="11" fill={c as string} opacity="0.15" />
          <circle cx={x as number} cy={y as number} r="4.5" fill={c as string} />
        </g>
      ))}
      <Window x={292} y={150} w={160} h={122}>
        {[0, 1, 2].map((i) => (
          <g key={i} transform={`translate(12 ${38 + i * 26})`}>
            <rect width="136" height="20" rx="5" fill={PANEL2} />
            <circle cx="11" cy="10" r="3.5" fill={[C, "#F5C76B", VS][i]} />
            <rect x="22" y="7" width={[70, 54, 82][i]} height="6" rx="3" fill="rgba(255,255,255,0.3)" />
          </g>
        ))}
      </Window>
      <g transform="translate(36 28)">
        <rect width="104" height="150" rx="16" fill={PANEL} stroke="rgba(255,255,255,0.12)" />
        <rect x="12" y="18" width="80" height="34" rx="8" fill={C} opacity="0.85" />
        <rect x="12" y="62" width="80" height="22" rx="6" fill={PANEL2} />
        <rect x="12" y="92" width="80" height="22" rx="6" fill={PANEL2} />
        <rect x="36" y="130" width="32" height="4" rx="2" fill="rgba(255,255,255,0.25)" />
      </g>
    </Frame>
  );
}

function Triage() {
  return (
    <Frame label="Illustration: AI support triage concept">
      <g transform="translate(40 52)">
        {[0, 1, 2, 3].map((i) => (
          <g key={i} transform={`translate(0 ${i * 50})`}>
            <rect width="150" height="38" rx="8" fill={PANEL} stroke={LINE} />
            <rect
              x="12"
              y="11"
              width={[90, 70, 110, 80][i]}
              height="6"
              rx="3"
              fill="rgba(255,255,255,0.35)"
            />
            <rect
              x="12"
              y="23"
              width={[60, 100, 50, 84][i]}
              height="4"
              rx="2"
              fill="rgba(255,255,255,0.14)"
            />
            {/* Every request routes into the classifier hub at (240, 48) in this group's coordinates. */}
            <path
              d={`M150 19 C190 19 200 ${48 - i * 50} 240 ${48 - i * 50}`}
              stroke={i === 1 ? C : "rgba(168,159,255,0.45)"}
              strokeWidth="1.5"
            />
          </g>
        ))}
      </g>
      <g transform="translate(280 78)">
        <circle cx="0" cy="22" r="22" fill={V} opacity="0.15" />
        <circle cx="0" cy="22" r="9" fill={VS} />
      </g>
      <Window x={300} y={60} w={148} h={190}>
        <rect x="14" y="40" width="54" height="16" rx="8" fill={C} opacity="0.2" />
        <rect x="20" y="45.5" width="42" height="5" rx="2.5" fill={C} />
        <rect x="14" y="68" width="120" height="6" rx="3" fill="rgba(255,255,255,0.35)" />
        <rect x="14" y="82" width="104" height="5" rx="2.5" fill="rgba(255,255,255,0.15)" />
        <rect x="14" y="94" width="112" height="5" rx="2.5" fill="rgba(255,255,255,0.15)" />
        <rect x="14" y="106" width="80" height="5" rx="2.5" fill="rgba(255,255,255,0.15)" />
        <rect x="14" y="150" width="56" height="22" rx="11" fill={V} />
        <rect x="78" y="150" width="56" height="22" rx="11" stroke="rgba(255,255,255,0.2)" />
      </Window>
    </Frame>
  );
}

function Network() {
  const nodes: [number, number, string][] = [
    [240, 150, VS],
    [100, 80, C],
    [90, 220, VS],
    [380, 76, VS],
    [392, 222, C],
  ];
  return (
    <Frame label="Illustration: systems integration hub concept">
      <circle cx="240" cy="150" r="96" stroke="rgba(255,255,255,0.06)" />
      <circle cx="240" cy="150" r="60" stroke="rgba(255,255,255,0.08)" strokeDasharray="3 6" />
      {nodes.slice(1).map(([x, y], i) => (
        <path
          key={i}
          d={`M240 150L${x} ${y}`}
          stroke={i % 2 ? "rgba(168,159,255,0.4)" : C}
          strokeWidth="1.5"
          strokeDasharray={i % 2 ? "4 5" : undefined}
        />
      ))}
      {nodes.slice(1).map(([x, y, c], i) => (
        <g key={i} transform={`translate(${x - 34} ${y - 22})`}>
          <rect width="68" height="44" rx="10" fill={PANEL} stroke="rgba(255,255,255,0.12)" />
          <circle cx="16" cy="22" r="5" fill={c} />
          <rect x="27" y="16" width="30" height="5" rx="2.5" fill="rgba(255,255,255,0.35)" />
          <rect x="27" y="25" width="20" height="4" rx="2" fill="rgba(255,255,255,0.15)" />
        </g>
      ))}
      <g transform="translate(240 150)">
        <rect x="-38" y="-38" width="76" height="76" rx="18" fill="#1E2340" stroke={V} strokeOpacity="0.6" />
        <path d="M-12 18V-18M-4 -18h8a9 9 0 0 1 0 18h-8M7 6l12 12" stroke={VS} strokeWidth="5" />
      </g>
    </Frame>
  );
}

const projectArt: Record<ProjectArtVariant, () => React.ReactElement> = {
  dashboard: Dashboard,
  dispatch: Dispatch,
  triage: Triage,
  network: Network,
};

export function ProjectArt({ variant, className }: { variant: ProjectArtVariant; className?: string }) {
  const Art = projectArt[variant];
  return (
    <div className={cn("overflow-hidden", className)}>
      <Art />
    </div>
  );
}

/* ---------- Product illustrations (compact, square-ish) ---------- */

function ProductFrame({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <svg viewBox="0 0 320 200" fill="none" className="h-full w-full" role="img" aria-label={label}>
      <rect width="320" height="200" fill="#0E111B" />
      <circle cx="260" cy="0" r="180" fill={V} opacity="0.08" />
      {children}
    </svg>
  );
}

function Flow() {
  return (
    <ProductFrame label="Illustration: workflow automation example product">
      {[40, 130, 220].map((x, i) => (
        <g key={x}>
          <rect
            x={x}
            y="78"
            width="64"
            height="44"
            rx="10"
            fill={PANEL}
            stroke={i === 1 ? C : "rgba(255,255,255,0.14)"}
          />
          <rect x={x + 12} y="94" width="40" height="5" rx="2.5" fill="rgba(255,255,255,0.4)" />
          <rect x={x + 12} y="104" width="26" height="4" rx="2" fill="rgba(255,255,255,0.15)" />
        </g>
      ))}
      <path d="M104 100H130M194 100H220" stroke={VS} strokeWidth="1.5" />
      <path d="M126 96l4 4-4 4M216 96l4 4-4 4" stroke={VS} strokeWidth="1.5" />
      <circle cx="162" cy="78" r="5" fill={C} />
    </ProductFrame>
  );
}

function Pulse() {
  return (
    <ProductFrame label="Illustration: business metrics example product">
      <path
        d="M24 130H86L104 84L126 160L150 60L170 130H296"
        stroke={C}
        strokeWidth="2.5"
        strokeLinejoin="round"
      />
      <path d="M24 160H296" stroke={LINE} />
      <circle cx="150" cy="60" r="5" fill={VS} />
      <rect x="196" y="40" width="84" height="40" rx="8" fill={PANEL} stroke="rgba(255,255,255,0.12)" />
      <rect x="208" y="52" width="40" height="5" rx="2.5" fill="rgba(255,255,255,0.25)" />
      <rect x="208" y="63" width="58" height="8" rx="3" fill={VS} />
    </ProductFrame>
  );
}

function Desk() {
  return (
    <ProductFrame label="Illustration: AI-assisted helpdesk example product">
      {[0, 1, 2].map((i) => (
        <g key={i} transform={`translate(32 ${40 + i * 42})`}>
          <rect width="150" height="32" rx="8" fill={PANEL} stroke={i === 0 ? V : LINE} />
          <circle cx="16" cy="16" r="5" fill={[C, VS, "rgba(255,255,255,0.3)"][i]} />
          <rect x="28" y="13" width={[96, 76, 88][i]} height="6" rx="3" fill="rgba(255,255,255,0.3)" />
        </g>
      ))}
      <rect x="198" y="40" width="94" height="116" rx="10" fill={PANEL} stroke="rgba(255,255,255,0.12)" />
      <rect x="210" y="56" width="60" height="5" rx="2.5" fill="rgba(255,255,255,0.35)" />
      <rect x="210" y="68" width="70" height="4" rx="2" fill="rgba(255,255,255,0.15)" />
      <rect x="210" y="78" width="54" height="4" rx="2" fill="rgba(255,255,255,0.15)" />
      <rect x="210" y="128" width="40" height="16" rx="8" fill={V} />
    </ProductFrame>
  );
}

const productArt: Record<ProductArtVariant, () => React.ReactElement> = {
  flow: Flow,
  pulse: Pulse,
  desk: Desk,
};

export function ProductArt({ variant, className }: { variant: ProductArtVariant; className?: string }) {
  const Art = productArt[variant];
  return (
    <div className={cn("overflow-hidden", className)}>
      <Art />
    </div>
  );
}
