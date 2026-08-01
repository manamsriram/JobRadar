import { useEffect, useState } from "react";

// Polls the same /api/health the backend already tracks (consecutive
// scrape failures per source) — surfaces it somewhere Sriram will
// actually see it, since nothing did before this. Renders nothing when
// everything's healthy, so it never adds visual noise on a normal day.
const POLL_MS = 5 * 60 * 1000; // health changes slowly; no need to hammer it

export default function SourceHealth() {
  const [down, setDown] = useState<string[]>([]);

  useEffect(() => {
    const check = () =>
      fetch("/api/health")
        .then((r) => r.json())
        .then((body) => setDown(body.down ?? []))
        .catch((err) => console.error("SourceHealth: /api/health check failed", err)); // best-effort — a failed health check shouldn't itself alarm the UI, but shouldn't be invisible either
    check();
    const id = setInterval(check, POLL_MS);
    return () => clearInterval(id);
  }, []);

  if (down.length === 0) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-1.5"
    >
      ⚠ {down.length} source{down.length > 1 ? "s" : ""} down: {down.join(", ")}
    </div>
  );
}
