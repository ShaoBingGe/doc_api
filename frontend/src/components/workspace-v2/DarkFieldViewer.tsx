import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ChevronDown,
  ChevronRight,
  FileText,
  List,
  Plus,
  Link as LinkIcon,
  CheckCircle2,
  AlertCircle,
  BarChart2,
  Trash2,
  Check,
  Square,
  X,
  Loader2,
  Sparkles,
  Table as TableIcon,
} from 'lucide-react'
import { cn } from '../../lib/utils'
import { useWorkspaceStore, type Annotation, type FieldEditDraft, type ProcessingResult } from '../../stores/workspace-store'
import { toast } from '../../lib/toast'

const FORMAT_OPTIONS = ['string', 'number', 'date', 'boolean', 'array'] as const

const LOW_CONFIDENCE_THRESHOLD = 85

type ViewTab = 'fields' | 'rules' | 'stats'

const FIELD_TYPES = ['text', 'number', 'date', 'boolean', 'array'] as const

// ─── Array detection ────────────────────────────────────────────────────────
//
// The store flattens nested structured_data into labels like
// `detailOfGoodsOrServices[0].articleName`. To render the array as a table we
// match this pattern and bucket annotations by their array path + index.

const ARRAY_LABEL_RE = /^(.*?)\[(\d+)\](?:\.(.+))?$/

interface ArrayCell {
  annotation: Annotation
  result?: ProcessingResult
}

interface ArrayGroup {
  /** Path of the array itself, e.g. "detailOfGoodsOrServices" or "items[0].nested" */
  arrayPath: string
  /** Column keys in first-appearance order across all rows */
  columns: string[]
  /** Row index → column key → cell */
  rows: Map<number, Map<string, ArrayCell>>
}

function groupAnnotations(
  annotations: Annotation[],
  resultMap: Map<string, ProcessingResult>,
): { scalars: Annotation[]; arrays: ArrayGroup[] } {
  const scalars: Annotation[] = []
  const arraysByPath = new Map<string, ArrayGroup>()

  for (const ann of annotations) {
    const m = ann.label.match(ARRAY_LABEL_RE)
    if (!m) {
      scalars.push(ann)
      continue
    }
    const [, arrayPath, idxStr, fieldName] = m
    const idx = Number(idxStr)
    const colKey = fieldName ?? '(value)'

    let group = arraysByPath.get(arrayPath)
    if (!group) {
      group = { arrayPath, columns: [], rows: new Map() }
      arraysByPath.set(arrayPath, group)
    }
    if (!group.columns.includes(colKey)) group.columns.push(colKey)

    let row = group.rows.get(idx)
    if (!row) {
      row = new Map()
      group.rows.set(idx, row)
    }
    row.set(colKey, { annotation: ann, result: resultMap.get(ann.id) })
  }

  return { scalars, arrays: Array.from(arraysByPath.values()) }
}

// ─── Editable cell ───────────────────────────────────────────────────────────

function EditableCell({
  value,
  onSave,
  className,
  placeholder = '',
}: {
  value: string
  onSave: (v: string) => void
  className?: string
  placeholder?: string
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) {
      setDraft(value)
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [editing]) // eslint-disable-line react-hooks/exhaustive-deps

  const commit = () => {
    setEditing(false)
    const trimmed = draft.trim()
    if (trimmed !== value) onSave(trimmed)
  }

  const cancel = () => {
    setEditing(false)
    setDraft(value)
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') cancel()
        }}
        onBlur={commit}
        className={cn(
          'bg-[#18181c] border border-blue-500/60 rounded px-1.5 py-0.5 text-sm text-white outline-none',
          className,
        )}
        placeholder={placeholder}
      />
    )
  }

  return (
    <span
      onDoubleClick={() => setEditing(true)}
      className={cn('cursor-text select-none', className)}
      title="双击编辑"
    >
      {value || <span className="text-gray-600">{placeholder || '—'}</span>}
    </span>
  )
}

// ─── Type selector ───────────────────────────────────────────────────────────

