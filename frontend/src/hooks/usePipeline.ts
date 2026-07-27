import { useCallback, useEffect, useState } from "react";

export interface Application {
  id: string;
  job_id: string;
  job_title: string;
  company: string;
  job_url: string | null;
  current_state: string;
  applied_at: string;
  updated_at: string;
  next_action_due: string | null;
}

export interface PipelineEvent {
  id: number;
  application_id: string;
  from_state: string | null;
  to_state: string;
  occurred_at: string;
  note: string | null;
  scorecard: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
}

export type Pipeline = Record<string, Application[]>;

export class TransitionError extends Error {}

/** Kanban state + transition action for the pipeline tracking feature. */
export function usePipeline() {
  const [pipeline, setPipeline] = useState<Pipeline>({});
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    setLoading(true);
    return fetch("/api/pipeline")
      .then((r) => r.json())
      .then(setPipeline)
      .catch(() => setPipeline({}))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function transition(
    applicationId: string,
    toState: string,
    opts?: { note?: string; scorecard?: Record<string, unknown> }
  ) {
    const res = await fetch(`/api/applications/${applicationId}/transition`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to_state: toState, ...opts }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new TransitionError(body.detail || `transition failed (${res.status})`);
    }
    await refresh();
  }

  return { pipeline, loading, refresh, transition };
}
