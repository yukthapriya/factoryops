import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router";

/**
 * Filter/sort/page state kept in the URL so views are linkable and survive reloads.
 * Changing any filter other than `page` resets to page 1.
 */
export function useSearchState<T extends Record<string, string>>(defaults: T) {
  const [params, setParams] = useSearchParams();
  const state = useMemo(() => {
    const out = { ...defaults };
    for (const k of Object.keys(defaults) as (keyof T)[]) {
      const v = params.get(k as string);
      if (v !== null) out[k] = v as T[keyof T];
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const set = useCallback(
    (patch: Partial<T>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v === undefined || v === "" || v === defaults[k]) next.delete(k);
            else next.set(k, String(v));
          }
          if (!("page" in patch)) next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [setParams],
  );

  const reset = useCallback(() => setParams(new URLSearchParams(), { replace: true }), [setParams]);
  return [state, set, reset] as const;
}