function TypeSelector({
  fieldType,
  onChange,
}: {
  fieldType: string
  onChange: (t: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const display =
    fieldType === 'number' ? 'number'
    : fieldType === 'date' ? 'date'
    : fieldType === 'boolean' ? 'bool'
    : fieldType === 'array' ? 'array'
    : 'string'

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs px-2 py-0.5 rounded-full bg-white/10 text-gray-400 hover:bg-white/20 transition-colors cursor-pointer"
      >
        {display}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-30 bg-[#2a2a32] border border-white/10 rounded-lg shadow-xl py-1 min-w-[100px]">
          {FIELD_TYPES.map((t) => (
            <button
              key={t}
              onClick={() => { onChange(t); setOpen(false) }}
              className={cn(
                'w-full text-left px-3 py-1.5 text-xs hover:bg-white/10 transition-colors',
                t === fieldType ? 'text-purple-400' : 'text-gray-300',
              )}
            >
              {t}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Field row ───────────────────────────────────────────────────────────────

function FieldRow({
  annotation,
  result,
  isHovered,
  isSelected,
  hasDirtyDraft,
  editedOnOtherDocs,
  isRenamedFrom,
  isValueModifiedHere,
  onHover,
  onSelect,
  onStartEdit,
  onDeleteField,
  onSaveLabel,
  onSaveValue,
  onSaveType,
  onConfirmConfidence,
  onStartDrawing,
  isDrawingThis,
}: {
  annotation: Annotation
  result?: ProcessingResult
  isHovered: boolean
  isSelected: boolean
  /** True when this field has a pending customer edit not yet submitted. */
  hasDirtyDraft: boolean
  /** design v8 — true when this field's value was modified on a DIFFERENT
   *  sample doc of the same ApiDef. Shows a soft cyan badge. */
  editedOnOtherDocs?: boolean
  /** design v8 — when this field was renamed (the current label is the
   *  NEW name), carries the OLD name for tooltip + subtitle display. */
  isRenamedFrom?: string | null
  /** design v8 — true when this field's VALUE was modified on THIS doc
   *  and persisted to pending_edits.modifications. Shows purple badge. */
  isValueModifiedHere?: boolean
  onHover: (id: string | null) => void
  onSelect: (id: string | null) => void
  onStartEdit: (id: string) => void
  onDeleteField: (id: string) => void
  onSaveLabel: (id: string, label: string) => void
  onSaveValue: (id: string, value: string) => void
  onSaveType: (id: string, type: string) => void
  onConfirmConfidence: (id: string) => void
  onStartDrawing: (id: string) => void
  isDrawingThis: boolean
}) {
  const value = result?.value ?? annotation.value ?? ''
  const confidence = result?.confidence ?? 0
  const isLowConfidence = confidence < LOW_CONFIDENCE_THRESHOLD
  const isConfirmed = confidence >= 100
  const showDrawIcon = isLowConfidence && !isConfirmed

  return (
    <div
      className={cn(
        'group flex items-center justify-between py-2 px-3 -mx-3 rounded cursor-pointer transition-colors',
        // Border-left strip + amber tint when this field has an unsaved
        // customer edit. Selection / hover state still wins for the bg.
        hasDirtyDraft && 'border-l-2 border-amber-400 pl-[10px]',
        isSelected
          ? 'bg-purple-500/30 ring-1 ring-purple-500/40'
          : isHovered
          ? 'bg-purple-500/20'
          : hasDirtyDraft
          ? 'bg-amber-500/10 hover:bg-amber-500/15'
          : 'hover:bg-white/5',
      )}
      onClick={() => onSelect(isSelected ? null : annotation.id)}
      onDoubleClick={(e) => {
        e.stopPropagation()
        onStartEdit(annotation.id)
      }}
      onMouseEnter={() => onHover(annotation.id)}
      onMouseLeave={() => onHover(null)}
      title={hasDirtyDraft ? '已有待提交修改，双击查看' : '点击聚焦，双击进入字段编辑面板'}
    >
      <div className="flex items-center gap-3 flex-1 min-w-0">
        {/* Label + (optional rename history subtitle) */}
        <div className="flex flex-col w-28 flex-shrink-0 min-w-0">
          <span className="text-gray-400 text-sm truncate">
            {annotation.label}
          </span>
          {isRenamedFrom && (
            <span
              className="text-[10px] text-emerald-400/70 truncate"
              title={`原命名: ${isRenamedFrom}（已重命名为 ${annotation.label}）`}
            >
              原: {isRenamedFrom}
            </span>
          )}
        </div>

        {/* Value */}
        <span className={cn(
          'text-sm truncate max-w-[140px]',
          hasDirtyDraft ? 'text-amber-200' : 'text-gray-200',
        )}>
          {value === null || value === undefined || value === ''
            ? <span className="text-gray-600">—</span>
            : String(value)}
        </span>
        {/* Badge priority: 已暂存 > 已重命名 > 已保存修改 > 其他文件已修改 */}
        {hasDirtyDraft && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 font-medium flex-shrink-0">
            已暂存
          </span>
        )}
        {!hasDirtyDraft && isRenamedFrom && (
          <span
            className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-medium flex-shrink-0"
            title={`已从 ${isRenamedFrom} 重命名为 ${annotation.label}（全局生效）`}
          >
            已重命名
          </span>
        )}
        {!hasDirtyDraft && !isRenamedFrom && isValueModifiedHere && (
          <span
            className="text-[10px] px-1.5 py-0.5 rounded-full bg-purple-500/20 text-purple-300 font-medium flex-shrink-0"
            title="本样本已修改此字段值，等待客户化提交"
          >
            已保存修改
          </span>
        )}
        {!hasDirtyDraft && !isRenamedFrom && !isValueModifiedHere && editedOnOtherDocs && (
          <span
            className="text-[10px] px-1.5 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 font-medium flex-shrink-0"
            title="此字段在其他样本中已被修改"
          >
            其他文件已修改
          </span>
        )}
      </div>

      <div className="flex items-center gap-2 flex-shrink-0">
        {/* Type selector */}
        <TypeSelector
          fieldType={annotation.fieldType}
          onChange={(t) => onSaveType(annotation.id, t)}
        />

        {/* Confidence bar + confirm button */}
        <div className="flex items-center gap-1.5 w-20">
          {isLowConfidence && !isConfirmed ? (
            <button
              onClick={() => onConfirmConfidence(annotation.id)}
              className="flex items-center gap-1 text-xs text-amber-400 hover:text-emerald-400 bg-amber-500/10 hover:bg-emerald-500/10 px-1.5 py-0.5 rounded transition-colors"
              title="确认此字段值正确"
            >
              <Check className="w-3 h-3" />
              确认
            </button>
          ) : (
            <>
              <div className="h-1 flex-1 bg-white/10 rounded-full overflow-hidden">
                <div
                  className={cn(
                    'h-full rounded-full',
                    confidence >= 95 ? 'bg-emerald-500'
                    : confidence >= 85 ? 'bg-amber-500'
                    : 'bg-red-500',
                  )}
                  style={{ width: `${Math.min(confidence, 100)}%` }}
                />
              </div>
              <span className={cn(
                'text-xs',
                isConfirmed ? 'text-emerald-400'
                : confidence >= 95 ? 'text-emerald-400'
                : confidence >= 85 ? 'text-amber-400'
                : 'text-red-400',
              )}>
                {isConfirmed ? <Check className="w-3 h-3 inline" /> : `${Math.round(confidence)}%`}
              </span>
            </>
          )}
        </div>

        {/* Delete button (visible on hover) */}
        <button
          onClick={() => onDeleteField(annotation.id)}
          className="opacity-0 group-hover:opacity-100 p-1 rounded text-gray-500 hover:text-red-400 hover:bg-red-500/10 transition-all"
          title="删除字段"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>

        {/* Draw bbox button (low-confidence only, visible on hover) */}
        {showDrawIcon && (
          <button
            onClick={() => onStartDrawing(annotation.id)}
            className={cn(
              'p-1 rounded transition-all',
              isDrawingThis
                ? 'opacity-100 text-purple-400 bg-purple-500/20'
                : 'opacity-0 group-hover:opacity-100 text-gray-500 hover:text-purple-400 hover:bg-purple-500/10',
            )}
            title="在文档上点击标注此字段位置"
          >
            <Square className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}

// ─── New field row (name + value, queues field, stays open for next entry) ──

function NewFieldRow({
  onAdd,
  onClose,
}: {
  onAdd: (name: string, value: string) => void
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const nameRef = useRef<HTMLInputElement>(null)

  useEffect(() => { nameRef.current?.focus() }, [])

  const commit = () => {
    const trimmedName = name.trim()
    if (!trimmedName) return
    onAdd(trimmedName, value.trim())
    setName('')
    setValue('')
    setTimeout(() => nameRef.current?.focus(), 0)
  }

  return (
    <div className="flex items-center gap-3 py-2 px-3 -mx-3 rounded bg-blue-500/10 border border-blue-500/20">
      <input
        ref={nameRef}
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="字段名"
        className="bg-transparent border-b border-white/20 text-sm text-gray-200 w-28 flex-shrink-0 outline-none focus:border-blue-400 px-0.5 py-0.5"
        onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') onClose() }}
      />
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="值（可空，留给 AI 识别）"
        className="bg-transparent border-b border-white/20 text-sm text-gray-200 flex-1 min-w-0 outline-none focus:border-blue-400 px-0.5 py-0.5"
        onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') onClose() }}
      />
      <div className="flex items-center gap-1 flex-shrink-0">
        <button
          onClick={commit}
          disabled={!name.trim()}
          className="p-1 rounded text-emerald-400 hover:bg-emerald-500/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          title="暂存（Enter）"
        >
          <Check className="w-4 h-4" />
        </button>
        <button
          onClick={onClose}
          className="p-1 rounded text-gray-400 hover:bg-white/10 transition-colors"
          title="收起（Esc）"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

// ─── Array table (renders detailOfGoodsOrServices etc.) ────────────────────

function ArrayTable({
  group,
  hoveredFieldId,
  selectedFieldId,
  fieldEditDrafts,
  onHover,
  onSelect,
  onStartEdit,
}: {
  group: ArrayGroup
  hoveredFieldId: string | null
  selectedFieldId: string | null
  fieldEditDrafts: Record<string, FieldEditDraft>
  onHover: (id: string | null) => void
  onSelect: (id: string | null) => void
  onStartEdit: (annotationId: string) => void
}) {
  const [expanded, setExpanded] = useState(true)
  const sortedIndices = useMemo(
    () => Array.from(group.rows.keys()).sort((a, b) => a - b),
    [group.rows],
  )

  return (
    <div className="bg-[#2a2a32] rounded-lg border border-white/5 overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between p-3 bg-white/5 hover:bg-white/10 transition-colors"
      >
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded bg-cyan-500/20 flex items-center justify-center text-cyan-400">
            <TableIcon className="w-3.5 h-3.5" />
          </div>
          <span className="text-sm font-medium text-gray-200">
            {group.arrayPath || '明细'}
          </span>
          <span className="text-xs text-gray-500 ml-2">
            {sortedIndices.length} 行 · {group.columns.length} 列
          </span>
        </div>
        {expanded ? (
          <ChevronDown className="w-4 h-4 text-gray-500" />
        ) : (
          <ChevronRight className="w-4 h-4 text-gray-500" />
        )}
      </button>

      {expanded && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="bg-[#1e1e24] text-gray-400">
                <th className="px-2 py-2 text-left font-medium border-b border-white/10 sticky left-0 bg-[#1e1e24] z-10 w-8">
                  #
                </th>
                {group.columns.map((col) => (
                  <th
                    key={col}
                    className="px-2 py-2 text-left font-medium border-b border-white/10 whitespace-nowrap"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedIndices.map((idx) => {
                const row = group.rows.get(idx)!
                return (
                  <tr
                    key={idx}
                    className="hover:bg-white/5 transition-colors"
                  >
                    <td className="px-2 py-1.5 text-gray-500 border-b border-white/5 sticky left-0 bg-[#2a2a32] z-10">
                      {idx + 1}
                    </td>
                    {group.columns.map((col) => {
                      const cell = row.get(col)
                      if (!cell) {
                        return (
                          <td
                            key={col}
                            className="px-2 py-1.5 text-gray-700 border-b border-white/5"
                          >
                            —
                          </td>
                        )
                      }
                      const { annotation, result } = cell
                      const value = result?.value ?? annotation.value ?? ''
                      const confidence = result?.confidence ?? 0
                      const isLow = confidence < LOW_CONFIDENCE_THRESHOLD
                      const isHovered = hoveredFieldId === annotation.id
                      const isSelected = selectedFieldId === annotation.id
                      // Dirty when this cell has its own draft (keyed by
                      // full label, e.g. `details[0].quantity`).
                      const cellDraft = fieldEditDrafts[annotation.label]
                      const hasDirtyDraft = !!cellDraft && (
                        (cellDraft.originalName || '') !== cellDraft.correctedName ||
                        String(cellDraft.originalValue ?? '') !== cellDraft.correctedValue ||
                        (cellDraft.originalFormat || '') !== cellDraft.correctedFormat
                      )
                      return (
                        <td
                          key={col}
                          className={cn(
                            'px-2 py-1.5 border-b border-white/5 cursor-pointer transition-colors',
                            hasDirtyDraft && 'border-l-2 border-l-amber-400',
                            isSelected
                              ? 'bg-purple-500/30'
                              : isHovered
                              ? 'bg-purple-500/15'
                              : hasDirtyDraft
                              ? 'bg-amber-500/10'
                              : '',
                          )}
                          onMouseEnter={() => onHover(annotation.id)}
                          onMouseLeave={() => onHover(null)}
                          onClick={() =>
                            onSelect(isSelected ? null : annotation.id)
                          }
                          onDoubleClick={(e) => {
                            // Replaces the old window.prompt — opens the
                            // same side-by-side edit panel as scalars,
                            // keyed by full label `details[N].col` so each
                            // cell gets its own draft.
                            e.stopPropagation()
                            onStartEdit(annotation.id)
                          }}
                          title={
                            hasDirtyDraft
                              ? '已暂存修改 — 双击查看'
                              : `置信度 ${Math.round(confidence)}% · 双击编辑`
                          }
                        >
                          <div className="flex items-center gap-1.5 min-w-0">
                            <div
                              className={cn(
                                'flex-1 min-w-0 max-w-[180px] truncate',
                                hasDirtyDraft ? 'text-amber-200'
                                : isLow ? 'text-amber-300'
                                : 'text-gray-200',
                              )}
                            >
                              {value === null ||
                              value === undefined ||
                              value === ''
                                ? <span className="text-gray-600">—</span>
                                : String(value)}
                            </div>
                            {hasDirtyDraft && (
                              <span className="flex-shrink-0 text-[9px] px-1 py-0.5 rounded-full bg-amber-500/20 text-amber-300 font-medium">
                                已暂存
                              </span>
                            )}
                            {isLow && !hasDirtyDraft && (
                              <span className="flex-shrink-0 text-[10px] text-amber-400">
                                {Math.round(confidence)}%
                              </span>
                            )}
                          </div>
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Pending fields list + confirm button ───────────────────────────────────

function PendingFieldsBar() {
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

function FieldEditPanel({
  annotation,
  result,
  draft,
  onUpdate,
  onCancel,
}: {
  annotation: Annotation
  result?: ProcessingResult
  draft: FieldEditDraft
  onUpdate: (patch: Partial<FieldEditDraft>) => void
  onCancel: () => void
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

        <p className="text-xs text-gray-500 leading-relaxed">
          修改自动暂存。继续编辑其他字段，全部完成后点字段列表底部的"保存并生成客户专属模板"按钮统一启动迭代优化。
          原模板不变，会另外 fork 出一个带新 api_code 的客户专属模板。
        </p>
        <button
          onClick={onCancel}
          className="w-full px-3 py-2 rounded-md bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/40 text-purple-100 text-sm font-medium transition-colors flex items-center justify-center gap-2"
        >
          <Check className="w-4 h-4" />
          暂存修改并返回字段列表
        </button>
      </div>
    </div>
  )
}

// ─── Add field list (4-column: # / name / value / format) ─────────────────
//
// Replaces the simple inline NewFieldRow. Each row is one new field the
// customer wants the prompt to learn. The customize bar saves them all.

function AddFieldList() {
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
                <tr key={idx} className="hover:bg-white/5 transition-colors">
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

function WaitingForSamplesBanner({
  customizeJob,
  onClose,
  navigate,
}: {
  customizeJob: CustomizeJobStatus
  onClose: () => void
  navigate: ReturnType<typeof useNavigate>
}) {
  const apiDefinitionId = useWorkspaceStore((s) => s.apiDefinitionId)
  const documents = useWorkspaceStore((s) => s.documents)
  const samplesReview = useWorkspaceStore((s) => s.samplesReview)
  const addSampleDocument = useWorkspaceStore((s) => s.addSampleDocument)
  const onNewWorkspace = !!apiDefinitionId && customizeJob.newApiDefinitionId === apiDefinitionId

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploads, setUploads] = useState<PerFileUpload[]>([])

  // On the source workspace, surface a single CTA to navigate to the new fork
  if (!onNewWorkspace && customizeJob.newApiDefinitionId) {
    return (
      <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 space-y-2.5">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-amber-400 flex-shrink-0" />
            <span className="text-sm text-amber-200 font-medium">等待上传样本以启动迭代优化</span>
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
        <button
          onClick={() => navigate(`/workspace/api/${customizeJob.newApiDefinitionId}`)}
          className="w-full px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white text-sm rounded transition-colors"
        >
          前往新模板工作区上传样本 →
        </button>
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

function CustomizeBar() {
  const navigate = useNavigate()
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
      <WaitingForSamplesBanner customizeJob={customizeJob} onClose={clearCustomizeJob} navigate={navigate} />
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
        {customizeJob.status === 'completed' && customizeJob.newApiDefinitionId && (
          <button
            onClick={() => {
              navigate(`/workspace/api/${customizeJob.newApiDefinitionId}`)
              clearCustomizeJob()
            }}
            className="w-full px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm rounded transition-colors"
          >
            打开新模板工作区 →
          </button>
        )}
      </div>
    )
  }

  if (totalCount === 0) return null
  return (
    <button
      onClick={() => void submitCustomize()}
      disabled={customizeSubmitting}
      className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-md bg-gradient-to-r from-purple-600 to-purple-700 hover:from-purple-500 hover:to-purple-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium transition-all shadow-lg shadow-purple-500/20"
    >
      {customizeSubmitting ? (
        <><Loader2 className="w-4 h-4 animate-spin" /> 提交中...</>
      ) : (
        <><Sparkles className="w-4 h-4" /> 保存并生成客户专属模板（{totalCount} 处改动）</>
      )}
    </button>
  )
}

// (CustomizeBar uses useNavigate directly; the workspace is always rendered
// inside a Router, so no fallback needed.)

// ─── Fields view ─────────────────────────────────────────────────────────────

function FieldsView() {
  const {
    annotations, processingResults, hoveredFieldId, setHoveredFieldId,
    selectedFieldId, setSelectedFieldId,
    documentInfo, removeAnnotation, updateFieldValue,
    saveAnnotation, deleteAnnotationRemote,
    addPendingField, drawingFieldId, setDrawingFieldId,
    editingFieldId, fieldEditDrafts, startEditingField, cancelEditingField,
    updateEditDraft, addNewFieldDraft, addFieldDrafts,
    pendingEdits,  // design v8 — cross-sample overlay
  } = useWorkspaceStore()

  // design v8 — derive "edited on OTHER files" set and the list of added
  // fields from OTHER files not yet present locally. See pending_edits_service.py.
  const currentDocId = documentInfo?.id
  const otherDocsEditedFields = useMemo(() => {
    if (!pendingEdits || !currentDocId) return new Set<string>()
    const out = new Set<string>()
    for (const [docId, fieldsObj] of Object.entries(pendingEdits.modifications || {})) {
      if (docId === currentDocId) continue
      for (const fname of Object.keys(fieldsObj)) out.add(fname)
    }
    return out
  }, [pendingEdits, currentDocId])

  const otherDocsAddedFields = useMemo(() => {
    if (!pendingEdits) return [] as Array<{ field_name: string; type: string; description: string }>
    const localNames = new Set(annotations.map((a) => a.label))
    return (pendingEdits.added_fields || []).filter(
      (f) => !localNames.has(f.field_name) && f.added_at_doc_id !== currentDocId,
    )
  }, [pendingEdits, annotations, currentDocId])

  // design v8 — reverse rename map {new → old} used to:
  //   - show "原: oldName" subtitle + "已重命名" badge on renamed rows
  //   - fall back to fieldEditDrafts[oldName] when looking up dirty drafts,
  //     so cascade-rename doesn't invalidate the editor's draft association
  const renamesReverse = useMemo(() => {
    const m: Record<string, string> = {}
    for (const [oldN, newN] of Object.entries(pendingEdits?.renames || {})) {
      m[newN] = oldN
    }
    return m
  }, [pendingEdits])

  // Set of field names whose value was modified on THIS doc and persisted
  // to pending_edits.modifications (used for purple "已保存修改" badge).
  const valueModifiedHere = useMemo(() => {
    if (!pendingEdits || !currentDocId) return new Set<string>()
    return new Set(Object.keys(pendingEdits.modifications?.[currentDocId] || {}))
  }, [pendingEdits, currentDocId])
  const resultMap = useMemo(
    () => new Map(processingResults.map((r) => [r.annotationId, r])),
    [processingResults],
  )

  // Bucket annotations: scalars stay in the basic-info list; anything matching
  // `path[N].field` gets pulled into a per-array table below.
  const { scalars, arrays } = useMemo(
    () => groupAnnotations(annotations, resultMap),
    [annotations, resultMap],
  )

  const [expanded, setExpanded] = useState({ basic: true, summary: true })
  const toggle = (k: keyof typeof expanded) =>
    setExpanded((p) => ({ ...p, [k]: !p[k] }))

  const [addingField, setAddingField] = useState(false)

  const docId = documentInfo?.id

  // ── Edit handlers ──────────────────────────────────────────────────────────

  const handleSaveLabel = useCallback((id: string, label: string) => {
    // Update local store label
    useWorkspaceStore.setState((state) => ({
      annotations: state.annotations.map((a) =>
        a.id === id ? { ...a, label } : a,
      ),
    }))
    // Persist to backend
    if (docId) saveAnnotation(docId, id, { field_name: label })
  }, [docId, saveAnnotation])

  const handleSaveValue = useCallback((id: string, value: string) => {
    updateFieldValue(id, value)
    if (docId) saveAnnotation(docId, id, { field_value: value })
  }, [docId, updateFieldValue, saveAnnotation])

  const handleSaveType = useCallback((id: string, type: string) => {
    useWorkspaceStore.setState((state) => ({
      annotations: state.annotations.map((a) =>
        a.id === id ? { ...a, fieldType: type as Annotation['fieldType'] } : a,
      ),
    }))
    if (docId) saveAnnotation(docId, id, { field_type: type })
  }, [docId, saveAnnotation])

  const handleConfirmConfidence = useCallback((id: string) => {
    // Set local confidence to 100
    useWorkspaceStore.setState((state) => ({
      processingResults: state.processingResults.map((r) =>
        r.annotationId === id ? { ...r, confidence: 100 } : r,
      ),
    }))
    // Persist: mark as confirmed (confidence=1.0 on backend is 0-1 scale)
    if (docId) saveAnnotation(docId, id, { confidence: 1.0 })
    toast.success('字段已确认')
  }, [docId, saveAnnotation])

  const handleDeleteField = useCallback((id: string) => {
    if (!docId) {
      removeAnnotation(id)
      return
    }
    deleteAnnotationRemote(docId, id)
    toast.success('字段已删除')
  }, [docId, removeAnnotation, deleteAnnotationRemote])

  const handleQueueField = useCallback((name: string, value: string) => {
    addPendingField(name, value)
    toast.info(value ? `已暂存：${name} = ${value}` : `已暂存：${name}`)
  }, [addPendingField])

  const handleStartDrawing = useCallback((id: string) => {
    setDrawingFieldId(id)
    toast.info('在左侧文档上点击字段位置，按 Esc 取消')
  }, [setDrawingFieldId])

  // Find the currently-editing annotation + its draft, if any
  const editingAnnotation = editingFieldId
    ? annotations.find((a) => a.id === editingFieldId)
    : null
  // Draft key = full label (same as startEditingField).
  const editingDraftKey = editingAnnotation ? editingAnnotation.label : null
  const editingDraft = editingDraftKey ? fieldEditDrafts[editingDraftKey] : undefined

  // ── Edit-mode view: show the panel + customize bar + nothing else ─────
  if (editingAnnotation && editingDraft) {
    return (
      <div className="flex-1 overflow-auto p-4 space-y-4">
        <FieldEditPanel
          annotation={editingAnnotation}
          result={resultMap.get(editingAnnotation.id)}
          draft={editingDraft}
          onUpdate={(patch) => updateEditDraft(editingDraftKey!, patch)}
          onCancel={cancelEditingField}
        />
        <CustomizeBar />
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      {/* Customize job progress / save CTA — lives at the top so it's always visible */}
      <CustomizeBar />

      {/* Add field list (4-column) */}
      {addFieldDrafts.length > 0 && <AddFieldList />}

      {/* Add field + Skill bar */}
      <div className="flex gap-2">
        <button
          onClick={() => { addNewFieldDraft(); setAddingField(false) }}
          className="flex-1 flex items-center gap-2 px-3 py-1.5 text-sm text-purple-400 bg-purple-500/10 border border-purple-500/20 rounded-md hover:bg-purple-500/20 transition-colors justify-center"
        >
          <Plus className="w-4 h-4" />
          添加识别字段
        </button>
        <button
          onClick={() => toast.info('Skill 功能即将上线 (Coming Soon)')}
          className="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-400 bg-white/5 border border-white/10 rounded-md hover:bg-white/10 hover:text-purple-300 transition-colors"
          title="挂载可复用的 OCR 技能（Coming Soon）"
        >
          <Plus className="w-4 h-4" />
          Skill
        </button>
      </div>

      {/* Inline new field row (queues; stays open for multiple entries) */}
      {addingField && (
        <NewFieldRow
          onAdd={handleQueueField}
          onClose={() => setAddingField(false)}
        />
      )}

      {/* Pending fields + confirm button */}
      <PendingFieldsBar />

      {/* Basic info section */}
      <div className="bg-[#2a2a32] rounded-lg border border-white/5 overflow-hidden">
        <button
          onClick={() => toggle('basic')}
          className="w-full flex items-center justify-between p-3 bg-white/5 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-purple-500/20 flex items-center justify-center text-purple-400">
              <FileText className="w-3.5 h-3.5" />
            </div>
            <span className="text-sm font-medium text-gray-200">基本信息</span>
            <span className="text-xs text-gray-500 ml-2">{scalars.length} 字段</span>
          </div>
          {expanded.basic
            ? <ChevronDown className="w-4 h-4 text-gray-500" />
            : <ChevronRight className="w-4 h-4 text-gray-500" />}
        </button>

        {expanded.basic && (
          <div className="p-3 space-y-1">
            {scalars.length === 0 ? (
              <p className="text-xs text-gray-500 text-center py-4">暂无字段，等待文档处理完成</p>
            ) : (
              scalars.map((ann) => {
                // design v8: lookup drafts by current label OR by the
                // pre-rename old name (so cascade-rename doesn't make
                // the editor's draft association vanish).
                const oldName = renamesReverse[ann.label]
                const d = fieldEditDrafts[ann.label] ?? (oldName ? fieldEditDrafts[oldName] : undefined)
                const hasDirtyDraft = !!d && (
                  (d.originalName || '') !== d.correctedName ||
                  String(d.originalValue ?? '') !== d.correctedValue ||
                  (d.originalFormat || '') !== d.correctedFormat
                )
                return (
                  <FieldRow
                    key={ann.id}
                    annotation={ann}
                    result={resultMap.get(ann.id)}
                    isHovered={hoveredFieldId === ann.id}
                    isSelected={selectedFieldId === ann.id}
                    hasDirtyDraft={hasDirtyDraft}
                    editedOnOtherDocs={otherDocsEditedFields.has(ann.label)}
                    isRenamedFrom={oldName ?? null}
                    isValueModifiedHere={valueModifiedHere.has(ann.label)}
                    onHover={setHoveredFieldId}
                    onSelect={setSelectedFieldId}
                    onStartEdit={startEditingField}
                    onDeleteField={handleDeleteField}
                    onSaveLabel={handleSaveLabel}
                    onSaveValue={handleSaveValue}
                    onSaveType={handleSaveType}
                    onConfirmConfidence={handleConfirmConfidence}
                    onStartDrawing={handleStartDrawing}
                    isDrawingThis={drawingFieldId === ann.id}
                  />
                )
              })
            )}
          </div>
        )}
      </div>

      {/* Array tables (detailOfGoodsOrServices, detailOfTaxSummary, ...) */}
      {arrays.map((group) => (
        <ArrayTable
          key={group.arrayPath}
          group={group}
          hoveredFieldId={hoveredFieldId}
          selectedFieldId={selectedFieldId}
          fieldEditDrafts={fieldEditDrafts}
          onHover={setHoveredFieldId}
          onSelect={setSelectedFieldId}
          onStartEdit={startEditingField}
        />
      ))}

      {/* design v8 — fields added on OTHER files, missing in this doc.
          Rendered as empty rows with a cyan "其他文件新增" badge so the user
          can manually annotate them on this sample. */}
      {otherDocsAddedFields.length > 0 && (
        <div className="bg-[#2a2a32] rounded-lg border border-cyan-500/20 overflow-hidden">
          <div className="w-full flex items-center justify-between p-3 bg-cyan-500/10">
            <div className="flex items-center gap-2">
              <div className="w-6 h-6 rounded bg-cyan-500/20 flex items-center justify-center text-cyan-300">
                <Plus className="w-3.5 h-3.5" />
              </div>
              <span className="text-sm font-medium text-gray-200">其他文件已新增字段</span>
              <span className="text-xs text-gray-500 ml-2">{otherDocsAddedFields.length} 字段待补充</span>
            </div>
          </div>
          <div className="p-3 space-y-2">
            <p className="text-[11px] text-gray-500 leading-relaxed">
              下列字段在其他样本中已新增。本样本若也有此字段，请手工补充值；如本样本确无此字段，可留空。
            </p>
            {otherDocsAddedFields.map((f) => (
              <div
                key={f.field_name}
                className="flex items-center justify-between py-1.5 px-2 rounded bg-cyan-500/5 border border-cyan-500/15"
                title={f.description || '其他样本中新增的字段'}
              >
                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <span className="text-gray-300 text-sm w-32 truncate">{f.field_name}</span>
                  <span className="text-gray-600 text-xs">—（待标注）</span>
                </div>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 font-medium flex-shrink-0">
                  其他文件新增
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Summary section */}
      <div className="bg-[#2a2a32] rounded-lg border border-white/5 overflow-hidden">
        <button
          onClick={() => toggle('summary')}
          className="w-full flex items-center justify-between p-3 bg-white/5 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-amber-500/20 flex items-center justify-center text-amber-400">
              <LinkIcon className="w-3.5 h-3.5" />
            </div>
            <span className="text-sm font-medium text-gray-200">汇总信息</span>
          </div>
          {expanded.summary
            ? <ChevronDown className="w-4 h-4 text-gray-500" />
            : <ChevronRight className="w-4 h-4 text-gray-500" />}
        </button>

        {expanded.summary && (
          <div className="p-3 divide-y divide-white/5">
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-gray-400">字段总数</span>
              <span className="text-gray-200">{annotations.length}</span>
            </div>
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-gray-400">平均置信度</span>
              <span className="text-emerald-400">
                {processingResults.length > 0
                  ? Math.round(processingResults.reduce((s, r) => s + r.confidence, 0) / processingResults.length)
                  : 0}%
              </span>
            </div>
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-gray-400">高置信字段</span>
              <span className="text-emerald-400">
                {processingResults.filter((r) => r.confidence >= 95).length}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-gray-400">需复核字段</span>
              <span className="text-red-400">
                {processingResults.filter((r) => r.confidence < 85).length}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-gray-400">手动添加</span>
              <span className="text-blue-400">
                {annotations.filter((a) => a.isManual).length}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Validation rules view ───────────────────────────────────────────────────

function RulesView() {
  const { annotations, processingResults } = useWorkspaceStore()
  const resultMap = new Map(processingResults.map((r) => [r.annotationId, r]))

  const checks = annotations.map((ann) => {
    const r = resultMap.get(ann.id)
    const val = r?.value ?? ann.value
    const isEmpty = val === null || val === undefined || val === ''
    const isLowConf = (r?.confidence ?? 0) < 85
    const isNumericInvalid = ann.fieldType === 'number' && val != null && val !== '' && isNaN(Number(val))
    const status = isEmpty ? 'error' : isNumericInvalid ? 'error' : isLowConf ? 'warning' : 'ok'
    const message = isEmpty ? '值为空'
      : isNumericInvalid ? `值 "${val}" 不是有效数字`
      : isLowConf ? `置信度偏低 (${Math.round(r?.confidence ?? 0)}%)`
      : `置信度 ${Math.round(r?.confidence ?? 0)}%`
    return { id: ann.id, label: ann.label, status, message }
  })

  return (
    <div className="flex-1 overflow-auto p-4 space-y-3">
      <p className="text-xs text-gray-500">自动校验提取字段的完整性与置信度</p>
      {checks.length === 0 ? (
        <p className="text-xs text-gray-500 text-center py-8">暂无字段数据</p>
      ) : (
        checks.map((c) => (
          <div
            key={c.id}
            className={cn(
              'flex items-center gap-3 p-3 rounded-lg border text-sm',
              c.status === 'ok'      ? 'bg-emerald-500/5 border-emerald-500/20'
              : c.status === 'warning' ? 'bg-amber-500/5 border-amber-500/20'
              : 'bg-red-500/5 border-red-500/20',
            )}
          >
            {c.status === 'ok'
              ? <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
              : c.status === 'warning'
              ? <AlertCircle className="w-4 h-4 text-amber-400 flex-shrink-0" />
              : <AlertCircle className="w-4 h-4 text-red-400 flex-shrink-0" />}
            <div className="flex-1 min-w-0">
              <p className="text-gray-300 font-medium truncate">{c.label}</p>
              <p className="text-xs text-gray-500 truncate">{c.message}</p>
            </div>
          </div>
        ))
      )}
    </div>
  )
}

// ─── Stats view ──────────────────────────────────────────────────────────────

function StatsView() {
  const { annotations, processingResults } = useWorkspaceStore()

  const total = processingResults.length
  const high = processingResults.filter((r) => r.confidence >= 95).length
  const med  = processingResults.filter((r) => r.confidence >= 85 && r.confidence < 95).length
  const low  = processingResults.filter((r) => r.confidence < 85).length
  const avg  = total > 0
    ? Math.round(processingResults.reduce((s, r) => s + r.confidence, 0) / total)
    : 0
  const confirmed = processingResults.filter((r) => r.confidence >= 100).length
  const manual = annotations.filter((a) => a.isManual).length

  const bars = [
    { label: '高置信 >=95%', count: high, color: 'bg-emerald-500', textColor: 'text-emerald-400' },
    { label: '中置信 85-95%', count: med,  color: 'bg-amber-500',   textColor: 'text-amber-400' },
    { label: '低置信 <85%',  count: low,  color: 'bg-red-500',     textColor: 'text-red-400' },
  ]

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      {/* KPI cards */}
      <div className="grid grid-cols-2 gap-3">
        {[
          { label: '总字段数', value: annotations.length, icon: List, color: 'text-blue-400' },
          { label: '平均置信度', value: `${avg}%`, icon: BarChart2, color: 'text-emerald-400' },
          { label: '已确认', value: confirmed, icon: CheckCircle2, color: 'text-purple-400' },
          { label: '手动添加', value: manual, icon: Plus, color: 'text-cyan-400' },
        ].map((kpi) => {
          const Icon = kpi.icon
          return (
            <div key={kpi.label} className="bg-[#2a2a32] rounded-lg p-4 border border-white/5">
              <div className="flex items-center gap-2 mb-2">
                <Icon className={cn('w-4 h-4', kpi.color)} />
                <span className="text-xs text-gray-500">{kpi.label}</span>
              </div>
              <p className={cn('text-2xl font-bold', kpi.color)}>{kpi.value}</p>
            </div>
          )
        })}
      </div>

      {/* Distribution */}
      <div className="bg-[#2a2a32] rounded-lg border border-white/5 p-4 space-y-3">
        <p className="text-xs font-medium text-gray-400 uppercase tracking-wide">置信度分布</p>
        {bars.map((b) => (
          <div key={b.label} className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">{b.label}</span>
              <span className={b.textColor}>{b.count} 字段</span>
            </div>
            <div className="h-2 bg-white/10 rounded-full overflow-hidden">
              <div
                className={cn('h-full rounded-full transition-all', b.color)}
                style={{ width: total > 0 ? `${(b.count / total) * 100}%` : '0%' }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Main component ──────────────────────────────────────────────────────────

export default function DarkFieldViewer({ activeTab = 'fields' }: { activeTab?: ViewTab }) {
  return (
    <div className="flex flex-col h-full bg-[#1e1e24] border-r border-white/10">
      {activeTab === 'fields' && <FieldsView />}
      {activeTab === 'rules'  && <RulesView />}
      {activeTab === 'stats'  && <StatsView />}
    </div>
  )
}
