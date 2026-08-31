import { useEffect, useState } from "react";
import type { OutreachRecord } from "../hooks/useSSE";

const STATUS_STYLE: Record<string, string> = {
  sent: "border-green-600 text-green-700",
  replied: "border-blue-600 text-blue-700",
  failed: "border-red-600 text-red-700",
  draft: "border-gray-400 text-gray-600",
};

/**
 * Audit trail of every outreach email: what went out, to whom, whether the
 * verification gate was overridden, and which ones came back. Reply detection
 * is an IMAP scan, so it only runs when asked.
 */
export default function OutreachLog() {
  const [records, setRecords] = useState<OutreachRecord[]>([]);
  const [meta, setMeta] = useState({ sent_today: 0, daily_cap: 0, send_enabled: false });
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");

  const load = () =>
    fetch("/api/outreach/log")
      .then((r) => r.json())
      .then((d) => {
        setRecords([...(d.records ?? [])].reverse());
        setMeta({ sent_today: d.sent_today, daily_cap: d.daily_cap, send_enabled: d.send_enabled });
      })
      .catch(() => setError("could not load the outreach log"));

  useEffect(() => {
    load();
  }, []);

  async function syncReplies() {
    setSyncing(true);
    setError("");
    try {
      const res = await fetch("/api/outreach/sync-replies", { method: "POST" });
      if (!res.ok) throw new Error(`reply sync failed: ${res.status}`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "reply sync failed");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="font-mono text-[10px] uppercase tracking-widest text-gray-500">
          {meta.sent_today}/{meta.daily_cap} sent today ·{" "}
          {meta.send_enabled ? "sending enabled" : "sending disabled"}
        </p>
        <button
          className="px-2 py-1 border border-gray-400 font-mono text-[10px] uppercase tracking-wide text-gray-600 hover:border-black hover:text-black disabled:opacity-50"
          disabled={syncing}
          onClick={syncReplies}
        >
          {syncing ? "Checking…" : "Check replies"}
        </button>
      </div>

      {error && <p className="text-xs text-red-600 mb-2">{error}</p>}
      {records.length === 0 && !error && (
        <p className="text-sm text-gray-500">Nothing sent yet.</p>
      )}

      <div className="flex flex-col gap-2">
        {records.map((r) => (
          <div key={r.id} className="border-b border-gray-300 pb-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className={`inline-block px-2 py-0.5 border font-mono text-[10px] uppercase tracking-wide ${
                  STATUS_STYLE[r.status] ?? STATUS_STYLE.draft
                }`}
              >
                {r.status}
              </span>
              <span className="font-semibold">{r.contact_name ?? r.email}</span>
              <span className="font-mono text-xs text-gray-500">{r.email}</span>
              {r.override_used && (
                <span className="font-mono text-[10px] uppercase tracking-wide text-amber-700">
                  override
                </span>
              )}
              <span className="ml-auto text-xs text-gray-400">
                {r.sent_at ? new Date(r.sent_at).toLocaleString() : "—"}
              </span>
            </div>
            <p className="text-sm text-gray-700 mt-1">{r.subject}</p>
            {r.error && <p className="text-xs text-red-600">{r.error}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
