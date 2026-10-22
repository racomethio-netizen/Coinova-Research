/**
 * Signature homepage visual: the Racom R rendered at scale as an engineered
 * object — an extruded blueprint layer for depth, measurement rings, connected
 * nodes and a forward-moving cyan leg. Pure SVG + CSS animation (no JS), and
 * all motion is disabled under prefers-reduced-motion by the global rule.
 */

// The mark's geometry (40-unit grid) scaled ×9 and offset into a 560 canvas.
const S = 9;
const O = { x: 100, y: 90 };
const p = (x: number, y: number) => `${O.x + x * S} ${O.y + y * S}`;
const stem = `M${p(10, 7)}V${O.y + 33 * S}`;
const bowl = `M${p(15, 7)}H${O.x + 20.5 * S}A${7.5 * S} ${7.5 * S} 0 0 1 ${O.x + 20.5 * S} ${O.y + 22 * S}H${O.x + 15 * S}`;
const leg = `M${p(22, 26)}L${p(29.5, 33.5)}`;
const W = 4.5 * S;
const DEPTH = 16;

const ticks = Array.from({ length: 60 }, (_, i) => {
  const a = (i / 60) * Math.PI * 2;
  const long = i % 5 === 0;
  const r1 = 252;
  const r2 = long ? 240 : 246;
  const f = (n: number) => n.toFixed(2);
  return {
    x1: f(280 + r1 * Math.cos(a)),
    y1: f(280 + r1 * Math.sin(a)),
    x2: f(280 + r2 * Math.cos(a)),
    y2: f(280 + r2 * Math.sin(a)),
    long,
  };
});

const draw = "[--draw-length:1] [stroke-dasharray:1] animate-draw";

export function HeroVisual({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 560 560" fill="none" className={className} aria-hidden="true" focusable="false">
      <defs>
        <radialGradient id="hv-glow" cx="50%" cy="48%" r="50%">
          <stop offset="0" stopColor="#7565FF" stopOpacity="0.32" />
          <stop offset="0.55" stopColor="#7565FF" stopOpacity="0.06" />
          <stop offset="1" stopColor="#7565FF" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="hv-bowl" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#D4CEFF" />
          <stop offset="0.5" stopColor="#8C7EFF" />
          <stop offset="1" stopColor="#5A4AE6" />
        </linearGradient>
        {/* userSpaceOnUse: a bounding-box gradient on a zero-width vertical line renders nothing. */}
        <linearGradient
          id="hv-stem"
          gradientUnits="userSpaceOnUse"
          x1="0"
          y1={O.y + 7 * S}
          x2="0"
          y2={O.y + 33 * S}
        >
          <stop offset="0" stopColor="#C2BBFF" />
          <stop offset="1" stopColor="#6B5BF7" />
        </linearGradient>
        <linearGradient id="hv-leg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#7AE9FA" />
          <stop offset="1" stopColor="#29D8F2" />
        </linearGradient>
        <linearGradient id="hv-trail" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#29D8F2" stopOpacity="0.9" />
          <stop offset="1" stopColor="#29D8F2" stopOpacity="0" />
        </linearGradient>
        <filter id="hv-blur" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="18" />
        </filter>
      </defs>

      {/* Ambient light */}
      <circle cx="280" cy="270" r="280" fill="url(#hv-glow)" />

      {/* Measurement rings */}
      <g stroke="#ffffff" strokeOpacity="0.07">
        <circle cx="280" cy="280" r="252" />
        <circle cx="280" cy="280" r="196" strokeDasharray="2 6" />
        <circle cx="280" cy="280" r="132" strokeOpacity="0.05" />
      </g>
      <g>
        {ticks.map((t, i) => (
          <line
            key={i}
            x1={t.x1}
            y1={t.y1}
            x2={t.x2}
            y2={t.y2}
            stroke="#ffffff"
            strokeOpacity={t.long ? 0.22 : 0.08}
          />
        ))}
      </g>

      {/* Slowly rotating orbit marker */}
      <g className="origin-[280px_280px] motion-safe:animate-[spin_48s_linear_infinite]">
        <circle cx="280" cy="28" r="3" fill="#29D8F2" />
        <path d="M280 28A252 252 0 0 1 432 79" stroke="url(#hv-trail)" strokeWidth="1.5" />
      </g>

      {/* Blueprint extrusion: the R offset back in depth, joined by hairlines */}
      <g stroke="#A89FFF" strokeOpacity="0.28" strokeWidth="1" transform={`translate(${DEPTH} ${DEPTH})`}>
        <path d={stem} strokeWidth={W} strokeOpacity="0.07" />
        <path d={bowl} strokeWidth={W} strokeOpacity="0.07" />
        <path d={leg} strokeWidth={W} strokeOpacity="0.07" />
      </g>
      <g stroke="#A89FFF" strokeOpacity="0.35" strokeWidth="1">
        {[
          [O.x + 10 * S + W / 2, O.y + 7 * S],
          [O.x + 10 * S + W / 2, O.y + 33 * S],
          [O.x + 28 * S + W / 2, O.y + 14.5 * S],
          [O.x + 29.5 * S + W / 2 - 4, O.y + 33.5 * S - 14],
        ].map(([x, y], i) => (
          <line key={i} x1={x} y1={y} x2={(x ?? 0) + DEPTH} y2={(y ?? 0) + DEPTH} />
        ))}
      </g>

      {/* Glow layer */}
      <g filter="url(#hv-blur)" opacity="0.55">
        <path d={bowl} stroke="#7565FF" strokeWidth={W} />
        <path d={leg} stroke="#29D8F2" strokeWidth={W} />
      </g>

      {/* The R */}
      <path d={stem} stroke="url(#hv-stem)" strokeWidth={W} />
      <path d={bowl} stroke="url(#hv-bowl)" strokeWidth={W} />
      <path d={leg} stroke="url(#hv-leg)" strokeWidth={W} />

      {/* Specular centre lines, drawn on load */}
      <g stroke="#ffffff" strokeOpacity="0.55" strokeWidth="1.2">
        <path d={stem} pathLength={1} className={draw} />
        <path d={bowl} pathLength={1} className={draw} style={{ animationDelay: "0.25s" }} />
        <path d={leg} pathLength={1} className={draw} style={{ animationDelay: "0.5s" }} />
      </g>

      {/* Forward vector from the leg toward the edge */}
      <path
        d={`M${p(29.5, 33.5)}L${p(36.5, 40.5)}`}
        stroke="url(#hv-trail)"
        strokeWidth="1.5"
        strokeDasharray="3 5"
      />

      {/* Connected nodes */}
      <g stroke="#ffffff" strokeOpacity="0.16">
        <path d={`M${p(10, 7)}L${80} ${118}`} />
        <path d={`M${p(15, 22)}L${74} ${O.y + 22 * S}`} />
        <path d={`M${p(28, 14.5)}L${478} ${142}`} />
      </g>
      {[
        { x: 80, y: 118, c: "#A89FFF" },
        { x: 74, y: O.y + 22 * S, c: "#29D8F2" },
        { x: 478, y: 142, c: "#A89FFF" },
        { x: O.x + 36.5 * S, y: O.y + 40.5 * S, c: "#29D8F2" },
      ].map((n, i) => (
        <g key={i}>
          <circle cx={n.x} cy={n.y} r="9" fill={n.c} opacity="0.12" />
          <circle
            cx={n.x}
            cy={n.y}
            r="3.5"
            fill={n.c}
            className="animate-pulse-soft"
            style={{ animationDelay: `${i * 0.7}s` }}
          />
        </g>
      ))}
    </svg>
  );
}
