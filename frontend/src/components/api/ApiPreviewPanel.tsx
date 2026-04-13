import { useMemo, useState } from 'react'
import { useWorkspaceStore, type Annotation, type ProcessingResult } from '../../stores/workspace-store'

// ─── Format generators ────────────────────────────────────────────────────────

type FormatMode = 'flat' | 'detailed' | 'grouped'

function buildFlat(
  annotations: Annotation[],
  results: ProcessingResult[]
): Record<string, unknown> {
  const resultMap = new Map(results.map((r) => [r.annotationId, r]))
  const data: Record<string, unknown> = {}
  for (const ann of annotations) {
    const r = resultMap.get(ann.id)
    data[ann.label] = r?.value ?? ann.value ?? null
  }
  return { data }
}

function buildDetailed(
  annotations: Annotation[],
  results: ProcessingResult[]
): Record<string, unknown> {
  const resultMap = new Map(results.map((r) => [r.annotationId, r]))
  const fields = annotations.map((ann) => {
    const r = resultMap.get(ann.id)
    return {
      key: ann.label,
      label: ann.label,
      value: r?.value ?? ann.value ?? null,
      confidence: r ? `${Math.round(r.confidence)}%` : ann.isManual ? '手动' : null,
      position: {
        x: `${Math.round(ann.boundingBox.x)}%`,
        y: `${Math.round(ann.boundingBox.y)}%`,
        width: `${Math.round(ann.boundingBox.width)}%`,
        height: `${Math.round(ann.boundingBox.height)}%`,
      },
    }
  })
  return { fields }
}

// Keyword-based grouping for financial documents
const GROUP_KEYWORDS: Record<string, string[]> = {
  vendor: ['vendor', 'supplier', 'seller', 'company', 'from', 'issued', 'name', 'address', '供应商', '卖方'],
  financial: ['amount', 'total', 'subtotal', 'tax', 'price', 'cost', 'fee', 'sum', '金额', '总计', '税'],
  payment: ['payment', 'bank', 'account', 'swift', 'iban', 'due', '付款', '银行'],
  dates: ['date', 'period', 'issued', 'expiry', '日期', '期间'],
}

function classifyKey(key: string): string {
  const lower = key.toLowerCase()
  for (const [group, keywords] of Object.entries(GROUP_KEYWORDS)) {
    if (keywords.some((kw) => lower.includes(kw))) return group
  }
  return 'other'
}

function buildGrouped(
  annotations: Annotation[],
  results: ProcessingResult[]
): Record<string, unknown> {
  const resultMap = new Map(results.map((r) => [r.annotationId, r]))
  const groups: Record<string, Record<string, unknown>> = {}

  for (const ann of annotations) {
    const r = resultMap.get(ann.id)
    const value = r?.value ?? ann.value ?? null
    const group = classifyKey(ann.label)
    if (!groups[group]) groups[group] = {}
    groups[group][ann.label] = value
  }

  return { data: groups }
}

// ─── Syntax highlighter (minimal) ────────────────────────────────────────────

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function colorizeJson(json: string): string {
  // escapeHtml first to prevent XSS; note: " is not affected so regex still matches
  const safe = escapeHtml(json)
  return safe
    .replace(/("(?:[^"\\]|\\.)*")\s*:/g, '<span class="text-sky-300">$1</span>:')
    .replace(/:\s*("(?:[^"\\]|\\.)*")/g, ': <span class="text-emerald-300">$1</span>')
    .replace(/:\s*(\d+\.?\d*)/g, ': <span class="text-amber-300">$1</span>')
    .replace(/:\s*(true|false|null)/g, ': <span class="text-rose-300">$1</span>')
}

// ─── Main component ───────────────────────────────────────────────────────────

const FORMAT_LABELS: { key: FormatMode; label: string }[] = [
  { key: 'flat', label: '扁平' },
  { key: 'detailed', label: '详细' },
  { key: 'grouped', label: '分组' },
]

export default function ApiPreviewPanel() {
  const { annotations, processingResults } = useWorkspaceStore()
  const [format, setFormat] = useState<FormatMode>('flat')

  const jsonOutput = useMemo(() => {
    let obj: Record<string, unknown>
    if (format === 'flat') obj = buildFlat(annotations, processingResults)
    else if (format === 'detailed') obj = buildDetailed(annotations, processingResults)
    else obj = buildGrouped(annotations, processingResults)
    return JSON.stringify(obj, null, 2)
  }, [annotations, processingResults, format])

  const highlighted = useMemo(() => colorizeJson(jsonOutput), [jsonOutput])

  return (
    <div className="flex flex-col h-full">
      {/* Format toggle */}
      <div className="flex items-center gap-1 px-3 py-2.5 border-b border-gray-100 bg-gray-50">
        {FORMAT_LABELS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setFormat(key)}
            className={[
              'flex-1 py-1.5 text-xs font-semibold rounded-lg transition-all duration-150',
              format === key
                ? 'bg-white text-indigo-600 shadow-sm ring-1 ring-gray-200'
                : 'text-gray-500 hover:text-gray-700 hover:bg-white/60',
            ].join(' ')}
          >
            {label}
          </button>
        ))}
      </div>

      {/* JSON output */}
      <div className="flex-1 overflow-auto bg-gray-900 rounded-b-none">
        {annotations.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <p className="text-xs text-gray-500">暂无字段数据</p>
          </div>
        ) : (
          <pre
            className="text-[11px] leading-relaxed p-4 font-mono text-gray-100 whitespace-pre-wrap break-words"
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        )}
      </div>
    </div>
  )
}
