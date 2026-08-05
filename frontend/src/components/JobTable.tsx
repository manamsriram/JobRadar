import { Fragment, useEffect, useState } from "react";
import type { Contact, Job } from "../hooks/useSSE";
import ContactCard from "./ContactCard";

interface ContactResult {
  contacts: Contact[];
  domain_guessed: boolean;
}

const SOURCE_LABELS: Record<string, string> = {
  custom: "Company site",
  levels: "Levels.fyi",
  tldr: "TLDR Jobs",
  funding: "Funding",
};

interface JobTableProps {
  jobs: Job[];
  mode?: "active" | "applied";
  onApplied?: (id: string) => void;
  onDelete?: (id: string) => void;
}

export default function JobTable({ jobs, mode = "active", onApplied, onDelete }: JobTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [applied, setApplied] = useState<Set<string>>(new Set());
  const [contactResults, setContactResults] = useState<Record<string, ContactResult>>({});
  const [contactBusy, setContactBusy] = useState<string | null>(null);
  const [contactError, setContactError] = useState<Record<string, string>>({});

  // Expanding a row shows any contacts already found for that company (free
  // cache read) without needing to click "Find Contacts" first.
  useEffect(() => {
    if (!expanded || contactResults[expanded]) return;
    fetch(`/api/jobs/${expanded}/contacts`)
      .then((r) => r.json())
      .then((data) =>
        setContactResults((prev) => ({
          ...prev,
          [expanded]: { contacts: data.contacts, domain_guessed: data.domain_guessed },
        }))
      )
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  async function findContacts(id: string) {
    setContactBusy(id);
    setContactError((prev) => ({ ...prev, [id]: "" }));
    try {
      const res = await fetch(`/api/jobs/${id}/contacts`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `contact lookup failed: ${res.status}`);
      }
      const data = await res.json();
      setContactResults((prev) => ({
        ...prev,
        [id]: { contacts: data.contacts, domain_guessed: data.domain_guessed },
      }));
      if (!data.new_contact) {
        setContactError((prev) => ({ ...prev, [id]: "no additional contact found" }));
      }
    } catch (e) {
      setContactError((prev) => ({
        ...prev,
        [id]: e instanceof Error ? e.message : "contact lookup failed",
      }));
    } finally {
      setContactBusy(null);
    }
  }

  // Optimistically flag applied, then persist so purge/cron keeps the job.
  // Roll back on failure so the UI doesn't show "applied" for something the
  // server never recorded.
  async function markApplied(id: string) {
    setApplied((prev) => new Set(prev).add(id));
    try {
      const res = await fetch(`/api/jobs/${id}/apply`, { method: "POST" });
      if (!res.ok) throw new Error(`apply failed: ${res.status}`);
      onApplied?.(id);
    } catch {
      setApplied((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr className="text-left border-b-2 border-black">
          <th className="p-2 font-mono text-[10px] tracking-widest text-gray-500 uppercase w-12">
            No.
          </th>
          <th className="p-2 font-mono text-[10px] tracking-widest text-gray-500 uppercase">
            Role
          </th>
          <th className="p-2 font-mono text-[10px] tracking-widest text-gray-500 uppercase">
            Location
          </th>
          <th className="p-2 font-mono text-[10px] tracking-widest text-gray-500 uppercase">
            Source
          </th>
          <th className="p-2 font-mono text-[10px] tracking-widest text-gray-500 uppercase">
            Posted
          </th>
          <th className="p-2 font-mono text-[10px] tracking-widest text-gray-500 uppercase">
            Fit
          </th>
          <th className="p-2 font-mono text-[10px] tracking-widest text-gray-500 uppercase">
            Actions
          </th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job, i) => {
          const isApplied = job.applied || applied.has(job.id);
          return (
            <Fragment key={job.id}>
              <tr
                className="border-b border-gray-300 hover:bg-cream-field transition cursor-pointer align-top"
                onClick={() => setExpanded(expanded === job.id ? null : job.id)}
              >
                <td className="p-2 font-display text-xl font-bold text-gray-900">
                  #{i + 1}
                </td>
                <td className="p-2">
                  <h3 className="font-display font-semibold">{job.company}</h3>
                  <p className="text-gray-700">{job.title}</p>
                </td>
                <td className="p-2 text-gray-500">{job.location}</td>
                <td className="p-2">
                  <span className="inline-block px-2 py-0.5 border border-gray-400 font-mono text-[10px] uppercase tracking-wide text-gray-700">
                    {SOURCE_LABELS[job.source ?? ""] ?? job.source ?? "—"}
                  </span>
                </td>
                <td className="p-2 text-gray-400">
                  {job.posted_at
                    ? new Date(job.posted_at).toLocaleDateString()
                    : "—"}
                </td>
                <td className="p-2">
                  {job.ai_score != null && (
                    <span
                      className="inline-block px-2 py-0.5 border border-blue-400 font-mono text-[10px] uppercase tracking-wide text-blue-700"
                      title={job.ai_reason ?? ""}
                    >
                      {job.ai_score}/100 · {job.ai_resume}
                    </span>
                  )}
                </td>
                <td className="p-2">
                  <div className="flex flex-wrap gap-1.5 items-center">
                    <a
                      href={job.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="px-3 py-1 bg-black text-cream font-mono text-[10px] uppercase tracking-wide hover:bg-gray-800"
                      onClick={(e) => e.stopPropagation()}
                    >
                      Apply →
                    </a>
                    <button
                      className="px-2 py-1 border border-gray-400 font-mono text-[10px] uppercase tracking-wide text-gray-600 hover:border-black hover:text-black disabled:opacity-50"
                      disabled={contactBusy === job.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        findContacts(job.id);
                      }}
                    >
                      {contactBusy === job.id
                        ? "Finding…"
                        : (contactResults[job.id]?.contacts.length ?? job.contacts?.length ?? 0) > 0
                          ? "More contacts"
                          : "Contacts"}
                    </button>
                    {mode === "applied" ? (
                      <button
                        className="px-2 py-1 border border-gray-400 font-mono text-[10px] uppercase tracking-wide text-gray-600 hover:border-red-600 hover:text-red-600"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete?.(job.id);
                        }}
                      >
                        Remove
                      </button>
                    ) : isApplied ? (
                      <span className="font-mono text-[10px] uppercase tracking-wide text-green-700">
                        ✓ Applied
                      </span>
                    ) : (
                      <button
                        className="px-2 py-1 border border-gray-400 font-mono text-[10px] uppercase tracking-wide text-gray-600 hover:border-green-600 hover:text-green-700"
                        onClick={(e) => {
                          e.stopPropagation();
                          markApplied(job.id);
                        }}
                      >
                        Mark applied
                      </button>
                    )}
                  </div>
                </td>
              </tr>
              {expanded === job.id && (() => {
                const result = contactResults[job.id];
                const contacts = result ? result.contacts : job.contacts ?? [];
                const error = contactError[job.id];
                if (!job.description && contacts.length === 0 && !error) return null;
                return (
                  <tr>
                    <td colSpan={7} className="bg-cream-field px-4 py-3 border-b border-gray-300">
                      {job.description && (
                        <p className="text-gray-600 whitespace-pre-line mb-3 max-h-60 overflow-y-auto">
                          {job.description}
                        </p>
                      )}
                      {contacts.length > 0 && (
                        <div className="flex flex-col gap-2">
                          {result?.domain_guessed && (
                            <p className="text-amber-600 text-xs">
                              ⚠ Domain guessed for this company — contacts may not be accurate.
                            </p>
                          )}
                          <div className="flex gap-3 flex-wrap items-start">
                            {contacts.map((contact, ci) => (
                              <div key={ci} className="flex flex-col gap-1 items-start">
                                <ContactCard contact={contact} />
                                <a
                                  href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(
                                    `${contact.name} ${job.company}`
                                  )}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-blue-400 hover:underline text-xs"
                                >
                                  Search on LinkedIn →
                                </a>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {error && <p className="text-red-600 text-xs">{error}</p>}
                    </td>
                  </tr>
                );
              })()}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
