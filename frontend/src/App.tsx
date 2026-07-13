import { useEffect, useMemo, useState } from "react";
import { useSSE } from "./hooks/useSSE";
import JobTable from "./components/JobTable";
import FilterBar from "./components/FilterBar";
import LiveBadge from "./components/LiveBadge";
import type { Job } from "./types";

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [filters, setFilters] = useState({ title: "", location: "" });
  const liveJobs = useSSE("/api/stream");

  // Initial job list on mount.
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
    return t && l;
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
