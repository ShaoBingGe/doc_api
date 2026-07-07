import { useEffect, useState, useCallback } from 'react'
import { X, Plus, Trash2, Globe, Lock, Loader2, Link2 } from 'lucide-react'
import { cn } from '../../lib/utils'
import { toast } from '../../lib/toast'
import {
  fetchOcrSkills,
  createOcrSkill,
  deleteOcrSkill,
  fetchOcrVersions,
  fetchOcrVersion,
  attachOcrSkill,
  type OcrSkill,
} from '../../lib/api-client'

interface Props {
  apiDefinitionId: string | null
  open: boolean
  onClose: () => void
  /** Render as an inline page (the「新增技能」tab) instead of a modal overlay. */
  inline?: boolean
}

type ModuleLite = { module_key: string; display_name: string; skill_ids: string[] }

/** 技能库管理面板（ADR-001 P2 + P4 步骤④）。技能 = 可复用的 prompt 规则片段：
 *  全局（同国 API 共享）/ 私有（仅本 API）。「挂到字段」把技能 attach 到当前
 *  active 版本的某 module → 立即重组 prompt 注入该字段提示词（SKILL_LIBRARY_RENDER 默认开启）。 */
export default function SkillLibraryModal({ apiDefinitionId, open, onClose, inline }: Props) {
  const [skills, setSkills] = useState<OcrSkill[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const [content, setContent] = useState('')
  const [isGlobal, setIsGlobal] = useState(false)
  const [versionId, setVersionId] = useState<string | null>(null)
  const [modules, setModules] = useState<ModuleLite[]>([])

  const load = useCallback(async () => {
    if (!apiDefinitionId) return
    setLoading(true)
    try {
      const res = await fetchOcrSkills(apiDefinitionId)
      setSkills(res.data || [])
    } catch {
      toast.error('加载技能失败')
    } finally {
      setLoading(false)
    }
  }, [apiDefinitionId])

  // Load the active version's modules so skills can be attached to a field.
  const loadVersionModules = useCallback(async () => {
    if (!apiDefinitionId) return
    try {
      const vres = await fetchOcrVersions(apiDefinitionId)
      const versions = (vres.data || []) as Array<{ id: string; status: string }>
      const active = versions.find((v) => v.status === 'active') ?? versions[versions.length - 1]
      if (!active) {
        setVersionId(null)
        setModules([])
        return
      }
      setVersionId(active.id)
      const dres = await fetchOcrVersion(apiDefinitionId, active.id)
      const mods = ((dres.data as { modules?: ModuleLite[] })?.modules || []).map((m) => ({
        module_key: m.module_key,
        display_name: m.display_name,
        skill_ids: (m.skill_ids || []).map((x) => String(x)),
      }))
      setModules(mods)
    } catch {
      /* attach UI is optional enhancement — ignore load errors */
    }
  }, [apiDefinitionId])

  useEffect(() => {
    if (open) {
      void load()
      void loadVersionModules()
    }
  }, [open, load, loadVersionModules])

  const handleCreate = async () => {
    if (!apiDefinitionId || !name.trim() || !content.trim()) return
    setSaving(true)
    try {
      await createOcrSkill(apiDefinitionId, {
        name: name.trim(),
        content: content.trim(),
        api_definition_id: isGlobal ? null : apiDefinitionId,
      })
      setName('')
      setContent('')
      setIsGlobal(false)
      toast.success('已创建技能')
      await load()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || '创建失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (!apiDefinitionId) return
    try {
      await deleteOcrSkill(apiDefinitionId, id)
      await load()
      await loadVersionModules()
    } catch {
      toast.error('删除失败')
    }
  }

  const handleAttach = async (skillId: string, moduleKey: string) => {
    if (!apiDefinitionId || !versionId || !moduleKey) return
    try {
      await attachOcrSkill(apiDefinitionId, versionId, moduleKey, skillId)
      toast.success('已挂到字段')
      await loadVersionModules()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || '挂载失败')
    }
  }

  if (!open) return null

  const body = (
      <div
        className={cn(
          'overflow-hidden flex flex-col bg-[#1e1e24] border border-white/10 rounded-xl',
          inline
            ? 'w-full max-w-2xl mx-auto h-full'
            : 'w-[640px] max-h-[80vh] shadow-2xl',
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/10">
          <div className="text-white font-medium">{inline ? '新增技能' : '技能库'}</div>
          {!inline && (
            <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto px-5 py-3 space-y-2">
          {loading ? (
            <div className="flex items-center justify-center py-8 text-gray-500">
              <Loader2 className="w-4 h-4 animate-spin" />
            </div>
          ) : skills.length === 0 ? (
            <p className="text-xs text-gray-500 text-center py-6">
              暂无技能。技能是可复用的识别规则片段，挂到字段后立即注入该字段 prompt。
            </p>
          ) : (
            skills.map((s) => {
              const attachedTo = modules.filter((m) => m.skill_ids.includes(s.id))
              return (
                <div
                  key={s.id}
                  className="p-2.5 rounded-lg bg-white/5 border border-white/5 space-y-2"
                >
                  <div className="flex items-start gap-2">
                    <span
                      className={cn(
                        'mt-0.5 inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full flex-shrink-0',
                        s.api_definition_id == null
                          ? 'bg-cyan-500/20 text-cyan-300'
                          : 'bg-purple-500/20 text-purple-300',
                      )}
                      title={s.api_definition_id == null
                        ? `全局技能（${s.country ? s.country + ' 国家' : '通用'}，同国 API 共享）`
                        : '本 API 私有技能'}
                    >
                      {s.api_definition_id == null ? <Globe className="w-2.5 h-2.5" /> : <Lock className="w-2.5 h-2.5" />}
                      {s.api_definition_id == null ? `${s.country ? s.country + ' ' : ''}全局` : '私有'}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-gray-200 truncate">{s.name}</div>
                      <div className="text-xs text-gray-500 whitespace-pre-wrap break-words">{s.content}</div>
                    </div>
                    <button
                      onClick={() => handleDelete(s.id)}
                      className="p-1 rounded text-gray-500 hover:text-red-400 hover:bg-red-500/10 flex-shrink-0"
                      title="停用此技能"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>

                  {/* Attach-to-field (P4 步骤④) */}
                  {modules.length > 0 && (
                    <div className="flex items-center gap-2 pl-1">
                      <span className="text-[10px] text-gray-500 truncate flex-1" title={attachedTo.map((m) => m.display_name).join('、')}>
                        {attachedTo.length
                          ? `已挂：${attachedTo.map((m) => m.display_name).join('、')}`
                          : '未挂载（挂到字段后才生效）'}
                      </span>
                      <AttachControl
                        modules={modules}
                        attached={attachedTo.map((m) => m.module_key)}
                        onAttach={(mk) => handleAttach(s.id, mk)}
                      />
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>

        {/* Create */}
        <div className="border-t border-white/10 px-5 py-3 space-y-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="技能名称（如：日元金额规整）"
            className="w-full bg-[#15151a] border border-white/10 rounded px-2.5 py-1.5 text-sm text-white outline-none focus:border-purple-500/50"
          />
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="规则内容（如：所有金额去千分位与「円/¥」后输出纯数值）"
            rows={2}
            className="w-full bg-[#15151a] border border-white/10 rounded px-2.5 py-1.5 text-sm text-white outline-none focus:border-purple-500/50 resize-none"
          />
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
              <input
                type="checkbox"
                checked={isGlobal}
                onChange={(e) => setIsGlobal(e.target.checked)}
                className="w-3.5 h-3.5"
              />
              设为全局技能（所有 API 共享）
            </label>
            <button
              onClick={handleCreate}
              disabled={saving || !name.trim() || !content.trim()}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-purple-600 hover:bg-purple-700 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-colors"
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
              新建技能
            </button>
          </div>
        </div>
      </div>
  )

  if (inline) {
    return <div className="h-full overflow-hidden bg-[#18181c] px-6 py-4">{body}</div>
  }
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      {body}
    </div>
  )
}

/** 把一条技能挂到某字段：下拉选未挂载的字段 + 挂载按钮。 */
function AttachControl({
  modules,
  attached,
  onAttach,
}: {
  modules: ModuleLite[]
  attached: string[]
  onAttach: (moduleKey: string) => void
}) {
  const [sel, setSel] = useState('')
  const available = modules.filter((m) => !attached.includes(m.module_key))
  if (available.length === 0) {
    return <span className="text-[10px] text-gray-600 flex-shrink-0">已挂全部字段</span>
  }
  return (
    <div className="flex items-center gap-1.5 flex-shrink-0">
      <select
        value={sel}
        onChange={(e) => setSel(e.target.value)}
        className="bg-[#15151a] border border-white/10 rounded px-1.5 py-1 text-[11px] text-gray-300 outline-none max-w-[150px]"
      >
        <option value="">挂到字段…</option>
        {available.map((m) => (
          <option key={m.module_key} value={m.module_key}>
            {m.display_name}
          </option>
        ))}
      </select>
      <button
        disabled={!sel}
        onClick={() => {
          onAttach(sel)
          setSel('')
        }}
        className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-cyan-600/80 hover:bg-cyan-600 disabled:opacity-40 disabled:cursor-not-allowed text-white"
      >
        <Link2 className="w-3 h-3" /> 挂载
      </button>
    </div>
  )
}
