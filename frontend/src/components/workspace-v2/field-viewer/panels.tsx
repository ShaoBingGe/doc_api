// B2：DarkFieldViewer 拆分——面板/栏组件（PendingFieldsBar / FieldEditPanel /
// MissingFieldsList / AddFieldList / WaitingForSamplesBanner / CustomizeBar）。
import { useState, useRef, useCallback, Fragment } from 'react'
import { Plus, Check, X, Trash2, Loader2, Sparkles, AlertCircle, GitBranch } from 'lucide-react'
import { cn } from '../../../lib/utils'
import { toast } from '../../../lib/toast'
import { useWorkspaceStore, type Annotation, type ProcessingResult, type FieldEditDraft, type CustomizeJobStatus } from '../../../stores/workspace-store'
import { commitDraftToOverlay, clearPendingEdits, saveDocumentAnnotation, resumeCustomizeJob } from '../../../lib/api-client'
import NoiseSampleModal from '../NoiseSampleModal'
import { FORMAT_OPTIONS } from './shared'


export function PendingFieldsBar() {
  const { pendingFields, removePendingField, clearPendingFields, confirmPendingFields, reprocessing } =
    useWorkspaceStore()

  if (pendingFields.length === 0) return null

  return (
    <div className="rounded-lg bg-amber-500/5 border border-amber-500/20 p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-amber-300 font-medium">
          待确认字段 · {pendingFields.length}
        </span>
        <button
          onClick={clearPendingFields}
          disabled={reprocessing}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors disabled:opacity-30"
        >
          清空
        </button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {pendingFields.map((f) => (
          <span
            key={f.tempId}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/15 text-xs text-amber-200"
          >
            <span className="font-medium">{f.name}</span>
            {f.value && <span className="text-amber-300/70">= {f.value}</span>}
            <button
              onClick={() => removePendingField(f.tempId)}
              disabled={reprocessing}
              className="hover:text-red-300 disabled:opacity-30"
              title="移除"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
      </div>
      <button
        onClick={confirmPendingFields}
        disabled={reprocessing}
        className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-md bg-purple-600 hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
      >
        {reprocessing ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            AI 识别中...
          </>
        ) : (
          <>
            <Sparkles className="w-4 h-4" />
            确认并 AI 识别
          </>
        )}
      </button>
    </div>
  )
}

// ─── Field edit panel (shown when editingFieldId is set) ──────────────────
//
// Renders a side-by-side "原始" / "修正后" form for the currently-editing
// field. Edits get written to fieldEditDrafts in the store; user saves all
// drafts at once via the customize bar below the field list.

export function FieldEditPanel({
  annotation,
  result,
  draft,
  onUpdate,
  onCancel,
  onSaveToOverlay,
  onDelete,
}: {
  annotation: Annotation
  result?: ProcessingResult
  draft: FieldEditDraft
  onUpdate: (patch: Partial<FieldEditDraft>) => void
  onCancel: () => void
  /** Phase 10 — commits the current draft to backend overlay so badges
   * + cascade rename fire BEFORE the user clicks the customize button. */
  onSaveToOverlay: () => Promise<void> | void
  /** Phase 11c — permanently delete this field across all docs of the
   * ApiDef AND drop it from the optimizer pipeline (no meta, no reflection). */
  onDelete: () => Promise<void> | void
}) {
  const origValue = result?.value ?? annotation.value ?? ''
  const origValueStr = origValue === null || origValue === undefined ? '' : String(origValue)
  return (
    <div className="bg-[#2a2a32] rounded-lg border border-purple-500/30 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 bg-purple-500/10 border-b border-purple-500/20">
        <div className="flex items-center gap-2">
          <span className="px-2 py-0.5 rounded-full bg-purple-500/30 text-purple-200 text-xs font-medium">
            字段编辑
          </span>
          <span className="text-sm text-gray-200">{draft.originalName || annotation.label}</span>
        </div>
        <button
          onClick={onCancel}
          className="p-1 rounded text-gray-400 hover:text-white hover:bg-white/10 transition-colors"
          title="返回字段列表（修改自动暂存）"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="p-4 space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {/* Original column */}
          <div className="space-y-3">
            <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">原始</div>
            <div>
              <div className="text-xs text-gray-500 mb-1">字段名</div>
              <div className="text-sm text-gray-300 bg-[#1e1e24] border border-white/5 rounded px-2 py-1.5">
                {draft.originalName || annotation.label}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500 mb-1">识别值</div>
              <div className="text-sm text-gray-300 bg-[#1e1e24] border border-white/5 rounded px-2 py-1.5 min-h-[32px]">
                {origValueStr || <span className="text-gray-600">（空）</span>}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500 mb-1">格式</div>
              <div className="text-sm text-gray-300 bg-[#1e1e24] border border-white/5 rounded px-2 py-1.5">
                {draft.originalFormat || annotation.fieldType}
              </div>
            </div>
          </div>

          {/* Corrected column */}
          <div className="space-y-3">
            <div className="text-xs font-medium text-purple-300 uppercase tracking-wide">修正后</div>
            <div>
              <div className="text-xs text-purple-300/70 mb-1">字段名</div>
              <input
                value={draft.correctedName}
                onChange={(e) => onUpdate({ correctedName: e.target.value })}
                className="w-full bg-[#1e1e24] border border-purple-500/30 focus:border-purple-400 rounded px-2 py-1.5 text-sm text-white outline-none transition-colors"
                placeholder="修正后的字段名"
              />
            </div>
            <div>
              <div className="text-xs text-purple-300/70 mb-1">正确值</div>
              <textarea
                value={draft.correctedValue}
                onChange={(e) => onUpdate({ correctedValue: e.target.value })}
                className="w-full bg-[#1e1e24] border border-purple-500/30 focus:border-purple-400 rounded px-2 py-1.5 text-sm text-white outline-none transition-colors min-h-[32px] resize-y"
                placeholder="从票面上读出的正确值"
                rows={1}
              />
            </div>
            <div>
              <div className="text-xs text-purple-300/70 mb-1">格式</div>
              <select
                value={draft.correctedFormat}
                onChange={(e) => onUpdate({ correctedFormat: e.target.value })}
                className="w-full bg-[#1e1e24] border border-purple-500/30 focus:border-purple-400 rounded px-2 py-1.5 text-sm text-white outline-none transition-colors"
              >
                {FORMAT_OPTIONS.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {/* 字段反馈提示（迭代反思用，不是最终 prompt）—— 与本次修改一起保存 */}
        <div>
          <div className="text-xs text-cyan-300/70 mb-1 flex items-center gap-1">
            <Sparkles className="w-3 h-3" /> 反馈提示（可选 · 喂给迭代优化，不进最终 prompt）
          </div>
          <textarea
            value={draft.feedback || ''}
            onChange={(e) => onUpdate({ feedback: e.target.value })}
            className="w-full bg-[#1e1e24] border border-cyan-500/30 focus:border-cyan-400 rounded px-2 py-1.5 text-sm text-white outline-none transition-colors min-h-[32px] resize-y"
            placeholder="例如：发票号要输出纯数字、去掉前缀；或：这个字段取右下角那个值"
            rows={2}
          />
        </div>

        <p className="text-xs text-gray-500 leading-relaxed">
          点"保存到模板"会把这条修改立刻提交到客户模板的跨样本 overlay：
          其他样本的同名字段会自动跟随重命名/同步标识，OCR 新上传样本时也会用新名识别。
          继续编辑其他字段，全部完成后点字段列表底部的"保存并生成客户专属模板"按钮统一启动迭代优化。
        </p>
        <div className="flex gap-2">
          <button
            onClick={async () => {
              // 反馈一起保存：落 overlay.field_feedback，下次优化作反思上下文
              await useWorkspaceStore
                .getState()
                .saveFieldFeedback(draft.moduleKey || draft.correctedName, draft.feedback || '')
              await onSaveToOverlay()
              onCancel()
            }}
            className="flex-1 px-3 py-2 rounded-md bg-purple-500/30 hover:bg-purple-500/40 border border-purple-500/50 text-purple-50 text-sm font-medium transition-colors flex items-center justify-center gap-2"
          >
            <Check className="w-4 h-4" />
            保存到模板（立即生效）
          </button>
          <button
            onClick={onCancel}
            className="px-3 py-2 rounded-md bg-white/5 hover:bg-white/10 border border-white/10 text-gray-400 text-sm transition-colors"
            title="仅暂存到本地，需手工点击客户化按钮才会真正提交"
          >
            仅暂存
          </button>
        </div>
        <button
          onClick={async () => {
            if (!confirm(
              `确认从所有样本中删除字段 "${draft.originalName || annotation.label}"？\n\n` +
              `· 所有文件中该字段的标注将被永久删除\n` +
              `· 客户化时不再生成该字段的 module / 反思 / schema\n` +
              `· 此操作不可撤销`,
            )) return
            await onDelete()
          }}
          className="w-full px-3 py-2 rounded-md bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 text-red-300 text-xs font-medium transition-colors flex items-center justify-center gap-2"
          title="从所有样本和优化器中彻底删除此字段"
        >
          <Trash2 className="w-3.5 h-3.5" />
          删除此字段（所有样本 + 优化器同步生效）
        </button>
      </div>
    </div>
  )
}

// ─── Missing fields list (Phase 13/15) ─────────────────────────────────────
//
// Renders fields the customer wants on every sample but the LLM did NOT
// emit on THIS doc. Each row mirrors the AddFieldList UX: type the actual
// value, OR tick "无此字段" to record that this sample genuinely lacks it.
// On save, a real Annotation row is created (source=manual). The backend's
// create_annotation mirrors source=manual annotations into pending_edits.
// added_fields automatically — so cross-doc parity is preserved without a
// separate dedup pass.

interface MissingDraft {
  value: string
  isNone: boolean   // user confirmed: this doc really doesn't have it
}

// Two visual variants sharing one interaction model. Class strings are
// written as LITERALS (not interpolated) so Tailwind's scanner emits them.
type FieldListVariant = 'missing' | 'added'

const FIELD_LIST_STYLE: Record<FieldListVariant, {
  ring: string; head: string; iconBg: string; iconText: string
  rowBg: string; inputFocus: string; checkbox: string; saveBtn: string
}> = {
  missing: {
    ring: 'border-orange-500/25',
    head: 'bg-orange-500/10',
    iconBg: 'bg-orange-500/20',
    iconText: 'text-orange-300',
    rowBg: 'bg-orange-500/5 border-orange-500/15',
    inputFocus: 'focus:border-orange-400',
    checkbox: 'accent-orange-400',
    saveBtn: 'bg-orange-500/30 hover:bg-orange-500/45 text-orange-100',
  },
  added: {
    ring: 'border-cyan-500/25',
    head: 'bg-cyan-500/10',
    iconBg: 'bg-cyan-500/20',
    iconText: 'text-cyan-300',
    rowBg: 'bg-cyan-500/5 border-cyan-500/15',
    inputFocus: 'focus:border-cyan-400',
    checkbox: 'accent-cyan-400',
    saveBtn: 'bg-cyan-500/30 hover:bg-cyan-500/45 text-cyan-100',
  },
}

export function MissingFieldsList({
  names,
  docId,
  variant = 'missing',
  title = '本样本未识别字段',
  countLabel = '个待补齐',
  description = '下列字段是本 ApiDef 的必填字段集（模板 + 客户新增 − 删除字段），本样本的 OCR 输出未包含它们。直接填写票面实际值，或勾选"无此字段"表示本样本确无此字段；保存后会跨样本同步去重。',
}: {
  names: string[]
  docId?: string
  variant?: FieldListVariant
  title?: string
  countLabel?: string
  description?: string
}) {
  const st = FIELD_LIST_STYLE[variant]
  const apiDefinitionId = useWorkspaceStore((s) => s.apiDefinitionId)
  const [drafts, setDrafts] = useState<Record<string, MissingDraft>>({})
  const [savingNames, setSavingNames] = useState<Set<string>>(new Set())
  const [deletingNames, setDeletingNames] = useState<Set<string>>(new Set())

  const updateDraft = (name: string, patch: Partial<MissingDraft>) => {
    setDrafts((s) => {
      const merged = { ...s[name], ...patch }
      return { ...s, [name]: { value: merged.value ?? '', isNone: merged.isNone ?? false } }
    })
  }

  const saveOne = useCallback(async (name: string) => {
    if (!docId) return
    const d = drafts[name] || { value: '', isNone: false }
    if (!d.isNone && !d.value.trim()) {
      toast.error('请填写值或勾选"无此字段"')
      return
    }
    setSavingNames((s) => new Set(s).add(name))
    try {
      const payload = {
        field_name: name,
        field_value: d.isNone ? null : d.value.trim(),
        field_type: 'string',
        source: 'manual' as const,
      }
      await saveDocumentAnnotation(docId, payload)
      // Refresh document so the new annotation shows in the main list
      await useWorkspaceStore.getState().loadDocument(docId)
      await useWorkspaceStore.getState().loadPendingEdits()
      await useWorkspaceStore.getState().loadRequiredFields()
      // Clear the draft for this name
      setDrafts((s) => {
        const copy = { ...s }
        delete copy[name]
        return copy
      })
      toast.success(d.isNone ? `已确认本样本无 "${name}"` : `已补齐 "${name}"`)
    } catch (err) {
      console.error('saveMissingField failed', err)
      toast.error('保存失败')
    } finally {
      setSavingNames((s) => {
        const copy = new Set(s); copy.delete(name); return copy
      })
    }
  }, [docId, drafts])

  // Phase 16 — delete a missing field entirely from the required-fields set.
  // Reuses the Phase 11a flow: POST {deleted: true, field_name} to overlay.
  // No annotation row exists on this doc (that's why it's "missing"), so
  // the cascade has nothing to delete locally; the side effect is the
  // field disappears from the required-fields list (and from every other
  // doc's required set too) + the optimizer drops it next fork.
  const deleteOne = useCallback(async (name: string) => {
    if (!apiDefinitionId) return
    if (!confirm(
      `从必填字段集中永久删除 "${name}"？\n\n` +
      `· 所有样本不再要求该字段\n` +
      `· 客户化时不再生成该字段的 module / 反思 / schema\n` +
      `· 此操作不可撤销`,
    )) return
    setDeletingNames((s) => new Set(s).add(name))
    try {
      await commitDraftToOverlay(apiDefinitionId, { deleted: true, field_name: name })
      await useWorkspaceStore.getState().loadPendingEdits()
      await useWorkspaceStore.getState().loadRequiredFields()
      // Drop the draft (if any) for this name
      setDrafts((s) => {
        const copy = { ...s }
        delete copy[name]
        return copy
      })
      toast.success(`已从必填字段集中删除 "${name}"`)
    } catch (err) {
      console.error('deleteMissingField failed', err)
      toast.error('删除失败')
    } finally {
      setDeletingNames((s) => {
        const copy = new Set(s); copy.delete(name); return copy
      })
    }
  }, [apiDefinitionId])

  return (
    <div className={cn('bg-[#2a2a32] rounded-lg border overflow-hidden', st.ring)}>
      <div className={cn('flex items-center justify-between p-3', st.head)}>
        <div className="flex items-center gap-2">
          <div className={cn('w-6 h-6 rounded flex items-center justify-center', st.iconBg, st.iconText)}>
            {variant === 'added'
              ? <Plus className="w-3.5 h-3.5" />
              : <AlertCircle className="w-3.5 h-3.5" />}
          </div>
          <span className="text-sm font-medium text-gray-200">{title}</span>
          <span className="text-xs text-gray-500 ml-2">{names.length} {countLabel}</span>
        </div>
      </div>
      <div className="p-3 space-y-2">
        <p className="text-[11px] text-gray-500 leading-relaxed">
          {description}
        </p>
        {/* Column header */}
        <div className="grid grid-cols-[7rem_1fr_4rem_3.5rem_2rem] gap-2 text-[10px] text-gray-500 uppercase tracking-wide pb-1 border-b border-white/5">
          <span>字段名</span>
          <span>票面实际值</span>
          <span className="text-center">无此字段</span>
          <span></span>
          <span></span>
        </div>
        {names.map((name) => {
          const d = drafts[name] || { value: '', isNone: false }
          const saving = savingNames.has(name)
          const deleting = deletingNames.has(name)
          return (
            <div
              key={name}
              className={cn('grid grid-cols-[7rem_1fr_4rem_3.5rem_2rem] gap-2 items-center py-1 px-1.5 rounded border', st.rowBg)}
            >
              {/* Field name with a styled hover tooltip showing the full name
                  (the column truncates at 7rem so long camelCase names like
                  billFromBusinessRegistrationNumber get clipped). */}
              <div className="relative group/name min-w-0">
                <span className="text-gray-300 text-xs font-mono truncate block cursor-help">
                  {name}
                </span>
                <div className="pointer-events-none absolute left-0 top-full mt-1 z-20 hidden group-hover/name:block">
                  <div className="bg-black/90 border border-white/10 rounded px-2 py-1 text-xs text-orange-100 font-mono shadow-lg whitespace-nowrap max-w-[28rem] break-all">
                    {name}
                  </div>
                </div>
              </div>
              <input
                type="text"
                value={d.value}
                onChange={(e) => updateDraft(name, { value: e.target.value, isNone: false })}
                onKeyDown={(e) => { if (e.key === 'Enter') void saveOne(name) }}
                placeholder={d.isNone ? '— 无此字段 —' : '直接输入实际值'}
                disabled={d.isNone || saving || deleting}
                className={cn('w-full bg-[#1e1e24] border border-white/10 rounded px-1.5 py-1 text-xs text-white outline-none disabled:opacity-40 disabled:cursor-not-allowed', st.inputFocus)}
              />
              <label className="flex items-center justify-center cursor-pointer" title="本样本无此字段">
                <input
                  type="checkbox"
                  checked={d.isNone}
                  onChange={(e) => updateDraft(name, { isNone: e.target.checked, value: e.target.checked ? '' : d.value })}
                  disabled={deleting}
                  className={cn('w-3.5 h-3.5 cursor-pointer disabled:opacity-40', st.checkbox)}
                />
              </label>
              <button
                onClick={() => void saveOne(name)}
                disabled={saving || deleting || (!d.isNone && !d.value.trim())}
                className={cn('px-2 py-1 rounded disabled:bg-white/5 disabled:text-gray-600 text-[11px] font-medium transition-colors flex items-center justify-center gap-1', st.saveBtn)}
              >
                {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : '保存'}
              </button>
              {/* Phase 16 — per-row delete. Cascades to all docs + optimizer
                  via the same flow as the "已删除字段" footer (Phase 11a). */}
              <button
                onClick={() => void deleteOne(name)}
                disabled={saving || deleting}
                title={`从所有样本和优化器中永久删除字段 "${name}"`}
                className="p-1 rounded bg-red-500/10 hover:bg-red-500/25 disabled:bg-white/5 border border-red-500/20 disabled:border-white/5 text-red-300 disabled:text-gray-600 transition-colors flex items-center justify-center"
              >
                {deleting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── Add field list (4-column: # / name / value / format) ─────────────────
//
// Replaces the simple inline NewFieldRow. Each row is one new field the
// customer wants the prompt to learn. The customize bar saves them all.

export function AddFieldList() {
  const { addFieldDrafts, addNewFieldDraft, updateAddDraft, removeAddDraft } = useWorkspaceStore()

  return (
    <div className="bg-[#2a2a32] rounded-lg border border-white/5 overflow-hidden">
      <div className="flex items-center justify-between p-3 bg-white/5">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded bg-blue-500/20 flex items-center justify-center text-blue-400">
            <Plus className="w-3.5 h-3.5" />
          </div>
          <span className="text-sm font-medium text-gray-200">新增识别字段</span>
          <span className="text-xs text-gray-500 ml-2">{addFieldDrafts.length} 行</span>
        </div>
        <button
          onClick={addNewFieldDraft}
          className="text-xs text-blue-400 hover:text-blue-300 bg-blue-500/10 hover:bg-blue-500/20 px-2 py-1 rounded transition-colors flex items-center gap-1"
        >
          <Plus className="w-3 h-3" /> 加一行
        </button>
      </div>
      {addFieldDrafts.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="bg-[#1e1e24] text-gray-400">
                <th className="px-2 py-2 text-left font-medium border-b border-white/10 w-8">#</th>
                <th className="px-2 py-2 text-left font-medium border-b border-white/10">字段名</th>
                <th className="px-2 py-2 text-left font-medium border-b border-white/10">值</th>
                <th className="px-2 py-2 text-left font-medium border-b border-white/10 w-24">格式</th>
                <th className="px-2 py-2 text-left font-medium border-b border-white/10 w-10"></th>
              </tr>
            </thead>
            <tbody>
              {addFieldDrafts.map((row, idx) => (
                <Fragment key={idx}>
                <tr className="hover:bg-white/5 transition-colors">
                  <td className="px-2 py-1.5 text-gray-500 border-b border-white/5">{idx + 1}</td>
                  <td className="px-2 py-1.5 border-b border-white/5">
                    <input
                      value={row.correctedName}
                      onChange={(e) => updateAddDraft(idx, { correctedName: e.target.value })}
                      className="w-full bg-transparent border-b border-white/10 focus:border-blue-400 text-gray-200 outline-none px-1 py-0.5"
                      placeholder="字段名"
                    />
                  </td>
                  <td className="px-2 py-1.5 border-b border-white/5">
                    <input
                      value={row.correctedValue}
                      onChange={(e) => updateAddDraft(idx, { correctedValue: e.target.value })}
                      className="w-full bg-transparent border-b border-white/10 focus:border-blue-400 text-gray-200 outline-none px-1 py-0.5"
                      placeholder="样例值（从票面读出，可空）"
                    />
                  </td>
                  <td className="px-2 py-1.5 border-b border-white/5">
                    <select
                      value={row.correctedFormat}
                      onChange={(e) => updateAddDraft(idx, { correctedFormat: e.target.value })}
                      className="w-full bg-[#1e1e24] border border-white/10 focus:border-blue-400 rounded text-gray-200 outline-none px-1 py-0.5 text-xs"
                    >
                      {FORMAT_OPTIONS.map((opt) => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-2 py-1.5 border-b border-white/5 text-center">
                    <button
                      onClick={() => removeAddDraft(idx)}
                      className="p-1 rounded text-gray-500 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </td>
                </tr>
                {/* 多行明细（P1）：array 格式 → 定义每行的列（列名 + 类型）。
                    空 = 裸值数组（每行单值）。 */}
                {row.correctedFormat === 'array' && (
                  <tr>
                    <td className="border-b border-white/5" />
                    <td colSpan={4} className="px-2 pb-2 border-b border-white/5">
                      <div className="rounded bg-cyan-500/5 border border-cyan-500/15 p-2">
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[11px] text-cyan-300/80">明细表列定义（每行的字段）</span>
                          <button
                            onClick={() => updateAddDraft(idx, {
                              columns: [...(row.columns || []), { name: '', type: 'string' }],
                            })}
                            className="text-[11px] text-cyan-400 hover:text-cyan-300 flex items-center gap-0.5"
                          >
                            <Plus className="w-3 h-3" /> 加一列
                          </button>
                        </div>
                        {(row.columns || []).length === 0 && (
                          <div className="text-[11px] text-gray-500">未定义列 → 每行为单值数组</div>
                        )}
                        {(row.columns || []).map((col, ci) => (
                          <div key={ci} className="flex items-center gap-2 mb-1">
                            <input
                              value={col.name}
                              onChange={(e) => {
                                const cols = [...(row.columns || [])]
                                cols[ci] = { ...cols[ci], name: e.target.value }
                                updateAddDraft(idx, { columns: cols })
                              }}
                              placeholder="列名"
                              className="flex-1 bg-transparent border-b border-white/10 focus:border-cyan-400 text-gray-200 outline-none px-1 py-0.5 text-xs"
                            />
                            <select
                              value={col.type}
                              onChange={(e) => {
                                const cols = [...(row.columns || [])]
                                cols[ci] = { ...cols[ci], type: e.target.value }
                                updateAddDraft(idx, { columns: cols })
                              }}
                              className="w-20 bg-[#1e1e24] border border-white/10 focus:border-cyan-400 rounded text-gray-200 outline-none px-1 py-0.5 text-xs"
                            >
                              {['string', 'number', 'date', 'boolean'].map((t) => (
                                <option key={t} value={t}>{t}</option>
                              ))}
                            </select>
                            <button
                              onClick={() => updateAddDraft(idx, {
                                columns: (row.columns || []).filter((_, i) => i !== ci),
                              })}
                              className="p-0.5 rounded text-gray-500 hover:text-red-400 hover:bg-red-500/10"
                            >
                              <X className="w-3 h-3" />
                            </button>
                          </div>
                        ))}
                      </div>
                    </td>
                  </tr>
                )}
                {/* 反馈提示行（在字段行下方）— 输入后自动保存，喂给迭代优化 */}
                <tr>
                  <td className="border-b border-white/5" />
                  <td colSpan={4} className="px-2 pb-2 border-b border-white/5">
                    <input
                      value={row.feedback || ''}
                      onChange={(e) => updateAddDraft(idx, { feedback: e.target.value })}
                      onBlur={() => {
                        const nm = (row.correctedName || '').trim()
                        if (nm) useWorkspaceStore.getState().saveFieldFeedback(nm, row.feedback || '')
                      }}
                      className="w-full bg-transparent border-b border-cyan-500/20 focus:border-cyan-400 text-gray-300 outline-none px-1 py-0.5 text-xs"
                      placeholder="↳ 反馈提示（可选 · 输入后自动保存，喂给迭代优化，不进最终 prompt）"
                    />
                  </td>
                </tr>
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Customize bar (save button + job progress) ───────────────────────────

// ─── Waiting-for-samples banner ───────────────────────────────────────────
//
// Shown inside the customize bar slot when the job is parked because the
// new (forked) ApiDefinition has < MIN_SAMPLES_FOR_ITERATION samples. The
// banner is the single upload entry point — clicking 上传样本 pops the OS
// file picker directly; multiple files can be selected at once. Each file
// uploads independently and we keep a per-file status so the customer can
// retry just the failures.

const MIN_NEW_SAMPLES = 2  // matches user's spec: "至少 2 个（最多 9 个）"
const MAX_NEW_SAMPLES = 9

interface PerFileUpload {
  id: string
  file: File
  status: 'pending' | 'uploading' | 'success' | 'failed'
  error?: string
}

export function WaitingForSamplesBanner({
  customizeJob,
  onClose,
}: {
  customizeJob: CustomizeJobStatus
  onClose: () => void
}) {
  const apiDefinitionId = useWorkspaceStore((s) => s.apiDefinitionId)
  const documents = useWorkspaceStore((s) => s.documents)
  const samplesReview = useWorkspaceStore((s) => s.samplesReview)
  const addSampleDocument = useWorkspaceStore((s) => s.addSampleDocument)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploads, setUploads] = useState<PerFileUpload[]>([])
  const [noiseOpen, setNoiseOpen] = useState(false)

  // Phase 19 single-workspace model: there is no separate "fork URL" to
  // navigate to. Banner state is driven purely by job.status now —
  // C1 fix removes the `onNewWorkspace` gate (which always evaluated true
  // post-Phase-19 since newApiDefinitionId == apiDefinitionId).
  if (customizeJob.status === 'waiting_for_samples') {
    const sampleProgress = useWorkspaceStore.getState().samplesReview
    const confirmed = sampleProgress?.confirmedCount ?? 0
    const required = sampleProgress?.requiredCount ?? 3
    const remaining = Math.max(0, required - confirmed)

    return (
      <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 space-y-2.5">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-amber-400 flex-shrink-0" />
            <span className="text-sm text-amber-200 font-medium">
              {remaining <= 0
                ? '样本已就绪，反思 + 3 轮优化即将自动启动'
                : required > 3
                  ? (confirmed >= 3
                      ? `即将启动自动迭代优化，请额外上传 ${remaining} 份多样化噪声样本`
                      : `请先上传并审视 3 份代表样本（当前 ${confirmed}/3），再补 ${required - 3} 份噪声样本`)
                  : `还需 ${remaining} 个已审视样本即可启动反思 + 优化`}
            </span>
          </div>
          <button
            onClick={onClose}
            className="p-0.5 rounded text-gray-400 hover:text-white hover:bg-white/10 flex-shrink-0"
            title="关闭提醒"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        {/* Progress bar — N cells */}
        <div className="flex gap-1.5">
          {Array.from({ length: required }).map((_, i) => (
            <div
              key={i}
              className={cn(
                'flex-1 h-1.5 rounded-full transition-colors',
                i < confirmed ? 'bg-emerald-500' : 'bg-white/10',
              )}
            />
          ))}
        </div>
        <div className="text-[11px] text-amber-100/60 leading-relaxed">
          {required > 3 ? (
            <>
              启动迭代需要 <b>{required} 个</b>样本：<b>3 个锚点</b>（你精选并审视的代表样本）
              + <b>{required - 3} 个多样化「噪声」样本</b>（不同开票方/版式/税率/扫描质量，越随机越好）。
              噪声样本作为<b>留出验证集</b>防止过拟合你那 3 张；系统会<b>自动识别并以当前结果为基线，无需逐张复核</b>。
              {confirmed >= 3
                ? '点下方按钮一次性批量上传这 ' + (required - 3) + ' 份噪声样本，上传完即自动启动 3 轮迭代。'
                : '请先把 3 份锚点样本标记为"已审视"。'}
            </>
          ) : (
            <>
              请在本工作区上传至少 {required} 个样本并把每个文档标记为"已审视"。
              全部已审视后，反思 agent 和 3 轮迭代优化会自动启动，新版本会直接激活在本工作区——
              URL 不会切换，刷新即看到优化后的字段名和识别结果。
            </>
          )}
        </div>
        <div className="flex gap-2">
          {remaining > 0 && required > 3 && confirmed >= 3 ? (
            // Noise-gate: anchors confirmed → batch-upload exactly `remaining`
            // diverse noise samples (auto-OCR + baseline GT, no per-sample review).
            <button
              onClick={() => setNoiseOpen(true)}
              className="flex-1 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm rounded transition-colors font-medium"
            >
              批量上传 {remaining} 份噪声样本 →
            </button>
          ) : remaining > 0 ? (
            <button
              onClick={() => {
                // Scroll the top sample-thumbnail column into view; document-list lives in Column A
                const docCol = document.querySelector('[data-sample-column="true"]')
                if (docCol) docCol.scrollIntoView({ behavior: 'smooth', block: 'start' })
                else toast.info('在左侧文档列上方点击"上传样本"按钮，或直接拖拽 PDF 到工作区')
              }}
              className="flex-1 px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white text-sm rounded transition-colors"
            >
              上传/确认更多样本 →
            </button>
          ) : (
            // Phase 22: 3/3 confirmed — flip to a green "开始优化" CTA that
            // manually triggers /customize-jobs/{id}/resume (in case the
            // auto-resume hook is slow or hit a race). Fires an immediate
            // pollCustomizeJob() so the banner state flips as soon as the
            // backend transitions to reflecting/optimizing.
            <button
              onClick={async () => {
                const jobId = customizeJob.jobId
                if (!jobId) {
                  toast.error('找不到对应的 customize job')
                  return
                }
                try {
                  await resumeCustomizeJob(jobId)
                  toast.success('已触发反思 + 3 轮优化')
                  // Immediate refresh so the banner flips without waiting
                  // for the 10s waiting_for_samples polling cadence.
                  await useWorkspaceStore.getState().pollCustomizeJob()
                } catch (err) {
                  console.error('manual resume failed', err)
                  toast.error('启动失败，请稍后重试')
                }
              }}
              className="flex-1 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm rounded transition-colors font-medium"
            >
              开始优化 →
            </button>
          )}
          <button
            onClick={async () => {
              if (!apiDefinitionId) return
              if (!confirm('清理后本工作区将不再显示"已重命名/已新增/已修改/已删除"标识。继续？')) return
              try {
                await clearPendingEdits(apiDefinitionId)
                await useWorkspaceStore.getState().loadPendingEdits()
                toast.success('已清理本工作区的变更标识')
              } catch {
                toast.error('清理失败')
              }
            }}
            className="px-3 py-1.5 bg-white/5 hover:bg-white/10 border border-white/15 text-gray-300 text-xs rounded transition-colors"
            title="清空本工作区的变更标识 overlay（不影响新模板）"
          >
            清理变更标识
          </button>
        </div>
        <NoiseSampleModal
          open={noiseOpen}
          onClose={() => setNoiseOpen(false)}
          noiseCount={remaining}
        />
      </div>
    )
  }

  // ── Live progress numbers ─────────────────────────────────────────────
  // Per design v3 we gate by CONFIRMED samples (customer marked the OCR
  // result as GT), not raw upload count. samplesReview is fetched on
  // workspace load and refreshed after every confirm-gt call.
  const confirmed = samplesReview?.confirmedCount ?? 0
  const totalSamples = samplesReview?.totalCount ?? documents.length
  const requiredTotal = samplesReview?.requiredCount ?? 3
  const stillNeeded = Math.max(0, requiredTotal - confirmed)
  const successUploads = uploads.filter((u) => u.status === 'success').length
  const failedUploads = uploads.filter((u) => u.status === 'failed')
  const uploadingNow = uploads.some((u) => u.status === 'uploading')
  // Quota: how many new samples the customer should still pick. We hint at
  // 2..9 per the verbatim spec, capped by remaining quota.
  const remainingMinQuota = Math.max(0, MIN_NEW_SAMPLES - successUploads)
  const remainingMaxQuota = Math.max(0, MAX_NEW_SAMPLES - successUploads)

  const triggerFilePicker = () => fileInputRef.current?.click()

  const handleFilesChosen = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    const next: PerFileUpload[] = Array.from(files).map((f) => ({
      id: `${f.name}-${f.size}-${f.lastModified}-${Math.random().toString(36).slice(2, 6)}`,
      file: f,
      status: 'pending',
    }))
    setUploads((prev) => [...prev, ...next])
    // Sequentially upload — addSampleDocument is synchronous (triggers OCR);
    // running them sequentially keeps backend load reasonable and lets us
    // surface progress per file.
    for (const item of next) {
      await uploadOne(item)
    }
    // Reset the input so picking the same file again re-fires the change.
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const uploadOne = async (item: PerFileUpload) => {
    setUploads((prev) => prev.map((u) => u.id === item.id ? { ...u, status: 'uploading' } : u))
    try {
      const result = await addSampleDocument(item.file)
      setUploads((prev) => prev.map((u) =>
        u.id === item.id
          ? { ...u, status: result ? 'success' : 'failed', error: result ? undefined : '上传失败' }
          : u,
      ))
    } catch (err: unknown) {
      const msg = (err as { message?: string })?.message || '上传失败'
      setUploads((prev) => prev.map((u) =>
        u.id === item.id ? { ...u, status: 'failed', error: msg } : u,
      ))
    }
  }

  const retryFailed = async () => {
    for (const u of failedUploads) {
      await uploadOne(u)
    }
  }

  const dismissFailure = (id: string) =>
    setUploads((prev) => prev.filter((u) => u.id !== id))

  return (
    <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-amber-400 flex-shrink-0" />
          <span className="text-sm text-amber-200 font-medium">等待上传样本以启动迭代优化</span>
          <span
            className={cn(
              'ml-1 text-[10px] px-1.5 py-0.5 rounded-full font-medium',
              stillNeeded === 0 ? 'bg-emerald-500/30 text-emerald-200' : 'bg-amber-500/30 text-amber-100',
            )}
            title={`已审视 ${confirmed} / 需 ${requiredTotal}（共 ${totalSamples} 个样本）`}
          >
            已审视 {confirmed}/{requiredTotal}
          </span>
        </div>
        <button
          onClick={onClose}
          className="p-0.5 rounded text-gray-400 hover:text-white hover:bg-white/10 flex-shrink-0"
          title="关闭提醒"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {customizeJob.newApiCode && (
        <div className="text-xs text-amber-100/80">
          新 api_code: <code className="bg-black/40 px-1.5 py-0.5 rounded text-amber-200">{customizeJob.newApiCode}</code>
        </div>
      )}

      <p className="text-xs text-amber-100/80 leading-relaxed">
        系统将启动识别优化程序，为保证识别能力能够适应更多不同的场景，请上传至少 2 个（最多 9 个）不同格式的样本，要求：
        一是识别结果，必须与当前样本文件需要识别的字段/内容/格式和输出完全一致，
        二是在内容的布局和式样上，尽量与当前样本完全不一致，且相互之间也完全不一致。
      </p>

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="application/pdf,image/png,image/jpeg,image/webp"
        onChange={(e) => void handleFilesChosen(e.target.files)}
        className="hidden"
      />

      <button
        onClick={triggerFilePicker}
        disabled={uploadingNow}
        className={cn(
          'w-full px-3 py-2 rounded-md text-sm font-medium transition-colors flex items-center justify-center gap-2',
          uploadingNow
            ? 'bg-amber-700/40 text-amber-200/60 cursor-not-allowed'
            : 'bg-amber-600 hover:bg-amber-700 text-white',
        )}
      >
        {uploadingNow ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
        {uploadingNow ? '上传中...' : '上传样本（可多选）'}
      </button>

      {(uploads.length > 0 || failedUploads.length > 0) && (
        <div className="space-y-1">
          {uploads.map((u) => (
            <div
              key={u.id}
              className={cn(
                'flex items-center justify-between gap-2 px-2 py-1 rounded text-xs',
                u.status === 'success' ? 'bg-emerald-500/10 text-emerald-200'
                : u.status === 'failed' ? 'bg-red-500/10 text-red-200'
                : u.status === 'uploading' ? 'bg-amber-500/15 text-amber-100'
                : 'bg-white/5 text-gray-300',
              )}
            >
              <div className="flex items-center gap-1.5 min-w-0 flex-1">
                {u.status === 'uploading' && <Loader2 className="w-3 h-3 animate-spin flex-shrink-0" />}
                {u.status === 'success' && <Check className="w-3 h-3 flex-shrink-0" />}
                {u.status === 'failed' && <AlertCircle className="w-3 h-3 flex-shrink-0" />}
                <span className="truncate">{u.file.name}</span>
              </div>
              {u.status === 'failed' && (
                <div className="flex items-center gap-1 flex-shrink-0">
                  <button
                    onClick={() => uploadOne(u)}
                    className="px-1.5 py-0.5 rounded bg-amber-500/30 hover:bg-amber-500/50 text-amber-100 transition-colors"
                  >
                    重试
                  </button>
                  <button
                    onClick={() => dismissFailure(u.id)}
                    className="p-0.5 rounded hover:bg-white/10"
                    title="忽略"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              )}
            </div>
          ))}
          {failedUploads.length > 1 && (
            <button
              onClick={retryFailed}
              className="w-full px-2 py-1 text-xs text-amber-200 bg-amber-500/15 hover:bg-amber-500/25 rounded transition-colors"
            >
              一键重试 {failedUploads.length} 个失败的上传
            </button>
          )}
        </div>
      )}

      <p className="text-xs text-amber-200/70 leading-relaxed">
        {stillNeeded === 0 ? (
          <>✓ 已审视样本充足，迭代将自动启动</>
        ) : totalSamples >= requiredTotal ? (
          <>
            已上传 {totalSamples} 个样本，但只有 {confirmed} 个被审视过。
            打开每个样本 → 检查 OCR 是否正确 → 点击右上"待审视"切到"已审视"。
          </>
        ) : (
          <>
            还需上传 {Math.max(0, requiredTotal - totalSamples)} 个不同格式的样本
            {remainingMinQuota > 0 && (
              <>（建议 {remainingMinQuota}–{remainingMaxQuota} 个）</>
            )}
            ，并将每个样本点为"已审视"。
          </>
        )}
      </p>
    </div>
  )
}

export function CustomizeBar() {
  const {
    fieldEditDrafts, addFieldDrafts, submitCustomize, customizeSubmitting,
    customizeJob, clearCustomizeJob,
  } = useWorkspaceStore()

  const editCount = Object.values(fieldEditDrafts).filter((d) => {
    const nameChanged = (d.originalName || '') !== d.correctedName
    const valueChanged = String(d.originalValue ?? '') !== d.correctedValue
    const fmtChanged = (d.originalFormat || '') !== d.correctedFormat
    return nameChanged || valueChanged || fmtChanged
  }).length
  const addCount = addFieldDrafts.filter((d) => d.correctedName.trim().length > 0).length
  const totalCount = editCount + addCount

  // ── Waiting for samples — verbatim instruction + inline upload ──────
  if (customizeJob && customizeJob.status === 'waiting_for_samples') {
    return (
      <WaitingForSamplesBanner customizeJob={customizeJob} onClose={clearCustomizeJob} />
    )
  }

  // ── Other in-flight phases (reflecting/forking/optimizing) ──────────
  if (customizeJob && customizeJob.status !== 'completed' && customizeJob.status !== 'failed') {
    return (
      <div className="bg-purple-500/10 border border-purple-500/30 rounded-lg p-3 space-y-2">
        <div className="flex items-center gap-2">
          <Loader2 className="w-4 h-4 text-purple-400 animate-spin" />
          <span className="text-sm text-purple-200 font-medium">{customizeJob.phaseDetail || customizeJob.status}</span>
        </div>
        <div className="flex items-center gap-1.5">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className={cn(
                'h-1.5 flex-1 rounded-full transition-colors',
                customizeJob.roundsDone > i ? 'bg-emerald-500' : 'bg-white/10',
              )}
            />
          ))}
        </div>
        <p className="text-xs text-purple-300/80">
          完成 {customizeJob.roundsDone} / {customizeJob.roundsTotal} 轮迭代
        </p>
      </div>
    )
  }
  if (customizeJob && (customizeJob.status === 'completed' || customizeJob.status === 'failed')) {
    return (
      <div
        className={cn(
          'rounded-lg p-3 space-y-2 border',
          customizeJob.status === 'completed'
            ? 'bg-emerald-500/10 border-emerald-500/30'
            : 'bg-red-500/10 border-red-500/30',
        )}
      >
        <div className="flex items-center justify-between">
          <span className={cn(
            'text-sm font-medium',
            customizeJob.status === 'completed' ? 'text-emerald-300' : 'text-red-300',
          )}>
            {customizeJob.status === 'completed' ? '✓ 已生成新模板' : '✗ 生成失败'}
          </span>
          <button
            onClick={clearCustomizeJob}
            className="p-0.5 rounded text-gray-400 hover:text-white hover:bg-white/10"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        {customizeJob.newApiCode && (
          <div className="text-xs text-gray-300">
            新 api_code: <code className="bg-black/40 px-1.5 py-0.5 rounded text-emerald-300">{customizeJob.newApiCode}</code>
          </div>
        )}
        {customizeJob.overallAccuracy !== null && customizeJob.overallAccuracy !== undefined && (
          <div className="text-xs text-gray-400">
            综合准确率：<span className="text-emerald-400">{Math.round((customizeJob.overallAccuracy || 0) * 100)}%</span>
          </div>
        )}
        {customizeJob.errorMessage && (
          <div className="text-xs text-red-300/80">{customizeJob.errorMessage}</div>
        )}
        {customizeJob.status === 'completed' && (
          customizeJob.options?.save_as_new && customizeJob.newApiDefinitionId ? (
            <div className="space-y-2">
              <p className="text-[11px] text-emerald-200/80 leading-relaxed">
                ✓ 已在新模板上完成定制与迭代优化 — 当前模板未被改动，
                仍按原 api_code 对外服务。
              </p>
              <button
                onClick={() => {
                  const newId = customizeJob.newApiDefinitionId
                  clearCustomizeJob()
                  if (newId) window.location.assign(`/workspace/api/${newId}`)
                }}
                className="w-full px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm rounded transition-colors"
              >
                前往新模板工作区
              </button>
            </div>
          ) : (
          <div className="space-y-2">
            <p className="text-[11px] text-emerald-200/80 leading-relaxed">
              ✓ 新版本已直接激活在本工作区 — 无需切换 URL。
              字段栏和 JSON 视图已更新为优化后的识别结果。
            </p>
            <button
              onClick={async () => {
                // Refresh the workspace to load the new active version's
                // modules + ProcessingResults on source's docs.
                const apiId = useWorkspaceStore.getState().apiDefinitionId
                if (apiId) {
                  await useWorkspaceStore.getState().loadApiDefinition(apiId)
                }
                clearCustomizeJob()
              }}
              className="w-full px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm rounded transition-colors"
            >
              刷新本工作区查看结果
            </button>
          </div>
          )
        )}
      </div>
    )
  }

  if (totalCount === 0) return null
  return (
    <div className="space-y-1.5">
      <button
        onClick={() => void submitCustomize()}
        disabled={customizeSubmitting}
        className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-md bg-gradient-to-r from-purple-600 to-purple-700 hover:from-purple-500 hover:to-purple-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium transition-all shadow-lg shadow-purple-500/20"
      >
        {customizeSubmitting ? (
          <><Loader2 className="w-4 h-4 animate-spin" /> 提交中...</>
        ) : (
          <><Sparkles className="w-4 h-4" /> 优化当前模板（{totalCount} 处改动 · api_code 不变）</>
        )}
      </button>
      <button
        onClick={() => {
          const name = window.prompt(
            '另存为新模板：源模板（含已发布的 API）保持不变，\n' +
            '改动将在新模板上做迭代优化，并获得独立的 api_code。\n\n' +
            '新模板名称（留空则自动命名）：',
          )
          if (name === null) return // 用户取消
          void submitCustomize({ saveAsNew: true, newName: name || undefined })
        }}
        disabled={customizeSubmitting}
        className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-md border border-purple-500/40 text-purple-300 hover:bg-purple-500/10 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-all"
      >
        <GitBranch className="w-4 h-4" /> 另存为新模板（不影响当前 API）
      </button>
    </div>
  )
}

// (CustomizeBar uses useNavigate directly; the workspace is always rendered
// inside a Router, so no fallback needed.)

// ─── Fields view ─────────────────────────────────────────────────────────────
