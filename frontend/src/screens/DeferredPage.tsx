export function DeferredPage({ name }: { name: string }) {
  return (
    <section className="card">
      <h2 className="text-sm font-bold">{name}</h2>
      <p className="text-[var(--text-muted)]">Planned for a later release.</p>
    </section>
  );
}
