// Route-transition skeleton for the dashboard group. Pulsing placeholders keep
// the shell stable while a server page streams in.

export default function Loading() {
  return (
    <div className="flex animate-pulse flex-col gap-6" aria-hidden>
      <div className="flex flex-col gap-2">
        <div className="h-3 w-32 rounded bg-[color:var(--ui-bg-active)]" />
        <div className="h-8 w-64 rounded bg-[color:var(--ui-bg-soft)]" />
        <div className="h-4 w-96 max-w-full rounded bg-[color:var(--ui-bg-soft)]" />
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-24 rounded-xl border border-ui bg-[color:var(--ui-bg)]" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="h-64 rounded-xl border border-ui bg-[color:var(--ui-bg)]" />
        <div className="h-64 rounded-xl border border-ui bg-[color:var(--ui-bg)]" />
      </div>
    </div>
  );
}
