export default function LiveBadge({ count }: { count: number }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
      <span className="text-sm text-gray-600">
        {count > 0 ? `${count} new since load` : "Listening for new jobs…"}
      </span>
    </div>
  );
}
