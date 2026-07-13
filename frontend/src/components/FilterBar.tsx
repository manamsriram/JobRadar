interface Filters {
  title: string;
  location: string;
}

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
    </div>
  );
}
