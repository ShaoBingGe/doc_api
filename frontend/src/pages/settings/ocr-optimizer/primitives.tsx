// L2.2：OcrOptimizer 拆分——基础 UI 原语（JsonBlock / Section / Field）。
export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="text-[11px] leading-relaxed bg-gray-900 text-gray-100 rounded-md p-3 overflow-auto max-h-72">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

export function Section({
  title,
  right,
  children,
}: {
  title: string
  right?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="bg-white rounded-xl border border-gray-200 mb-6">
      <header className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
        <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
        {right}
      </header>
      <div className="p-5">{children}</div>
    </section>
  )
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">{label}</div>
      <div className="text-sm text-gray-800 break-all">{children}</div>
    </div>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────

