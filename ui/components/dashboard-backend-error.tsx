type DashboardBackendErrorProps = {
  title: string;
  detail: string;
  error: Error;
};

export function DashboardBackendError({
  title,
  detail,
  error,
}: DashboardBackendErrorProps) {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-[960px] items-center px-4 py-10 sm:px-6 lg:px-8">
      <section className="panel w-full overflow-hidden p-6 sm:p-8">
        <p className="eyebrow">Dashboard API unavailable</p>
        <h1 className="mt-3 text-3xl font-semibold text-slate-950 sm:text-4xl">{title}</h1>
        <p className="mt-4 max-w-2xl text-sm text-slate-600 sm:text-base">{detail}</p>

        <div className="mt-6 rounded-[24px] border border-amber-200 bg-amber-50/80 p-5">
          <p className="text-sm font-medium text-amber-950">Recovery steps</p>
          <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-amber-900">
            <li>Start the Python dashboard API with <code className="rounded bg-white px-1.5 py-0.5">python dashboard.py</code>.</li>
            <li>Or point the UI at a running backend with <code className="rounded bg-white px-1.5 py-0.5">DASHBOARD_API_BASE</code>.</li>
            <li>Reload <code className="rounded bg-white px-1.5 py-0.5">http://127.0.0.1:3000/</code> once port <code className="rounded bg-white px-1.5 py-0.5">8050</code> is available.</li>
          </ol>
        </div>

        <div className="mt-6 rounded-[24px] bg-slate-950 p-5 text-sm text-white/75">
          <p className="text-[11px] uppercase tracking-[0.18em] text-white/45">Server error</p>
          <pre className="mt-3 whitespace-pre-wrap font-[var(--font-mono)] text-xs text-white/85">{error.message}</pre>
        </div>
      </section>
    </main>
  );
}
