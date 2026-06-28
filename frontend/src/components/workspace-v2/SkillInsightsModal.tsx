import { useEffect, useState } from 'react'
import { X, Loader2, Pin, AlertTriangle, Sparkles, BarChart3 } from 'lucide-react'
import { toast } from '../../lib/toast'
import { fetchSkillInsights, type SkillInsights, type SkillInsightField } from '../../lib/api-client'

interface Props {
  apiDefinitionId: string | null
  open: boolean
  onClose: () => void
}

/** P5 技能/优化洞察（只读）。一处显性化 P1–P4：每字段跨轮准确率轨迹（P1 留出分）、
 *  slow-update 守护状态（P3 · pin 稳定 / caution 波动）、已挂技能（P2/P4）。 */
export default function SkillInsightsModal({ apiDefinitionId, open, onClose }: Props) {
  const [data, setData] = useState<SkillInsights | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !apiDefinitionId) return
    setLoading(true)
    fetchSkillInsights(apiDefinitionId)
      .then(({ data }) => setData(data))
      .catch(() => toast.error('加载技能洞察失败'))
      .finally(() => setLoading(false))
  }, [open, apiDefinitionId])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="w-[720px] max-h-[82vh] overflow-hidden flex flex-col bg-[#1e1e24] border border-white/10 rounded-xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/10">
          <div className="flex items-center gap-2 text-white font-medium">
            <BarChart3 className="w-4 h-4 text-cyan-400" />
            技能洞察
            {data && (
              <span className="text-xs text-gray-500 font-normal">
                v{data.version ?? '—'} · {data.has_run ? `${data.rounds} 轮迭代` : '尚无迭代'}
              </span>
            )}
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-3">
          {loading ? (
            <div className="flex items-center justify-center py-10 text-gray-500">
              <Loader2 className="w-4 h-4 animate-spin" />
            </div>
          ) : !data || data.fields.length === 0 ? (
            <p className="text-xs text-gray-500 text-center py-8">
              暂无字段洞察（尚未生成 active 版本）。
            </p>
          ) : (
            <>
              <div className="flex items-center gap-4 text-[10px] text-gray-500 mb-3 pb-2 border-b border-white/5">
                <span className="flex items-center gap-1"><Pin className="w-3 h-3 text-emerald-400" /> 稳定达标（pin）</span>
                <span className="flex items-center gap-1"><AlertTriangle className="w-3 h-3 text-amber-400" /> 波动（caution）</span>
                <span className="flex items-center gap-1"><Sparkles className="w-3 h-3 text-cyan-400" /> 已挂技能</span>
                <span className="ml-auto">柱 = 各轮准确率</span>
              </div>
              {/* ADR-002: typed-edit meta memory (accept/reject by op) */}
              {data.meta_memory && (data.meta_memory.total_accepted + data.meta_memory.total_rejected > 0) && (
                <div className="text-[11px] text-gray-400 mb-3 bg-white/5 rounded-lg px-3 py-2 flex items-center gap-4 flex-wrap">
                  <span>编辑结果：<b className="text-emerald-400">接受 {data.meta_memory.total_accepted}</b> · <b className="text-rose-400">拒绝 {data.meta_memory.total_rejected}</b></span>
                  {Object.entries(data.meta_memory.by_op).map(([op, c]) => (
                    <span key={op} className="text-gray-500">{op}: {c.accepted}✓/{c.rejected}✗</span>
                  ))}
                </div>
              )}
              <div className="space-y-1.5">
                {data.fields.map((f) => (
                  <FieldRow key={f.field} f={f} />
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function pct(v: number): string {
  return `${Math.round(v * 100)}%`
}
function barColor(v: number): string {
  if (v >= 0.9) return 'bg-emerald-500'
  if (v >= 0.6) return 'bg-amber-500'
  return 'bg-rose-500'
}

function FieldRow({ f }: { f: SkillInsightField }) {
  const last = f.trajectory.length ? f.trajectory[f.trajectory.length - 1] : null
  return (
    <div className="px-2.5 py-1.5 rounded-lg bg-white/5 border border-white/5">
      <div className="flex items-center gap-3">
      {/* name + guardian */}
      <div className="flex items-center gap-1.5 w-44 min-w-0">
        <span className="text-sm text-gray-200 truncate" title={f.field}>
          {f.display_name}
        </span>
        {f.guardian?.kind === 'pin' && (
          <Pin className="w-3 h-3 text-emerald-400 flex-shrink-0" />
        )}
        {f.guardian?.kind === 'caution' && (
          <AlertTriangle className="w-3 h-3 text-amber-400 flex-shrink-0" />
        )}
      </div>

      {/* trajectory bars */}
      <div className="flex items-end gap-0.5 h-6 flex-shrink-0" title={f.guardian?.note || ''}>
        {f.trajectory.length === 0 ? (
          <span className="text-[10px] text-gray-600">无轨迹</span>
        ) : (
          f.trajectory.map((v, i) => (
            <div
              key={i}
              className={`w-2 rounded-sm ${barColor(v)}`}
              style={{ height: `${Math.max(2, v * 24)}px` }}
              title={`第 ${i + 1} 轮：${pct(v)}`}
            />
          ))
        )}
      </div>
      {last !== null && (
        <span className="text-[11px] text-gray-400 w-9 flex-shrink-0">{pct(last)}</span>
      )}

      {/* attached skills */}
      <div className="flex-1 flex flex-wrap gap-1 justify-end min-w-0">
        {f.skills.map((s) => (
          <span
            key={s}
            className="inline-flex items-center gap-1 text-[10px] text-cyan-300 bg-cyan-500/15 rounded px-1.5 py-0.5"
          >
            <Sparkles className="w-2.5 h-2.5" />
            {s}
          </span>
        ))}
      </div>
      </div>

      {/* ADR-002: iterated rule section (typed-edit mode) */}
      {f.rule_edits.length > 0 && (
        <div className="mt-1.5 pt-1.5 pl-1 space-y-0.5 border-t border-white/5">
          <div className="text-[10px] text-gray-500">规则段（迭代沉淀）</div>
          {f.rule_edits.map((r, i) => (
            <div key={i} className="text-[11px] text-gray-400 flex gap-1.5">
              <span className="text-purple-400 flex-shrink-0">▸</span>
              <span className="break-words">{r}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
