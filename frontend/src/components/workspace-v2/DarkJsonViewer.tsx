import { useMemo, useState } from 'react'
import { Copy, Download, Code2 } from 'lucide-react'
import { cn } from '../../lib/utils'
import { useWorkspaceStore, type Annotation, type ProcessingResult } from '../../stores/workspace-store'

// ─── JSON line component ─────────────────────────────────────────────────────

function JsonLine({
  annotationId,
  indent,
  keyName,
  value,
  isLast = false,
  isNumber = false,
  hoveredFieldId,
  onHover,
}: {
  annotationId?: string
  indent: number
  keyName: string
  value: string | number | boolean | null
  isLast?: boolean
  isNumber?: boolean
  hoveredFieldId: string | null
  onHover: (id: string | null) => void
}) {
  const isHovered = annotationId ? hoveredFieldId === annotationId : false

  return (
    <div
      className={cn(
        'font-mono text-sm py-0.5 px-4 -mx-4 cursor-default transition-colors',
        isHovered ? 'bg-purple-500/30' : 'hover:bg-white/5',
      )}
      onMouseEnter={() => annotationId && onHover(annotationId)}
      onMouseLeave={() => annotationId && onHover(null)}
      style={{ paddingLeft: `${indent * 1.5 + 1}rem` }}
    >
      <span className="text-blue-400">&quot;{keyName}&quot;</span>
      <span className="text-gray-400">: </span>
      {value === null ? (
        <span className="text-rose-300">null</span>
      ) : isNumber ? (
        <span className="text-orange-400">{value}</span>
      ) : (
        <span className="text-green-400">&quot;{String(value)}&quot;</span>
      )}
      {!isLast && <span className="text-gray-400">,</span>}
    </div>
  )
}

// ─── Build JSON structure from annotations ───────────────────────────────────

function buildJsonLines(
  annotations: Annotation[],
  results: ProcessingResult[],
): { annotationId?: string; keyName: string; value: string | number | boolean | null; isNumber: boolean }[] {
  const resultMap = new Map(results.map((r) => [r.annotationId, r]))
  return annotations.map((ann) => {
    const r = resultMap.get(ann.id)
    const val = r?.value ?? ann.value ?? null
    const isNumber = typeof val === 'number' || ann.fieldType === 'number'
    return {
      annotationId: ann.id,
      keyName: ann.label,
      value: val,
      isNumber,
    }
  })
}

// ─── Main component ──────────────────────────────────────────────────────────

type FormatMode = 'json' | 'xml' | 'csv'

export default function DarkJsonViewer() {
  const {
    annotations,
    processingResults,
    hoveredFieldId,
    setHoveredFieldId,
    apiDefinition,
  } = useWorkspaceStore()

  const [format, setFormat] = useState<FormatMode>('json')

  const lines = useMemo(
    () => buildJsonLines(annotations, processingResults),
    [annotations, processingResults],
  )

  // Full JSON string for copy
  const fullJson = useMemo(() => {
    const resultMap = new Map(processingResults.map((r) => [r.annotationId, r]))
    const obj: Record<string, unknown> = {}
    for (const ann of annotations) {
      const r = resultMap.get(ann.id)
      obj[ann.label] = r?.value ?? ann.value ?? null
    }
    return JSON.stringify(obj, null, 2)
  }, [annotations, processingResults])

  const totalLines = lines.length + 2 // opening + closing braces

  const handleCopy = () => {
    navigator.clipboard.writeText(fullJson).catch(() => {})
  }

  return (
    <div className="flex flex-col h-full bg-[#1e1e24]">
      {/* Format tabs */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
        <div className="flex items-center gap-2 bg-[#2a2a32] p-1 rounded-lg">
          {(['json', 'xml', 'csv'] as FormatMode[]).map((f) => (
            <button
              key={f}
              onClick={() => setFormat(f)}
              className={cn(
                'px-3 py-1 text-xs font-medium rounded shadow-sm transition-colors uppercase',
                format === f
                  ? 'bg-purple-600 text-white'
                  : 'text-gray-400 hover:text-gray-200',
              )}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 text-gray-400">
          <Copy
            className="w-4 h-4 cursor-pointer hover:text-white transition-colors"
            onClick={handleCopy}
          />
          <Download className="w-4 h-4 cursor-pointer hover:text-white transition-colors" />
          <Code2 className="w-4 h-4 cursor-pointer hover:text-white transition-colors" />
        </div>
      </div>

      {/* Code area */}
      <div className="flex-1 overflow-auto p-4 flex">
        {/* Line numbers */}
        <div className="flex flex-col text-right pr-4 text-gray-600 font-mono text-sm select-none border-r border-white/10 mr-4">
          {Array.from({ length: Math.max(totalLines, 20) }).map((_, i) => (
            <div key={i} className="py-0.5">
              {i + 1}
            </div>
          ))}
        </div>

        {/* Code content */}
        <div className="flex-1 min-w-0">
          {annotations.length === 0 ? (
            <div className="flex items-center justify-center h-32">
              <p className="text-xs text-gray-500">暂无字段数据</p>
            </div>
          ) : (
            <>
              <div className="font-mono text-sm text-gray-400 py-0.5">{'{'}</div>
              {lines.map((line, idx) => (
                <JsonLine
                  key={line.annotationId ?? idx}
                  annotationId={line.annotationId}
                  indent={1}
                  keyName={line.keyName}
                  value={line.value}
                  isNumber={line.isNumber}
                  isLast={idx === lines.length - 1}
                  hoveredFieldId={hoveredFieldId}
                  onHover={setHoveredFieldId}
                />
              ))}
              <div className="font-mono text-sm text-gray-400 py-0.5">{'}'}</div>
            </>
          )}
        </div>
      </div>

      {/* API Endpoint Footer */}
      {apiDefinition && (
        <div className="p-4 border-t border-white/10 bg-[#18181c]">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold text-orange-400 bg-orange-400/10 px-1.5 py-0.5 rounded">
                POST
              </span>
              <span className="text-sm text-gray-300 font-mono truncate max-w-[250px]">
                {apiDefinition.endpoint}
              </span>
            </div>
            <Copy
              className="w-3.5 h-3.5 text-gray-500 hover:text-gray-300 cursor-pointer transition-colors"
              onClick={() => navigator.clipboard.writeText(apiDefinition.endpoint).catch(() => {})}
            />
          </div>
          <div className="flex items-center justify-between text-xs text-gray-500">
            <div>
              API Code:{' '}
              <span className="text-purple-400 font-mono bg-purple-500/10 px-1 rounded">
                {apiDefinition.apiCode}
              </span>
            </div>
            <div className="flex items-center gap-1 cursor-pointer hover:text-gray-300 transition-colors">
              <Code2 className="w-3.5 h-3.5" />
              查看文档
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
