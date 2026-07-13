import { Fragment, useState } from "react";
import type { Job } from "../types";
import ContactCard from "./ContactCard";

export default function JobTable({ jobs }: { jobs: Job[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr className="bg-gray-100 text-left">
          <th className="p-2">Title</th>
          <th className="p-2">Company</th>
          <th className="p-2">Location</th>
          <th className="p-2">Posted</th>
          <th className="p-2">Apply</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => (
          <Fragment key={job.id}>
            <tr
              className="border-b hover:bg-yellow-50 transition cursor-pointer"
              onClick={() => setExpanded(expanded === job.id ? null : job.id)}
            >
              <td className="p-2 font-medium">{job.title}</td>
              <td className="p-2">{job.company}</td>
              <td className="p-2 text-gray-500">{job.location}</td>
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
            </tr>
            {expanded === job.id && job.contacts && job.contacts.length > 0 && (
              <tr>
                <td colSpan={5} className="bg-gray-50 px-4 py-2">
                  <div className="flex gap-3 flex-wrap">
                    {job.contacts.map((c, i) => (
                      <ContactCard key={i} contact={c} />
                    ))}
                  </div>
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}
