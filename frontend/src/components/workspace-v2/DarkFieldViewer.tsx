import { useState, useRef, useEffect, useCallback } from 'react'
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
} from 'lucide-react'
import { cn } from '../../lib/utils'
import { useWorkspaceStore, type Annotation, type ProcessingResult } from '../../stores/workspace-store'
import { toast } from '../../lib/toast'

const LOW_CONFIDENCE_THRESHOLD = 85

type ViewTab = 'fields' | 'rules' | 'stats'

const FIELD_TYPES = ['text', 'number', 'date', 'boolean', 'array'] as const

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
  onHover,
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
  onHover: (id: string | null) => void
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
        'group flex items-center justify-between py-2 px-3 -mx-3 rounded cursor-default transition-colors',
        isHovered ? 'bg-purple-500/20' : 'hover:bg-white/5',
      )}
      onMouseEnter={() => onHover(annotation.id)}
      onMouseLeave={() => onHover(null)}
    >
      <div className="flex items-center gap-3 flex-1 min-w-0">
        {/* Label (double-click to edit) */}
        <EditableCell
          value={annotation.label}
          onSave={(v) => onSaveLabel(annotation.id, v)}
          className="text-gray-400 text-sm w-28 flex-shrink-0 truncate"
          placeholder="字段名"
        />

        {/* Value (double-click to edit) */}
        <EditableCell
          value={value === null || value === undefined ? '' : String(value)}
          onSave={(v) => onSaveValue(annotation.id, v)}
          className="text-gray-200 text-sm truncate max-w-[140px]"
          placeholder="输入值"
        />
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
            title="在文档上画框标注此字段位置"
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

// ─── Fields view ─────────────────────────────────────────────────────────────

function FieldsView() {
  const {
    annotations, processingResults, hoveredFieldId, setHoveredFieldId,
    documentInfo, removeAnnotation, updateFieldValue,
    saveAnnotation, deleteAnnotationRemote,
    addPendingField, drawingFieldId, setDrawingFieldId,
  } = useWorkspaceStore()
  const resultMap = new Map(processingResults.map((r) => [r.annotationId, r]))

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
    toast.info('在左侧文档上拖拽画框，按 Esc 取消')
  }, [setDrawingFieldId])

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      {/* Add field + Skill bar */}
      <div className="flex gap-2">
        <button
          onClick={() => setAddingField(true)}
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
            <span className="text-xs text-gray-500 ml-2">{annotations.length} 字段</span>
          </div>
          {expanded.basic
            ? <ChevronDown className="w-4 h-4 text-gray-500" />
            : <ChevronRight className="w-4 h-4 text-gray-500" />}
        </button>

        {expanded.basic && (
          <div className="p-3 space-y-1">
            {annotations.length === 0 ? (
              <p className="text-xs text-gray-500 text-center py-4">暂无字段，等待文档处理完成</p>
            ) : (
              annotations.map((ann) => (
                <FieldRow
                  key={ann.id}
                  annotation={ann}
                  result={resultMap.get(ann.id)}
                  isHovered={hoveredFieldId === ann.id}
                  onHover={setHoveredFieldId}
                  onDeleteField={handleDeleteField}
                  onSaveLabel={handleSaveLabel}
                  onSaveValue={handleSaveValue}
                  onSaveType={handleSaveType}
                  onConfirmConfidence={handleConfirmConfidence}
                  onStartDrawing={handleStartDrawing}
                  isDrawingThis={drawingFieldId === ann.id}
                />
              ))
            )}
          </div>
        )}
      </div>

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
