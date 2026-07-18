import { useEffect, useState } from "react";

interface ResumeMeta {
  filename: string;
  updated_at: string;
}

type ResumeStatus = Record<"backend" | "frontend", ResumeMeta | null>;

const SLOTS: Array<{ key: "backend" | "frontend"; label: string }> = [
  { key: "backend", label: "Backend Resume" },
  { key: "frontend", label: "Frontend Resume" },
];

export default function ResumePanel() {
  const [status, setStatus] = useState<ResumeStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    fetch("/api/resumes")
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => setStatus(null));
  }

  useEffect(refresh, []);

  async function upload(slot: "backend" | "frontend", file: File) {
    setBusy(slot);
    setError(null);
    // ponytail: prompt() for the token instead of a settings form — single-user
    // app, add a proper stored-token UI if this becomes multi-user.
    const token = window.prompt("Resume upload token");
    if (!token) {
      setBusy(null);
      return;
    }
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch(`/api/resumes/${slot}`, {
        method: "POST",
        headers: { "X-Resume-Token": token },
        body: form,
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "upload failed");
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex gap-4 items-center mb-4 text-sm">
      {SLOTS.map(({ key, label }) => (
        <label
          key={key}
          className="flex flex-col gap-1 border rounded px-3 py-2 cursor-pointer hover:bg-gray-50"
        >
          <span className="font-medium">
            {label}
            {busy === key && "…"}
          </span>
          <span className="text-gray-500 text-xs">
            {status?.[key]?.filename ?? "none uploaded"}
          </span>
          <input
            type="file"
            accept=".txt,.pdf"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) upload(key, file);
              e.target.value = "";
            }}
          />
        </label>
      ))}
      {error && <span className="text-red-600 text-xs">{error}</span>}
    </div>
  );
}
