import { useEffect, useState } from "react";

export interface Contact {
  name: string;
  title?: string;
  email?: string;
  linkedin?: string;
}

export interface Job {
  id: string;
  title: string;
  company: string;
  location: string;
  url: string;
  source?: string;
  posted_at?: string | null;
  description?: string;
  matched?: boolean;
  applied?: boolean;
  contacts?: Contact[];
  ai_score?: number;
  ai_resume?: "backend" | "frontend";
  ai_reason?: string;
}

/**
 * Subscribes to the SSE job feed. Accumulates live jobs deduped by id
 * (senior-review fix #1: no duplicate re-prepend), newest first.
 */
export function useSSE(url: string): Job[] {
  const [liveJobs, setLiveJobs] = useState<Job[]>([]);

  useEffect(() => {
    const es = new EventSource(url);
    es.onmessage = (e) => {
      const job: Job = JSON.parse(e.data);
      setLiveJobs((prev) =>
        prev.some((j) => j.id === job.id) ? prev : [job, ...prev]
      );
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [url]);

  return liveJobs;
}
