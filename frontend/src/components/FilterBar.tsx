interface Filters {
  title: string;
  location: string;
  source: string;
}

const SOURCES = [
  { value: "", label: "All sources" },
  { value: "custom", label: "Company site" },
  { value: "levels", label: "Levels.fyi" },
  { value: "yc", label: "Y Combinator" },
  { value: "tldr", label: "TLDR Jobs" },
  { value: "funding", label: "Funding signal" },
];

export default function FilterBar({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (f: Filters) => void;
}) {
  return (
    <div className="flex gap-3 mb-4">
      <input
        type="text"
        placeholder="Filter by title…"
        value={filters.title}
        onChange={(e) => onChange({ ...filters, title: e.target.value })}
        className="border rounded px-3 py-1 text-sm flex-1"
      />
      <input
        type="text"
        placeholder="Filter by location…"
        value={filters.location}
        onChange={(e) => onChange({ ...filters, location: e.target.value })}
        className="border rounded px-3 py-1 text-sm flex-1"
      />
      <select
        aria-label="Filter by source"
        value={filters.source}
        onChange={(e) => onChange({ ...filters, source: e.target.value })}
        className="border rounded px-3 py-1 text-sm"
      >
        {SOURCES.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </select>
    </div>
  );
}
