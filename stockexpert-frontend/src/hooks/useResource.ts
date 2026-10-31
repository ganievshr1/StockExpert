import { useEffect, useState, type DependencyList } from "react";

/** Ignore late responses after navigation; keep network state local to a screen. */
export function useResource<T>(
  load: () => Promise<T>,
  dependencies: DependencyList,
  enabled = true,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(enabled);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    setError("");
    if (!enabled) {
      setData(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    load()
      .then((value) => {
        if (alive) setData(value);
      })
      .catch((reason) => {
        if (alive)
          setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // Callers explicitly declare every request dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, revision, enabled]);
  return {
    data,
    error,
    loading,
    refresh: () => setRevision((value) => value + 1),
  };
}

export function useDebouncedValue<T>(value: T, delay = 250) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
