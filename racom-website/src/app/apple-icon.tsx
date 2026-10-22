import { ImageResponse } from "next/og";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

const mark = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" fill="none"><defs><linearGradient id="b" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#C2BBFF"/><stop offset="1" stop-color="#7565FF"/></linearGradient></defs><path d="M10 33V7" stroke="#A89FFF" stroke-width="4.5"/><path d="M15 7h5.5a7.5 7.5 0 0 1 0 15H15" stroke="url(#b)" stroke-width="4.5"/><path d="M22 26l7.5 7.5" stroke="#29D8F2" stroke-width="4.5"/></svg>`;

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#090B12",
        }}
      >
        <img
          alt=""
          width={124}
          height={124}
          src={`data:image/svg+xml;base64,${Buffer.from(mark).toString("base64")}`}
        />
      </div>
    ),
    size,
  );
}
