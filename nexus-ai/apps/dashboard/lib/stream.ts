"use client";

import { useEffect, useState } from "react";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Tick = { type: "tick"; at: string; books: any[]; kill_switch: boolean; bot_state: string };

/** Owner-only live stream (WebSocket). Reconnects with backoff; `connected` is false while
 * the stream is down so pages can fall back to polling. */
export function useStream(): { tick: Tick | null; connected: boolean } {
  const [tick, setTick] = useState<Tick | null>(null);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    let ws: WebSocket | null = null;
    let delay = 1000;
    let stopped = false;
    let timer: number | undefined;
    const open = () => {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${window.location.host}/api/v1/ws/stream`);
      ws.onopen = () => { setConnected(true); delay = 1000; };
      ws.onmessage = (e) => { try { setTick(JSON.parse(e.data)); } catch { /* ignore bad frame */ } };
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) { timer = window.setTimeout(open, delay); delay = Math.min(delay * 2, 30000); }
      };
    };
    open();
    return () => { stopped = true; window.clearTimeout(timer); ws?.close(); };
  }, []);
  return { tick, connected };
}
