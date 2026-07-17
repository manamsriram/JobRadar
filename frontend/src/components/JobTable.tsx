import { Fragment, useState } from "react";
import type { Job } from "../hooks/useSSE";
import ContactCard from "./ContactCard";

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
                  <a
                    href={job.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline"
                    onClick={(e) => e.stopPropagation()}
                  >
                    Apply →
                  </a>
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
              {expanded === job.id &&
                (job.description ||
                  (job.contacts && job.contacts.length > 0)) && (
                  <tr>
                    <td colSpan={7} className="bg-gray-50 px-4 py-2">
                      {job.description && (
                        <p className="text-gray-600 whitespace-pre-line mb-3 max-h-60 overflow-y-auto">
                          {job.description}
                        </p>
                      )}
                      {job.contacts && job.contacts.length > 0 && (
                        <div className="flex gap-3 flex-wrap">
                          {job.contacts.map((c, i) => (
                            <ContactCard key={i} contact={c} />
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
