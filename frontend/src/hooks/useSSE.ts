import { useEffect, useState } from "react";
import type { Job } from "../types";

export function useSSE(url: string): Job[] {
  const [liveJobs, setLiveJobs] = useState<Job[]>([]);

  useEffect(() => {
    const es = new EventSource(url);
    es.onmessage = (e) => {
      const job: Job = JSON.parse(e.data);
      setLiveJobs((prev) => [job, ...prev]);
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [url]);

  return liveJobs;
}
