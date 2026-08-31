import { useEffect, useState } from "react";

export interface EmailGuess {
  email: string;
  pattern: string;
  status: "verified" | "probable" | "unknown";
  reasons: string[];
}

export interface Eligibility {
  eligible: boolean;
  basis: string;
  blockers: string[];
}

export interface Contact {
  name: string;
  title?: string;
  email?: string;
  linkedin?: string;
  // Set by the outreach discovery path (origin "search"); Hunter-returned
  // contacts carry a real `email` instead and no guesses.
  origin?: "hunter" | "search";
  source_url?: string;
  source_type?: "linkedin" | "company_site";
  reasons?: string[];
  guesses?: EmailGuess[];
  // Set by /api/jobs/{id}/outreach — whether the top guess can be sent
  // without spending a verification credit, and why not when it can't.
  eligibility?: Eligibility;
  verified_status?: string;
}

export interface OutreachRecord {
  id: string;
  job_id: string;
  domain: string;
  email: string;
  contact_name?: string | null;
  contact_title?: string | null;
  subject: string;
  body: string;
  status: string;
  send_eligibility?: string | null;
  override_used?: boolean;
  sent_at?: string | null;
  replied_at?: string | null;
  error?: string | null;
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
