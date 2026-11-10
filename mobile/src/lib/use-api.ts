import { useCallback, useEffect, useState } from "react";
import type { z } from "zod";
import type { Result } from "./api";
import { useSession } from "./session";

/** Load a validated API resource with pull-to-refresh support. */
export function useApi<S extends z.ZodType>(path: string | null, schema: S) {
  const { api } = useSession();
  const [state, setState] = useState<{ loading: boolean; result: Result<z.infer<S>> | null }>({ loading: Boolean(path), result: null });
  const fetchResult = useCallback(async () => (path ? api.call(path, schema) : null), [api, path, schema]);
  useEffect(() => {
    let active = true;
    void fetchResult().then((result) => {
      if (active && result) setState({ loading: false, result });
    });
    return () => {
      active = false; // ignore responses for a screen that has gone away
    };
  }, [fetchResult]);
  const reload = useCallback(async () => {
    setState((s) => ({ ...s, loading: true }));
    const result = await fetchResult();
    if (result) setState({ loading: false, result });
  }, [fetchResult]);
  return { ...state, reload };
}
