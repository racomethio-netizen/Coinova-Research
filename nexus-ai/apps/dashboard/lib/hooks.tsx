"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

export type Mode = "paper" | "live";

const ModeContext = createContext<{ mode: Mode; setMode: (m: Mode) => void }>({
  mode: "paper",
  setMode: () => undefined,
});

export function ModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<Mode>("paper");
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem("nexus-mode");
      if (saved === "live" || saved === "paper") setModeState(saved);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const setMode = (m: Mode) => {
    setModeState(m);
    try {
      window.localStorage.setItem("nexus-mode", m);
    } catch {
      /* ignore */
    }
  };
  return <ModeContext.Provider value={{ mode, setMode }}>{children}</ModeContext.Provider>;
}

export const useMode = () => useContext(ModeContext);

export function useApi<T>(path: string | null, refreshMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api.get<T>(path));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    setLoading(true);
    void load();
    if (!refreshMs) return;
    const id = window.setInterval(() => void load(), refreshMs);
    return () => window.clearInterval(id);
  }, [load, refreshMs]);
  return { data, error, loading, reload: load };
}
