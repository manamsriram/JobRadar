import { useEffect, useMemo, useState } from "react";
import { useSSE, type Job } from "./hooks/useSSE";
import JobTable from "./components/JobTable";
import FilterBar from "./components/FilterBar";
import LiveBadge from "./components/LiveBadge";

// Same-origin in prod (static build); Vite proxies /api to :8000 in dev.
export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [filters, setFilters] = useState({ title: "", location: "", source: "" });
  const liveJobs = useSSE("/api/stream");

  useEffect(() => {
    fetch("/api/jobs")
      .then((r) => r.json())
      .then(setJobs)
      .catch(() => setJobs([]));
  }, []);

  // Merge live SSE jobs with the fetched list, newest first, deduped by id.
  const merged = useMemo(() => {
    const byId = new Map<string, Job>();
    for (const j of [...liveJobs, ...jobs]) {
      if (!byId.has(j.id)) byId.set(j.id, j);
    }
    return [...byId.values()];
  }, [liveJobs, jobs]);

  const filtered = merged.filter((j) => {
    const t = j.title.toLowerCase().includes(filters.title.toLowerCase());
    const l = j.location.toLowerCase().includes(filters.location.toLowerCase());
    const s = !filters.source || j.source === filters.source;
    return t && l && s;
  });

  return (
    <div className="max-w-5xl mx-auto p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">JobRadar</h1>
        <LiveBadge count={liveJobs.length} />
      </div>
      <FilterBar filters={filters} onChange={setFilters} />
      <JobTable jobs={filtered} />
    </div>
  );
}
