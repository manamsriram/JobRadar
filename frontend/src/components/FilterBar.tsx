interface Filters {
  title: string;
  location: string;
  source: string;
}

const SOURCES = [
  { value: "", label: "All sources" },
  { value: "custom", label: "Company site" },
  { value: "levels", label: "Levels.fyi" },
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
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6">
      <label className="border border-gray-300 bg-cream-field px-3 py-2 flex flex-col gap-1">
        <span className="font-mono text-[10px] tracking-widest text-gray-500 uppercase">
          Title
        </span>
        <input
          type="text"
          placeholder="Filter by title…"
          value={filters.title}
          onChange={(e) => onChange({ ...filters, title: e.target.value })}
          className="bg-transparent text-sm font-medium outline-none"
        />
      </label>
      <label className="border border-gray-300 bg-cream-field px-3 py-2 flex flex-col gap-1">
        <span className="font-mono text-[10px] tracking-widest text-gray-500 uppercase">
          Location
        </span>
        <input
          type="text"
          placeholder="Filter by location…"
          value={filters.location}
          onChange={(e) => onChange({ ...filters, location: e.target.value })}
          className="bg-transparent text-sm font-medium outline-none"
        />
      </label>
      <label className="border border-gray-300 bg-cream-field px-3 py-2 flex flex-col gap-1">
        <span className="font-mono text-[10px] tracking-widest text-gray-500 uppercase">
          Source
        </span>
        <select
          aria-label="Filter by source"
          value={filters.source}
          onChange={(e) => onChange({ ...filters, source: e.target.value })}
          className="bg-transparent text-sm font-medium outline-none"
        >
          {SOURCES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
