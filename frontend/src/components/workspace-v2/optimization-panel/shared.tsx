// L2.1：OptimizationProcessPanel 拆分——基础 UI 组件（Collapsible /
// EmptyPhase / FinalizeModal）。类型与纯 helper 在 ./helpers。
import { useState } from 'react'
import { Check, ChevronDown, ChevronRight, Loader2, X } from 'lucide-react'
import { cn } from '../../../lib/utils'
import { accPct, originLabel, type VersionSummary } from './helpers'

export function Collapsible({
  title,
  children,
  defaultOpen = false,
}: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-white/5 rounded-md bg-[#1a1a1f] overflow-hidden">
      <button
        onClick={() => setOpen((p) => !p)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-gray-200 hover:bg-white/5"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        {title}
      </button>
      {open && <div className="px-3 py-2 border-t border-white/5">{children}</div>}
    </div>
  )
}

export function EmptyPhase({ label }: { label: string }) {
  return <div className="text-[11px] text-gray-500 italic">{label}</div>
}

// ──────────────────────────────────────────────────────────────────────────────
// Finalize modal
// ──────────────────────────────────────────────────────────────────────────────

export function FinalizeModal({
  versions,
  defaultId,
  finalizing,
  onCancel,
  onConfirm,
}: {
  versions: VersionSummary[]
  defaultId: string | null
  finalizing: boolean
  onCancel: () => void
  onConfirm: (versionId: string) => void
}) {
  const [picked, setPicked] = useState<string | null>(defaultId)

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="w-[520px] bg-[#1e1e24] border border-white/10 rounded-xl shadow-2xl overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
          <span className="text-sm font-medium">选择要激活的版本</span>
          <button onClick={onCancel} className="text-gray-400 hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="max-h-[400px] overflow-y-auto px-2 py-3 space-y-1">
          {versions.map((v) => {
            const oi = originLabel(v.origin)
            return (
              <label
                key={v.id}
                className={cn(
                  'flex items-center gap-2 px-3 py-2 rounded-md cursor-pointer text-xs',
                  v.id === picked
                    ? 'bg-purple-500/15 ring-1 ring-purple-500/40'
                    : 'hover:bg-white/5',
                )}
              >
                <input
                  type="radio"
                  className="accent-purple-500"
                  checked={picked === v.id}
                  onChange={() => setPicked(v.id)}
                />
                <span className={cn('px-1.5 py-0.5 rounded border text-[10px]', oi.color)}>
                  v{v.version} · {oi.label}
                </span>
                <span className="text-gray-400">{accPct(v.overall_accuracy)}</span>
                {v.status === 'active' && (
                  <span className="text-[10px] text-emerald-400">★ 当前 active</span>
                )}
                <span className="ml-auto text-[10px] text-gray-500">
                  {new Date(v.created_at).toLocaleString()}
                </span>
              </label>
            )
          })}
        </div>
        <div className="px-4 py-3 border-t border-white/10 flex items-center justify-between text-[11px] text-gray-400">
          <span>选中的版本将设为 active, 老 active 版本归档</span>
          <div className="flex gap-2">
            <button
              onClick={onCancel}
              className="px-3 py-1.5 text-xs text-gray-300 hover:text-white"
            >
              取消
            </button>
            <button
              onClick={() => picked && onConfirm(picked)}
              disabled={!picked || finalizing}
              className="px-3 py-1.5 text-xs rounded-md bg-emerald-600/80 hover:bg-emerald-600 text-white disabled:opacity-50 flex items-center gap-1.5"
            >
              {finalizing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Check className="w-3.5 h-3.5" />
              )}
              确定激活
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
