import { useEffect, useState } from "react";
import type { Contact, Job } from "../hooks/useSSE";

interface OutreachPanelProps {
  job: Job;
  contact: Contact;
  onClose: () => void;
  onSent?: () => void;
}

interface LogStatus {
  send_enabled: boolean;
  sent_today: number;
  daily_cap: number;
}

// Backend gate reasons and eligibility bases arrive as fixed slugs; the UI
// owns the wording. Anything unmapped falls through as the slug itself so a
// new backend reason is visible rather than silently blank.
const GATE_REASON: Record<string, string> = {
  sending_disabled: "Sending is off — set OUTREACH_SEND_ENABLED=true on the backend.",
  daily_cap_reached: "Daily send cap reached. Try again tomorrow.",
  duplicate: "You already reached out to this address.",
  unverified_address: "Address is not verified. Verify it, or tick override.",
};

const BASIS_LABEL: Record<string, string> = {
  verified_address: "verified address",
  stored_verified_address: "verified earlier",
  auto_promoted_pattern: "company pattern (high confidence, not confirmed)",
  needs_verification: "unverified guess",
  no_guess: "no usable address",
};

const BLOCKER_LABEL: Record<string, string> = {
  pattern_not_verified: "company's email pattern isn't confirmed yet",
  pattern_failed_since_last_success: "this pattern failed here recently",
  name_unparseable: "name may not split into first/last correctly",
  unparseable_name: "name may not split into first/last correctly",
};

const label = (map: Record<string, string>, key?: string | null) =>
  (key && (map[key] ?? key)) || "";

/**
 * Compose and send one recruiter email. Every hard rule (kill switch, daily
 * cap, dedup, verification) is enforced by the backend — this panel only
 * shows which gate is closed, so a disabled Send always says why.
 */
export default function OutreachPanel({ job, contact, onClose, onSent }: OutreachPanelProps) {
  const guess = contact.guesses?.[0];
  const [email, setEmail] = useState(contact.email ?? guess?.email ?? "");
  const [subject, setSubject] = useState(`${job.title} application — ${contact.name}`);
  const [body, setBody] = useState("");
  const [override, setOverride] = useState(false);
  const [busy, setBusy] = useState<"verify" | "send" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [eligibility, setEligibility] = useState(contact.eligibility);
  const [status, setStatus] = useState<LogStatus | null>(null);

  useEffect(() => {
    fetch("/api/outreach/log")
      .then((r) => r.json())
      .then((d) => setStatus({ send_enabled: d.send_enabled, sent_today: d.sent_today, daily_cap: d.daily_cap }))
      .catch(() => {});
  }, []);

  async function verify() {
    setBusy("verify");
    setError("");
    setNotice("");
    try {
      const url = `/api/jobs/${job.id}/outreach/verify?source_url=${encodeURIComponent(contact.source_url ?? "")}`;
      const res = await fetch(url, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "verification failed");
      setEmail(data.email ?? email);
      setEligibility({
        eligible: data.status === "valid" || data.status === "auto_promoted",
        basis: data.basis,
        blockers: [],
      });
      setNotice(
        data.spent_credit
          ? `Hunter says ${data.status}${data.score != null ? ` (${data.score})` : ""} — 1 credit spent.`
          : `${label(BASIS_LABEL, data.basis)} — no credit spent.`
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "verification failed");
    } finally {
      setBusy(null);
    }
  }

  async function send() {
    setBusy("send");
    setError("");
    try {
      const res = await fetch(`/api/jobs/${job.id}/outreach/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, subject, body, source_url: contact.source_url, override }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data.detail;
        if (detail && typeof detail === "object" && detail.reason) {
          const prior = detail.prior?.sent_at ? ` (sent ${new Date(detail.prior.sent_at).toLocaleDateString()})` : "";
          throw new Error((GATE_REASON[detail.reason] ?? detail.reason) + prior);
        }
        throw new Error(typeof detail === "string" ? detail : "send failed");
      }
      setNotice("Sent.");
      onSent?.();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "send failed");
    } finally {
      setBusy(null);
    }
  }

  const capReached = status ? status.sent_today >= status.daily_cap : false;
  const needsOverride = eligibility ? !eligibility.eligible && !override : false;
  const blockedReason = !status?.send_enabled
    ? GATE_REASON.sending_disabled
    : capReached
      ? GATE_REASON.daily_cap_reached
      : needsOverride
        ? GATE_REASON.unverified_address
        : !email || !subject.trim() || !body.trim()
          ? "Recipient, subject and body are all required."
          : "";

  return (
    <div className="border border-black bg-cream p-3 mt-3 text-sm">
      <div className="flex items-start justify-between mb-2">
        <div>
          <p className="font-display font-semibold">
            Reach out to {contact.name}
            {contact.title ? ` · ${contact.title}` : ""}
          </p>
          <p className="font-mono text-[10px] uppercase tracking-wide text-gray-500">
            {job.company} · {job.title}
          </p>
        </div>
        <button
          className="font-mono text-[10px] uppercase tracking-wide text-gray-500 hover:text-black"
          onClick={onClose}
        >
          Close
        </button>
      </div>

      {eligibility && (
        <p className="text-xs mb-2 text-gray-600">
          {eligibility.eligible ? "✓" : "⚠"} {label(BASIS_LABEL, eligibility.basis)}
          {eligibility.blockers.length > 0 &&
            ` — ${eligibility.blockers.map((b) => label(BLOCKER_LABEL, b)).join("; ")}`}
        </p>
      )}

      <div className="flex flex-col gap-2">
        <div className="flex gap-2 items-center">
          <input
            className="flex-1 border border-gray-400 px-2 py-1 font-mono text-xs bg-white"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="recipient@company.com"
          />
          <button
            className="px-2 py-1 border border-gray-400 font-mono text-[10px] uppercase tracking-wide text-gray-600 hover:border-black hover:text-black disabled:opacity-50"
            disabled={busy !== null}
            onClick={verify}
            title="Confirms the address — may spend one Hunter credit"
          >
            {busy === "verify" ? "Verifying…" : "Verify"}
          </button>
        </div>
        <input
          className="border border-gray-400 px-2 py-1 bg-white"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder="Subject"
        />
        <textarea
          className="border border-gray-400 px-2 py-1 h-40 bg-white font-sans"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Plain text only. Say which role, when you applied, one concrete reason you fit, and a small ask."
        />
      </div>

      {eligibility && !eligibility.eligible && (
        <label className="flex items-center gap-2 mt-2 text-xs text-amber-700">
          <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />
          Send anyway without a verified address (recorded in the log)
        </label>
      )}

      <div className="flex items-center gap-3 mt-3">
        <button
          className="px-3 py-1 bg-black text-cream font-mono text-[10px] uppercase tracking-wide hover:bg-gray-800 disabled:opacity-40"
          disabled={busy !== null || blockedReason !== ""}
          onClick={send}
          title={blockedReason}
        >
          {busy === "send" ? "Sending…" : "Send"}
        </button>
        {blockedReason && <span className="text-xs text-gray-500">{blockedReason}</span>}
        {status && !blockedReason && (
          <span className="text-xs text-gray-400">
            {status.sent_today}/{status.daily_cap} sent today
          </span>
        )}
      </div>

      {notice && <p className="text-xs text-green-700 mt-2">{notice}</p>}
      {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
    </div>
  );
}
