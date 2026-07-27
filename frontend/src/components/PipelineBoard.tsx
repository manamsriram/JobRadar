import { usePipeline, type Application, TransitionError } from "../hooks/usePipeline";

const COLUMNS = [
  "APPLIED",
  "SCREENING",
  "INTERVIEWING",
  "OFFER_PENDING",
  "HIRED",
  "REJECTED",
  "WITHDRAWN",
] as const;

const TERMINAL = new Set(["HIRED", "REJECTED", "WITHDRAWN"]);

// Mirrors backend/pipeline_state.py's forward transition matrix.
const FORWARD: Record<string, string[]> = {
  APPLIED: ["SCREENING", "INTERVIEWING"],
  SCREENING: ["INTERVIEWING"],
  INTERVIEWING: ["INTERVIEWING", "OFFER_PENDING"], // self-loop = add scorecard/note
  OFFER_PENDING: ["HIRED"],
};

export default function PipelineBoard() {
  const { pipeline, loading, transition } = usePipeline();

  async function handleMove(app: Application, toState: string) {
    let note: string | null = null;
    let scorecard: Record<string, unknown> | undefined;

    if (toState === app.current_state) {
      note = window.prompt("Note for this round (optional):");
      const rating = window.prompt("Scorecard rating 1-5 (optional, gates Offer Pending):");
      if (rating) scorecard = { rating: Number(rating) };
    } else if (toState === "REJECTED" || toState === "WITHDRAWN") {
      note = window.prompt(`Reason for ${toState.toLowerCase()} (optional):`);
    }

    try {
      await transition(app.id, toState, {
        ...(note ? { note } : {}),
        ...(scorecard ? { scorecard } : {}),
      });
    } catch (e) {
      alert(e instanceof TransitionError ? e.message : "transition failed");
    }
  }

  if (loading) return <div className="text-gray-500 text-sm">Loading pipeline…</div>;

  return (
    <div className="flex gap-4 overflow-x-auto pb-4">
      {COLUMNS.map((col) => {
        const apps = pipeline[col] || [];
        return (
          <div key={col} className="flex-shrink-0 w-64">
            <h3 className="text-sm font-semibold text-gray-600 mb-2">
              {col.replace("_", " ")} ({apps.length})
            </h3>
            <div className="space-y-2">
              {apps.map((app) => (
                <div key={app.id} className="border rounded p-2 bg-white shadow-sm text-sm">
                  <div className="font-medium">{app.job_title}</div>
                  <div className="text-gray-500">{app.company}</div>
                  {!TERMINAL.has(app.current_state) && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {(FORWARD[app.current_state] || []).map((next) => (
                        <button
                          key={next}
                          className="text-xs px-2 py-1 border rounded hover:bg-gray-50"
                          onClick={() => handleMove(app, next)}
                        >
                          {next === app.current_state ? "Annotate" : `-> ${next.replace("_", " ")}`}
                        </button>
                      ))}
                      <button
                        className="text-xs px-2 py-1 border rounded text-red-600 hover:bg-red-50"
                        onClick={() => handleMove(app, "REJECTED")}
                      >
                        Reject
                      </button>
                      <button
                        className="text-xs px-2 py-1 border rounded text-gray-500 hover:bg-gray-50"
                        onClick={() => handleMove(app, "WITHDRAWN")}
                      >
                        Withdraw
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
