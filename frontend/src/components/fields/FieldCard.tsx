import { useRef, useEffect } from 'react'
import { useWorkspaceStore, type Annotation, type ProcessingResult } from '../../stores/workspace-store'
import { updateAnnotation } from '../../lib/api-client'

// ─── Confidence badge ─────────────────────────────────────────────────────────

interface ConfidenceBadgeProps {
  confidence: number | undefined
  isManual?: boolean
}

function ConfidenceBadge({ confidence, isManual }: ConfidenceBadgeProps) {
  if (isManual) {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-semibold bg-gray-100 text-gray-500">
        手动
      </span>
    )
  }
  if (confidence === undefined) return null

  const label = `${Math.round(confidence)}%`

  if (confidence >= 95) {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-semibold bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">
        {label}
      </span>
    )
  }
  if (confidence >= 90) {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-semibold bg-amber-50 text-amber-700 ring-1 ring-amber-200">
        {label}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-semibold bg-red-50 text-red-700 ring-1 ring-red-200">
      {label}
    </span>
  )
}

// ─── FieldCard ────────────────────────────────────────────────────────────────

interface FieldCardProps {
  annotation: Annotation
  result?: ProcessingResult
}

export default function FieldCard({ annotation, result }: FieldCardProps) {
  const { selectedFieldId, setSelectedFieldId, updateFieldValue, documentInfo } = useWorkspaceStore()
  const isSelected = annotation.id === selectedFieldId
  const cardRef = useRef<HTMLDivElement>(null)

  const confidence = result?.confidence ?? (annotation.isManual ? -1 : undefined)
  const displayValue = String(result?.value ?? annotation.value ?? '')
  const { x, y, width, height } = annotation.boundingBox

  // T3.3 — scroll this card into view when it becomes selected
  useEffect(() => {
    if (isSelected && cardRef.current) {
      cardRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [isSelected])

  const handleClick = () => {
    setSelectedFieldId(isSelected ? null : annotation.id)
  }

  const handleValueChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    updateFieldValue(annotation.id, e.target.value)
  }

  // T3.5 — persist value to backend on blur
  const handleBlur = async (e: React.FocusEvent<HTMLInputElement>) => {
    if (!documentInfo?.id) return
    try {
      await updateAnnotation(documentInfo.id, annotation.id, { value: e.target.value })
    } catch {
      // fail silently — local state already updated
    }
  }

  return (
    <div
      ref={cardRef}
      onClick={handleClick}
      className={[
        'mx-2 mb-1.5 rounded-xl border transition-all duration-150 cursor-pointer',
        isSelected
          ? 'border-indigo-400 bg-indigo-50 shadow-sm shadow-indigo-100'
          : 'border-gray-100 bg-white hover:border-gray-200 hover:shadow-sm',
      ].join(' ')}
    >
      {/* Header row */}
      <div className="flex items-center justify-between gap-2 px-3 pt-2.5 pb-1">
        <span
          className="text-[11px] font-semibold text-gray-700 truncate"
          title={annotation.label}
        >
          {annotation.label}
        </span>
        <ConfidenceBadge
          confidence={confidence === -1 ? undefined : confidence}
          isManual={annotation.isManual}
        />
      </div>

      {/* Value input */}
      <div className="px-3 pb-2">
        <input
          type="text"
          value={displayValue}
          onChange={handleValueChange}
          onBlur={handleBlur}
          onClick={(e) => e.stopPropagation()}
          className={[
            'w-full text-xs border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 transition-colors',
            isSelected
              ? 'border-indigo-300 bg-white focus:ring-indigo-500/40 focus:border-indigo-400'
              : 'border-gray-200 bg-gray-50/80 focus:ring-indigo-500/30 focus:border-indigo-300',
          ].join(' ')}
          placeholder="(空)"
        />
      </div>

      {/* Position info */}
      <div className="px-3 pb-2.5">
        <span className="text-[9px] text-gray-400 font-mono">
          ({Math.round(x)}%, {Math.round(y)}%) | {Math.round(width)}×{Math.round(height)}
        </span>
      </div>
    </div>
  )
}
