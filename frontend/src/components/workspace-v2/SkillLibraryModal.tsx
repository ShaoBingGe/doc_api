import { useEffect, useState, useCallback } from 'react'
import { X, Plus, Trash2, Globe, Lock, Loader2 } from 'lucide-react'
import { cn } from '../../lib/utils'
import { toast } from '../../lib/toast'
import {
  fetchOcrSkills,
  createOcrSkill,
  deleteOcrSkill,
  type OcrSkill,
} from '../../lib/api-client'

interface Props {
  apiDefinitionId: string | null
  open: boolean
  onClose: () => void
}

/** 技能库管理面板（ADR-001 P2）。技能 = 可复用的 prompt 规则片段：
 *  全局（所有 API 共享，需后端启用 SKILL_LIBRARY_RENDER 才注入）/ 私有（仅本 API）。 */
export default function SkillLibraryModal({ apiDefinitionId, open, onClose }: Props) {
  const [skills, setSkills] = useState<OcrSkill[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const [content, setContent] = useState('')
  const [isGlobal, setIsGlobal] = useState(false)

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

  useEffect(() => {
    if (open) void load()
  }, [open, load])

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
    } catch {
      toast.error('删除失败')
    }
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="w-[640px] max-h-[80vh] overflow-hidden flex flex-col bg-[#1e1e24] border border-white/10 rounded-xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/10">
          <div className="text-white font-medium">技能库</div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto px-5 py-3 space-y-2">
          {loading ? (
            <div className="flex items-center justify-center py-8 text-gray-500">
              <Loader2 className="w-4 h-4 animate-spin" />
            </div>
          ) : skills.length === 0 ? (
            <p className="text-xs text-gray-500 text-center py-6">
              暂无技能。技能是可复用的识别规则片段，挂到字段后（需后端启用渲染）注入 prompt。
            </p>
          ) : (
            skills.map((s) => (
              <div
                key={s.id}
                className="flex items-start gap-2 p-2.5 rounded-lg bg-white/5 border border-white/5"
              >
                <span
                  className={cn(
                    'mt-0.5 inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full flex-shrink-0',
                    s.api_definition_id == null
                      ? 'bg-cyan-500/20 text-cyan-300'
                      : 'bg-purple-500/20 text-purple-300',
                  )}
                  title={s.api_definition_id == null ? '全局技能（所有 API 共享）' : '本 API 私有技能'}
                >
                  {s.api_definition_id == null ? <Globe className="w-2.5 h-2.5" /> : <Lock className="w-2.5 h-2.5" />}
                  {s.api_definition_id == null ? '全局' : '私有'}
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
            ))
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
    </div>
  )
}
