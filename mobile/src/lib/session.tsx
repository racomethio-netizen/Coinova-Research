import type { Session } from "@supabase/supabase-js";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { AppState } from "react-native";
import { createApi } from "./api";
import { config } from "./config";
import { supabase } from "./supabase";

type Ctx = { session: Session | null; loading: boolean; api: ReturnType<typeof createApi> };
const SessionContext = createContext<Ctx | null>(null);

// Refresh tokens only while the app is in the foreground (Supabase guidance for React Native).
AppState.addEventListener("change", (state) => {
  if (state === "active") void supabase.auth.startAutoRefresh();
  else void supabase.auth.stopAutoRefresh();
});

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);
  const api = useMemo(() => createApi(config.apiUrl, async () => (await supabase.auth.getSession()).data.session?.access_token ?? null), []);
  return <SessionContext.Provider value={{ session, loading, api }}>{children}</SessionContext.Provider>;
}

export function useSession(): Ctx {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession outside SessionProvider");
  return ctx;
}
