import { ImageResponse } from "next/og";
import { site } from "@/config/site";

export const alt = `${site.name} — ${site.slogan}`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const mark = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" fill="none"><defs><linearGradient id="b" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#C2BBFF"/><stop offset="1" stop-color="#7565FF"/></linearGradient></defs><path d="M10 33V7" stroke="#A89FFF" stroke-width="4.5"/><path d="M15 7h5.5a7.5 7.5 0 0 1 0 15H15" stroke="url(#b)" stroke-width="4.5"/><path d="M22 26l7.5 7.5" stroke="#29D8F2" stroke-width="4.5"/></svg>`;

/** Default social sharing card for every page. */
export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "72px 80px",
          background: "radial-gradient(ellipse 60% 70% at 85% 10%, rgba(117,101,255,0.35), #090B12 70%)",
          color: "#F4F5FA",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <img
            alt=""
            width={72}
            height={72}
            src={`data:image/svg+xml;base64,${Buffer.from(mark).toString("base64")}`}
          />
          <div style={{ fontSize: 34, letterSpacing: 10, fontWeight: 600 }}>RACOM</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ fontSize: 76, fontWeight: 700, letterSpacing: -2, lineHeight: 1.05 }}>
            We engineer the digital future.
          </div>
          <div style={{ marginTop: 28, fontSize: 30, color: "#A5A9BA" }}>
            Software engineering · AI automation · Digital products
          </div>
        </div>
        <div style={{ display: "flex", fontSize: 26, color: "#A89FFF" }}>{site.slogan}</div>
      </div>
    ),
    size,
  );
}
