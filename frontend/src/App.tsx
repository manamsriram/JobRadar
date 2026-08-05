import { useEffect, useMemo, useState } from "react";
import { useSSE, type Job } from "./hooks/useSSE";
import JobTable from "./components/JobTable";
import FilterBar from "./components/FilterBar";
import LiveBadge from "./components/LiveBadge";
import SourceHealth from "./components/SourceHealth";
import ResumePanel from "./components/ResumePanel";
import PipelineBoard from "./components/PipelineBoard";

type Tab = "active" | "applied" | "pipeline";

// Same-origin in prod (static build); Vite proxies /api to :8000 in dev.
export default function App() {
  const [tab, setTab] = useState<Tab>("active");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [applied, setApplied] = useState<Job[]>([]);
  const [filters, setFilters] = useState({ title: "", location: "", source: "" });
  const liveJobs = useSSE("/api/stream");

  useEffect(() => {
    fetch("/api/jobs")
      .then((r) => r.json())
      .then(setJobs)
      .catch(() => setJobs([]));
  }, []);

  const loadApplied = () =>
    fetch("/api/jobs/applied")
      .then((r) => r.json())
      .then(setApplied)
      .catch(() => setApplied([]));

  useEffect(() => {
    if (tab === "applied") loadApplied();
  }, [tab]);

  // A job just marked applied on the active tab should drop out of the
  // active feed immediately rather than waiting for the next /api/jobs fetch.
  function handleApplied(id: string) {
    setJobs((prev) => prev.filter((j) => j.id !== id));
  }

  async function handleDelete(id: string) {
    setApplied((prev) => prev.filter((j) => j.id !== id));
    await fetch(`/api/jobs/${id}`, { method: "DELETE" }).catch(() => {});
  }

  // Merge live SSE jobs with the fetched active list, newest first, deduped by id.
  const merged = useMemo(() => {
    const byId = new Map<string, Job>();
    for (const j of [...liveJobs, ...jobs]) {
      if (!byId.has(j.id)) byId.set(j.id, j);
    }
    return [...byId.values()];
  }, [liveJobs, jobs]);

  const source = tab === "active" ? merged : applied;
  const filtered = source.filter((j) => {
    const t = j.title.toLowerCase().includes(filters.title.toLowerCase());
    const l = j.location.toLowerCase().includes(filters.location.toLowerCase());
    const s = !filters.source || j.source === filters.source;
    return t && l && s;
  });

  return (
    <div className="min-h-screen bg-cream font-sans text-gray-900">
      <div className="max-w-5xl mx-auto p-6">
        <div className="flex items-center justify-between border-b border-black pb-4 mb-6">
          <div>
            <p className="font-mono text-xs tracking-widest text-gray-500 uppercase mb-1">
              Self-hosted job radar
            </p>
            <h1 className="font-display text-2xl font-bold">JobRadar</h1>
          </div>
          <div className="flex items-center gap-3">
            <SourceHealth />
            <LiveBadge count={liveJobs.length} />
          </div>
        </div>
        <ResumePanel />
        <div className="flex gap-2 mb-6">
          {(["active", "applied", "pipeline"] as Tab[]).map((t) => (
            <button
              key={t}
              className={`font-mono text-xs uppercase tracking-wider px-3 py-1.5 border ${
                tab === t
                  ? "border-black bg-black text-cream"
                  : "border-gray-300 text-gray-500 hover:border-black hover:text-black"
              }`}
              onClick={() => setTab(t)}
            >
              {t === "active" ? "Jobs" : t === "applied" ? "Applied" : "Pipeline"}
            </button>
          ))}
        </div>
        {tab === "pipeline" ? (
          <PipelineBoard />
        ) : (
          <>
            <FilterBar filters={filters} onChange={setFilters} />
            <JobTable
              jobs={filtered}
              mode={tab}
              onApplied={handleApplied}
              onDelete={handleDelete}
            />
          </>
        )}
      </div>
    </div>
  );
}
