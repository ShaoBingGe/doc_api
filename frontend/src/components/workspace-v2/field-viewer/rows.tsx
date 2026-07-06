// B2：DarkFieldViewer 拆分——叶子行/表组件（TypeSelector / FieldRow /
// NewFieldRow / ArrayTable）。纯展示，状态经 props 传入。
import { useState, useRef, useEffect, useMemo } from 'react'
import { ChevronDown, ChevronRight, Check, Square, X, Trash2, Lock, Table as TableIcon } from 'lucide-react'
import { cn } from '../../../lib/utils'
import { useWorkspaceStore, type Annotation, type ProcessingResult, type FieldEditDraft } from '../../../stores/workspace-store'
import { FIELD_TYPES, LOW_CONFIDENCE_THRESHOLD, type ArrayGroup } from './shared'

// ─── Type selector ───────────────────────────────────────────────────────────

export function TypeSelector({
  fieldType,
  onChange,
  locked = false,
}: {
  fieldType: string
  onChange: (t: string) => void
  /** Country-locked field — type is governed by the country spec; show a
   *  static, non-editable chip with a lock icon. */
  locked?: boolean
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

  if (locked) {
    return (
      <span
        className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-white/5 text-gray-500 cursor-not-allowed"
        title="国家锁定字段：类型由国家规范管控，不可修改"
      >
        <Lock className="w-2.5 h-2.5" />
        {display}
      </span>
    )
  }

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

export function FieldRow({
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
  onSaveType,
  onConfirmConfidence,
  onStartDrawing,
  isDrawingThis,
  locked = false,
}: {
  annotation: Annotation
  result?: ProcessingResult
  isHovered: boolean
  isSelected: boolean
  /** Country-locked field (regulatory): no rename / retype / delete / edit. */
  locked?: boolean
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
        if (locked) return  // 国家锁定字段：禁止进入编辑面板（改名/改类型/删除）
        onStartEdit(annotation.id)
      }}
      onMouseEnter={() => onHover(annotation.id)}
      onMouseLeave={() => onHover(null)}
      title={locked
        ? '国家锁定字段：识别规则由国家规范（Part 1）管控，不可增删改'
        : hasDirtyDraft ? '已有待提交修改，双击查看' : '点击聚焦，双击进入字段编辑面板'}
    >
      <div className="flex items-center gap-3 flex-1 min-w-0">
        {/* Label + (optional rename history subtitle) */}
        <div className="flex flex-col w-28 flex-shrink-0 min-w-0">
          <span className="text-gray-400 text-sm truncate flex items-center gap-1">
            {locked && (
              <Lock
                className="w-3 h-3 text-amber-400/80 flex-shrink-0"
                aria-label="国家锁定字段"
              />
            )}
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
        {/* Type selector (static + locked for country-governed fields) */}
        <TypeSelector
          fieldType={annotation.fieldType}
          onChange={(t) => onSaveType(annotation.id, t)}
          locked={locked}
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

        {/* Delete button (visible on hover; hidden for country-locked fields) */}
        {!locked && (
          <button
            onClick={() => onDeleteField(annotation.id)}
            className="opacity-0 group-hover:opacity-100 p-1 rounded text-gray-500 hover:text-red-400 hover:bg-red-500/10 transition-all"
            title="删除此字段（所有样本 + 优化器同步生效）"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}

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

export function NewFieldRow({
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

export function ArrayTable({
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
  // 多行明细 P2 — 列级结构编辑（改名/删列/加列，全部样本级联生效）。
  // 只对顶层数组开放（`$[*].arr[*]` 模块）；嵌套路径（含 [ 或 .）不支持。
  const commitArrayColumn = useWorkspaceStore((s) => s.commitArrayColumn)
  const canEditColumns = !!group.arrayPath
    && !group.arrayPath.includes('[') && !group.arrayPath.includes('.')
  const [renamingCol, setRenamingCol] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [addingCol, setAddingCol] = useState(false)
  const [newColName, setNewColName] = useState('')
  const [newColType, setNewColType] = useState('string')

  const commitRename = (col: string) => {
    const nv = renameValue.trim()
    setRenamingCol(null)
    if (nv && nv !== col) void commitArrayColumn(group.arrayPath, 'rename', col, { newName: nv })
  }
  const commitAdd = () => {
    const nm = newColName.trim()
    setAddingCol(false)
    setNewColName('')
    if (nm) void commitArrayColumn(group.arrayPath, 'add', nm, { colType: newColType })
  }

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
                    className="px-2 py-2 text-left font-medium border-b border-white/10 whitespace-nowrap group/th"
                  >
                    {renamingCol === col ? (
                      <input
                        autoFocus
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onBlur={() => commitRename(col)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') commitRename(col)
                          if (e.key === 'Escape') setRenamingCol(null)
                        }}
                        className="bg-transparent border-b border-cyan-400 text-gray-200 outline-none w-24 px-0.5"
                      />
                    ) : (
                      <span className="inline-flex items-center gap-1">
                        {col}
                        {canEditColumns && (
                          <span className="opacity-0 group-hover/th:opacity-100 transition-opacity inline-flex items-center gap-0.5">
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                setRenamingCol(col)
                                setRenameValue(col)
                              }}
                              className="p-0.5 rounded text-gray-500 hover:text-cyan-400 hover:bg-cyan-500/10"
                              title="重命名此列（全部样本生效）"
                            >
                              ✎
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                if (confirm(`删除列「${col}」？该列在所有样本的数据与识别规则将一并移除。`)) {
                                  void commitArrayColumn(group.arrayPath, 'delete', col)
                                }
                              }}
                              className="p-0.5 rounded text-gray-500 hover:text-red-400 hover:bg-red-500/10"
                              title="删除此列（全部样本生效）"
                            >
                              <X className="w-3 h-3 inline" />
                            </button>
                          </span>
                        )}
                      </span>
                    )}
                  </th>
                ))}
                {canEditColumns && (
                  <th className="px-2 py-2 text-left font-medium border-b border-white/10 whitespace-nowrap w-28">
                    {addingCol ? (
                      <span className="inline-flex items-center gap-1">
                        <input
                          autoFocus
                          value={newColName}
                          onChange={(e) => setNewColName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') commitAdd()
                            if (e.key === 'Escape') { setAddingCol(false); setNewColName('') }
                          }}
                          placeholder="列名"
                          className="bg-transparent border-b border-cyan-400 text-gray-200 outline-none w-20 px-0.5"
                        />
                        <select
                          value={newColType}
                          onChange={(e) => setNewColType(e.target.value)}
                          className="bg-[#1e1e24] border border-white/10 rounded text-gray-200 outline-none text-[10px] px-0.5 py-0.5"
                        >
                          {['string', 'number', 'date', 'boolean'].map((t) => (
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </select>
                        <button onClick={commitAdd} className="p-0.5 text-emerald-400" title="确认">
                          <Check className="w-3 h-3 inline" />
                        </button>
                      </span>
                    ) : (
                      <button
                        onClick={() => setAddingCol(true)}
                        className="text-cyan-400/70 hover:text-cyan-300 text-[11px]"
                        title="新增一列（保存生成后识别）"
                      >
                        + 列
                      </button>
                    )}
                  </th>
                )}
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
                    {canEditColumns && <td className="border-b border-white/5" />}
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

