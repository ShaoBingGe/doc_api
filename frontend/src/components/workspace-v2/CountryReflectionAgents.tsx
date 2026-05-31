/**
 * CountryReflectionAgents — view/edit the two country-scoped reflection
 * agents (add_field + edit_field) for the workspace's country.
 *
 * Lives at the top of the 优化过程 (OptimizationProcessPanel) tab. Resolves
 * the country from the ApiDef's config.source_country. Hidden when no
 * country is set (i.e. ApiDefs that aren't derived from a country template).
 */
import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Save, Loader2, BookOpen, Edit3, X } from 'lucide-react'
import apiClient from '../../lib/api-client'
import { toast } from '../../lib/toast'
import { cn } from '../../lib/utils'

interface AgentDef {
  key: string
  display_name: string
  country: string
  kind: 'add' | 'edit'
  version: number
  remark: string
  system_prompt: string
  user_prompt_template: string
}

interface Props {
  apiDefinitionId: string
}

export default function CountryReflectionAgents({ apiDefinitionId }: Props) {
  const [country, setCountry] = useState<string | null>(null)
  const [agents, setAgents] = useState<{ add: AgentDef | null; edit: AgentDef | null } | null>(null)
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      // 1. resolve country from ApiDef
      const defRes = await apiClient.get(`/api/v1/api-definitions/${apiDefinitionId}`)
      const cfg = (defRes.data?.config ?? {}) as Record<string, unknown>
      const c = (cfg.source_country as string) || null
      setCountry(c)
      if (!c) {
        setAgents(null)
        return
      }
      // 2. fetch the two agents for that country
      const aRes = await apiClient.get(`/api/v1/reflection-agents/${c}`)
      setAgents(aRes.data)
    } catch (err: unknown) {
      console.error('CountryReflectionAgents reload error:', err)
    } finally {
      setLoading(false)
    }
  }, [apiDefinitionId])

  useEffect(() => { void reload() }, [reload])

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/5 text-xs text-gray-500">
        <Loader2 className="w-3 h-3 animate-spin" />
        加载国家反思 agent...
      </div>
    )
  }
  if (!country) return null

  const hasAdd = !!agents?.add
  const hasEdit = !!agents?.edit

  return (
    <div className="border-b border-white/5 bg-[#1a1a20]">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-2 hover:bg-white/5 transition-colors"
      >
        <div className="flex items-center gap-2">
          <BookOpen className="w-3.5 h-3.5 text-amber-400" />
          <span className="text-sm font-medium text-gray-200">
            🇲🇾 {country} 国家反思 Agent
          </span>
          <span className="text-[10px] text-gray-500">
            （全局通用，所有客户共享；产品技术维护）
          </span>
        </div>
        <div className="flex items-center gap-2">
          {hasAdd && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300">
              新增字段 v{agents!.add!.version}
            </span>
          )}
          {hasEdit && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300">
              修改字段 v{agents!.edit!.version}
            </span>
          )}
          {expanded ? <ChevronDown className="w-4 h-4 text-gray-500" /> : <ChevronRight className="w-4 h-4 text-gray-500" />}
        </div>
      </button>

      {expanded && agents && (
        <div className="px-4 pb-3 grid grid-cols-2 gap-3">
          <AgentCard
            country={country}
            agent={agents.add}
            kind="add"
            title="新增字段反思"
            badgeCls="bg-blue-500/20 text-blue-300"
            onSaved={reload}
          />
          <AgentCard
            country={country}
            agent={agents.edit}
            kind="edit"
            title="修改字段反思"
            badgeCls="bg-emerald-500/20 text-emerald-300"
            onSaved={reload}
          />
        </div>
      )}
    </div>
  )
}

// ─── Single agent card ───────────────────────────────────────────────────────

