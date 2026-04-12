"use client";

import { useEffect, useState, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const MAX_WAIT = 60_000;
const RETRY_INTERVAL = 5_000;

interface Props {
  brandName: string;
  accentPart: string;
  onReady: () => void;
}

export default function WakeUp({ brandName, accentPart, onReady }: Props) {
  const [elapsed, setElapsed] = useState(0);
  const [timedOut, setTimedOut] = useState(false);

  const ping = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/health`, { cache: "no-store" });
      if (res.ok) {
        onReady();
        return true;
      }
    } catch {}
    return false;
  }, [onReady]);

  useEffect(() => {
    let cancelled = false;
    let start = Date.now();

    const attempt = async () => {
      if (cancelled) return;
      const ok = await ping();
      if (ok || cancelled) return;

      const now = Date.now();
      setElapsed(now - start);

      if (now - start >= MAX_WAIT) {
        setTimedOut(true);
        return;
      }

      setTimeout(attempt, RETRY_INTERVAL);
    };

    attempt();
    return () => { cancelled = true; };
  }, [ping]);

  const handleRetry = () => {
    setTimedOut(false);
    setElapsed(0);
    const attempt = async () => {
      const ok = await ping();
      if (!ok) setTimedOut(true);
    };
    attempt();
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center px-4">
      <h1 className="text-2xl font-bold tracking-tight text-text-primary mb-6">
        {brandName.replace(accentPart, "")}<span className="text-accent">{accentPart}</span>
      </h1>

      {!timedOut ? (
        <>
          <div className="w-10 h-10 border-3 border-border border-t-accent rounded-full animate-spin mb-6" />
          <p className="text-text-secondary text-sm">Getting things ready for you...</p>
          {elapsed > 15_000 && (
            <p className="text-text-muted text-xs mt-2">Almost there...</p>
          )}
        </>
      ) : (
        <>
          <p className="text-text-secondary text-sm mb-4">
            Taking a little longer than usual — hang tight.
          </p>
          <button
            onClick={handleRetry}
            className="bg-accent hover:bg-accent-hover text-white text-sm px-5 py-2.5 rounded-lg transition-colors"
          >
            Retry
          </button>
        </>
      )}
    </div>
  );
}
