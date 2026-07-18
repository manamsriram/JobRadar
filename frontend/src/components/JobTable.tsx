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
  yc: "Y Combinator",
  tldr: "TLDR Jobs",
  funding: "Funding",
};

export default function JobTable({ jobs }: { jobs: Job[] }) {
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
        <tr className="bg-gray-100 text-left">
          <th className="p-2">Title</th>
          <th className="p-2">Company</th>
          <th className="p-2">Location</th>
          <th className="p-2">Source</th>
          <th className="p-2">Posted</th>
          <th className="p-2">Fit</th>
          <th className="p-2">Apply</th>
          <th className="p-2">Applied?</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => {
          const isApplied = job.applied || applied.has(job.id);
          return (
            <Fragment key={job.id}>
              <tr
                className="border-b hover:bg-yellow-50 transition cursor-pointer"
                onClick={() => setExpanded(expanded === job.id ? null : job.id)}
              >
                <td className="p-2 font-medium">{job.title}</td>
                <td className="p-2">{job.company}</td>
                <td className="p-2 text-gray-500">{job.location}</td>
                <td className="p-2">
                  <span className="inline-block px-2 py-0.5 rounded-full bg-gray-200 text-gray-700 text-xs">
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
                      className="inline-block px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 text-xs"
                      title={job.ai_reason ?? ""}
                    >
                      {job.ai_score}/100 · {job.ai_resume}
                    </span>
                  )}
                </td>
                <td className="p-2">
                  <div className="flex flex-col gap-1 items-start">
                    <a
                      href={job.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      Apply →
                    </a>
                    <button
                      className="text-gray-500 hover:text-blue-600 hover:underline text-xs disabled:opacity-50"
                      disabled={contactBusy === job.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        findContacts(job.id);
                      }}
                    >
                      {contactBusy === job.id
                        ? "Finding…"
                        : (contactResults[job.id]?.contacts.length ?? job.contacts?.length ?? 0) > 0
                          ? "Find Another Contact"
                          : "Find Contacts"}
                    </button>
                  </div>
                </td>
                <td className="p-2">
                  {isApplied ? (
                    <span className="text-green-600">✓ Applied</span>
                  ) : (
                    <button
                      className="text-gray-500 hover:text-green-600 hover:underline"
                      onClick={(e) => {
                        e.stopPropagation();
                        markApplied(job.id);
                      }}
                    >
                      Mark applied
                    </button>
                  )}
                </td>
              </tr>
              {expanded === job.id && (() => {
                const result = contactResults[job.id];
                const contacts = result ? result.contacts : job.contacts ?? [];
                const error = contactError[job.id];
                if (!job.description && contacts.length === 0 && !error) return null;
                return (
                  <tr>
                    <td colSpan={8} className="bg-gray-50 px-4 py-2">
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
                            {contacts.map((contact, i) => (
                              <div key={i} className="flex flex-col gap-1 items-start">
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
