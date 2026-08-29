"use client";

import { useEffect, useRef } from "react";

type RefreshCallback = () => Promise<void> | void;

export function useVisibleAutoRefresh(refresh: RefreshCallback, intervalMs: number) {
  const refreshRef = useRef(refresh);
  const runningRef = useRef(false);

  useEffect(() => {
    refreshRef.current = refresh;
  }, [refresh]);

  useEffect(() => {
    let active = true;

    const run = async () => {
      if (!active || document.visibilityState === "hidden" || runningRef.current) return;
      runningRef.current = true;
      try {
        await refreshRef.current();
      } catch {
        // Each workspace owns its error state; keep the polling loop alive.
      } finally {
        runningRef.current = false;
      }
    };

    const interval = window.setInterval(() => void run(), intervalMs);
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") void run();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      active = false;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [intervalMs]);
}
