import { useEffect, useMemo, useState } from 'react'
import { BarChart3 } from 'lucide-react'
import { fetchApiDefinitions } from '../../lib/api-client'
import { toast } from '../../lib/toast'
import SkillInsightsModal from '../../components/workspace-v2/SkillInsightsModal'

interface ApiItem {
  id: string
  name: string
  api_code?: string
  status?: string
  config?: { source_country?: string } | null
}

/** 平台管理员「技能洞察」—— 选一个 API，查看其优化诊断（每字段轨迹 / 守护 / 已挂技能 /
 *  typed-edit meta / 被拒编辑）。这是运维/调优视图，已从租户工作区移到这里。 */
export default function SkillInsights() {
  const [apis, setApis] = useState<ApiItem[]>([])
  const [selected, setSelected] = useState<string>('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchApiDefinitions()
      .then((res) => {
        const list: ApiItem[] = Array.isArray(res.data) ? res.data : res.data?.items ?? []
        setApis(list)
        if (list.length) setSelected(list[0].id)
      })
      .catch(() => toast.error('加载 API 列表失败'))
      .finally(() => setLoading(false))
  }, [])

  const sorted = useMemo(
    () => [...apis].sort((a, b) => (a.name || '').localeCompare(b.name || '')),
    [apis],
  )

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-cyan-600" /> 技能洞察
        </h1>
        <p className="text-sm text-gray-500 mt-1 max-w-3xl">
          选择一个 API，查看其优化诊断：每字段跨轮准确率轨迹、slow-update 守护（稳定/波动）、
          已挂技能、typed-edit 编辑结果（接受/拒绝）。运维 / 调优视图。
        </p>
      </div>

      {/* API picker */}
      <div className="flex items-center gap-3">
        <span className="text-sm text-gray-600 flex-shrink-0">选择 API</span>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          disabled={loading || apis.length === 0}
          className="flex-1 max-w-md px-3 py-2 rounded-lg border border-gray-200 text-sm bg-white focus:border-indigo-400 focus:outline-none"
        >
          {loading && <option>加载中…</option>}
          {!loading && apis.length === 0 && <option>暂无 API</option>}
          {sorted.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
              {a.config?.source_country ? `（${a.config.source_country}）` : ''}
              {a.status ? ` · ${a.status}` : ''}
            </option>
          ))}
        </select>
      </div>

      {/* Inline insights for the selected API. Dark card on the light admin page. */}
      {selected && (
        <div className="rounded-xl overflow-hidden bg-[#18181c] p-3">
          <SkillInsightsModal
            key={selected}
            apiDefinitionId={selected}
            open
            inline
            onClose={() => {}}
          />
        </div>
      )}
    </div>
  )
}