function AgentCard({
  country,
  agent,
  kind,
  title,
  badgeCls,
  onSaved,
}: {
  country: string
  agent: AgentDef | null
  kind: 'add' | 'edit'
  title: string
  badgeCls: string
  onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [draftSystem, setDraftSystem] = useState('')
  const [draftUser, setDraftUser] = useState('')
  const [draftRemark, setDraftRemark] = useState('')
  const [saving, setSaving] = useState(false)

  const startEdit = () => {
    setDraftSystem(agent?.system_prompt ?? '')
    setDraftUser(agent?.user_prompt_template ?? '')
    setDraftRemark(agent?.remark ?? '')
    setEditing(true)
  }

  const cancelEdit = () => {
    setEditing(false)
  }

  const save = async () => {
    setSaving(true)
    try {
      await apiClient.put(`/api/v1/reflection-agents/${country}/${kind}`, {
        display_name: agent?.display_name,
        remark: draftRemark,
        system_prompt: draftSystem,
        user_prompt_template: draftUser,
      })
      toast.success(`${title} 已保存 (version bump)`)
      setEditing(false)
      onSaved()
    } catch (err: unknown) {
      const e = err as { response?: { data?: { error?: { message?: string }; detail?: string } } }
      const msg = e?.response?.data?.error?.message ?? e?.response?.data?.detail ?? '保存失败'
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  if (!agent) {
    return (
      <div className="rounded-lg border border-white/10 bg-[#22222a] p-3 text-xs text-gray-500">
        <div className="flex items-center justify-between mb-1">
          <span className="font-medium text-gray-400">{title}</span>
          <span className={cn('text-[9px] px-1.5 py-0.5 rounded', badgeCls)}>未配置</span>
        </div>
        该国家尚未配置 {kind === 'add' ? '新增字段' : '修改字段'} 反思 agent。
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-white/10 bg-[#22222a] p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-200">{title}</span>
        <div className="flex items-center gap-1.5">
          <span className={cn('text-[9px] px-1.5 py-0.5 rounded font-medium', badgeCls)}>
            v{agent.version}
          </span>
          {!editing ? (
            <button
              onClick={startEdit}
              title="编辑此 agent 的 prompt"
              className="p-1 rounded text-gray-400 hover:text-white hover:bg-white/10 transition-colors"
            >
              <Edit3 className="w-3.5 h-3.5" />
            </button>
          ) : (
            <>
              <button
                onClick={save}
                disabled={saving}
                title="保存（自动 bump version）"
                className="p-1 rounded text-emerald-300 hover:bg-emerald-500/15 transition-colors disabled:opacity-40"
              >
                {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              </button>
              <button
                onClick={cancelEdit}
                disabled={saving}
                title="取消"
                className="p-1 rounded text-gray-400 hover:text-white hover:bg-white/10 transition-colors disabled:opacity-40"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </>
          )}
        </div>
      </div>

      {!editing ? (
        <>
          <div className="text-[10px] text-gray-500 leading-relaxed line-clamp-2" title={agent.remark}>
            {agent.remark || '（无备注）'}
          </div>
          <details className="text-xs">
            <summary className="cursor-pointer text-gray-400 hover:text-gray-200 text-[11px] py-0.5">
              查看 system_prompt
            </summary>
            <pre className="mt-1 p-2 bg-black/30 rounded text-[10px] text-gray-300 whitespace-pre-wrap max-h-40 overflow-auto leading-relaxed">
              {agent.system_prompt}
            </pre>
          </details>
          <details className="text-xs">
            <summary className="cursor-pointer text-gray-400 hover:text-gray-200 text-[11px] py-0.5">
              查看 user_prompt_template
            </summary>
            <pre className="mt-1 p-2 bg-black/30 rounded text-[10px] text-gray-300 whitespace-pre-wrap max-h-40 overflow-auto leading-relaxed">
              {agent.user_prompt_template}
            </pre>
          </details>
        </>
      ) : (
        <>
          <div>
            <div className="text-[10px] text-gray-500 mb-0.5">备注（remark）</div>
            <textarea
              value={draftRemark}
              onChange={(e) => setDraftRemark(e.target.value)}
              className="w-full text-[11px] bg-[#1a1a20] border border-white/10 focus:border-purple-400 rounded p-1.5 text-gray-200 outline-none resize-y min-h-[40px] leading-relaxed"
              placeholder="内部说明..."
            />
          </div>
          <div>
            <div className="text-[10px] text-gray-500 mb-0.5">system_prompt</div>
            <textarea
              value={draftSystem}
              onChange={(e) => setDraftSystem(e.target.value)}
              className="w-full text-[11px] bg-[#1a1a20] border border-white/10 focus:border-purple-400 rounded p-1.5 text-gray-200 outline-none resize-y min-h-[120px] leading-relaxed font-mono"
            />
          </div>
          <div>
            <div className="text-[10px] text-gray-500 mb-0.5">
              user_prompt_template
              <span className="text-[9px] text-gray-600 ml-2">
                （占位符 {'{module_key} {display_name} {original_value} {corrected_value} ...'}）
              </span>
            </div>
            <textarea
              value={draftUser}
              onChange={(e) => setDraftUser(e.target.value)}
              className="w-full text-[11px] bg-[#1a1a20] border border-white/10 focus:border-purple-400 rounded p-1.5 text-gray-200 outline-none resize-y min-h-[160px] leading-relaxed font-mono"
            />
          </div>
        </>
      )}
    </div>
  )
}
