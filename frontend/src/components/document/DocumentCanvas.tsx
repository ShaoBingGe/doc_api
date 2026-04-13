import { useState, useCallback, useRef, useEffect } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'
import { ChevronLeft, ChevronRight, FileText, AlertCircle } from 'lucide-react'
import { useWorkspaceStore, type Annotation, type ProcessingResult } from '../../stores/workspace-store'

// Use CDN worker — avoids Vite bundling issues with pdfjs
pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`

// ─── Confidence helpers ───────────────────────────────────────────────────────

type ConfidenceColors = {
  border: string
  bg: string
  labelBg: string
  selectedBorder: string
  selectedFill: string
}

function confidenceColors(confidence: number | undefined): ConfidenceColors {
  if (confidence === undefined || confidence < 0) {
    return {
      border: 'border-gray-400',
      bg: '',
      labelBg: 'bg-gray-400',
      selectedBorder: 'border-indigo-500',
      selectedFill: 'bg-indigo-500/15',
    }
  }
  if (confidence >= 95) {
    return {
      border: 'border-emerald-500',
      bg: '',
      labelBg: 'bg-emerald-500',
      selectedBorder: 'border-indigo-500',
      selectedFill: 'bg-indigo-500/15',
    }
  }
  if (confidence >= 90) {
    return {
      border: 'border-amber-500',
      bg: '',
      labelBg: 'bg-amber-500',
      selectedBorder: 'border-indigo-500',
      selectedFill: 'bg-indigo-500/15',
    }
  }
  return {
    border: 'border-red-500',
    bg: '',
    labelBg: 'bg-red-500',
    selectedBorder: 'border-indigo-500',
    selectedFill: 'bg-indigo-500/15',
  }
}

// ─── Bbox overlay ─────────────────────────────────────────────────────────────

interface BboxLayerProps {
  annotations: Annotation[]
  results: ProcessingResult[]
  selectedFieldId: string | null
  onSelect: (id: string | null) => void
  onUpdateBbox: (id: string, bbox: Annotation['boundingBox']) => void
}

function BboxLayer({ annotations, results, selectedFieldId, onSelect, onUpdateBbox }: BboxLayerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const dragState = useRef<{
    type: 'move' | 'resize'
    annotationId: string
    startX: number
    startY: number
    origBbox: Annotation['boundingBox']
  } | null>(null)
  const [isDragging, setIsDragging] = useState(false)

  const resultMap = new Map(results.map((r) => [r.annotationId, r]))

  // Start drag or resize
  const startDrag = useCallback(
    (e: React.MouseEvent, ann: Annotation, type: 'move' | 'resize') => {
      e.preventDefault()
      dragState.current = {
        type,
        annotationId: ann.id,
        startX: e.clientX,
        startY: e.clientY,
        origBbox: { ...ann.boundingBox },
      }
      setIsDragging(true)
    },
    []
  )

  // Document-level mouse handlers during drag
  useEffect(() => {
    if (!isDragging) return

    const handleMove = (e: MouseEvent) => {
      if (!dragState.current || !containerRef.current) return
      const { type, annotationId, startX, startY, origBbox } = dragState.current
      const rect = containerRef.current.getBoundingClientRect()
      const dx = ((e.clientX - startX) / rect.width) * 100
      const dy = ((e.clientY - startY) / rect.height) * 100

      if (type === 'move') {
        onUpdateBbox(annotationId, {
          ...origBbox,
          x: Math.max(0, Math.min(100 - origBbox.width, origBbox.x + dx)),
          y: Math.max(0, Math.min(100 - origBbox.height, origBbox.y + dy)),
        })
      } else {
        // resize: adjust width / height, enforce minimums
        onUpdateBbox(annotationId, {
          ...origBbox,
          width: Math.max(5, origBbox.width + dx),
          height: Math.max(2, origBbox.height + dy),
        })
      }
    }

    const handleUp = () => {
      dragState.current = null
      setIsDragging(false)
    }

    document.addEventListener('mousemove', handleMove)
    document.addEventListener('mouseup', handleUp)
    return () => {
      document.removeEventListener('mousemove', handleMove)
      document.removeEventListener('mouseup', handleUp)
    }
  }, [isDragging, onUpdateBbox])

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 pointer-events-none"
      style={{ zIndex: 10 }}
    >
      {annotations.map((ann) => {
        const result = resultMap.get(ann.id)
        const confidence = result?.confidence ?? (ann.isManual ? -1 : undefined)
        const colors = confidenceColors(confidence)
        const isSelected = ann.id === selectedFieldId
        const { x, y, width, height } = ann.boundingBox

        return (
          <div
            key={ann.id}
            className={[
              'absolute border-2 pointer-events-auto',
              isSelected
                ? `${colors.selectedBorder} ${colors.selectedFill}`
                : `${colors.border} hover:bg-white/10`,
            ].join(' ')}
            style={{
              left: `${x}%`,
              top: `${y}%`,
              width: `${width}%`,
              height: `${height}%`,
              cursor: isSelected ? (isDragging ? 'grabbing' : 'grab') : 'pointer',
              transition: isDragging && isSelected ? 'none' : 'all 0.15s ease',
            }}
            onClick={(e) => {
              e.stopPropagation()
              if (!isDragging) onSelect(isSelected ? null : ann.id)
            }}
            onMouseDown={(e) => {
              if (isSelected) startDrag(e, ann, 'move')
            }}
          >
            {/* Label tag top-left */}
            <div
              className={[
                'absolute -top-5 left-0 px-1.5 py-0.5 text-[9px] font-semibold text-white leading-none rounded-t whitespace-nowrap max-w-[120px] overflow-hidden text-ellipsis',
                isSelected ? 'bg-indigo-500' : colors.labelBg,
              ].join(' ')}
            >
              {ann.label}
            </div>

            {/* Resize handle — bottom-right corner, visible when selected */}
            {isSelected && (
              <div
                style={{
                  position: 'absolute',
                  bottom: 0,
                  right: 0,
                  width: 8,
                  height: 8,
                  background: '#6366f1',
                  cursor: 'nwse-resize',
                  transform: 'translate(50%, 50%)',
                  borderRadius: 2,
                }}
                onMouseDown={(e) => {
                  e.stopPropagation()
                  startDrag(e, ann, 'resize')
                }}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ─── Page nav ─────────────────────────────────────────────────────────────────

interface PageNavProps {
  page: number
  totalPages: number
  onPrev: () => void
  onNext: () => void
}

function PageNav({ page, totalPages, onPrev, onNext }: PageNavProps) {
  if (totalPages <= 1) return null
  return (
    <div className="flex items-center gap-2 justify-center py-2 bg-white/90 backdrop-blur-sm border-t border-gray-200 rounded-b-xl">
      <button
        onClick={onPrev}
        disabled={page <= 1}
        className="p-1 rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        <ChevronLeft className="w-4 h-4" />
      </button>
      <span className="text-xs text-gray-600 tabular-nums">
        {page} / {totalPages}
      </span>
      <button
        onClick={onNext}
        disabled={page >= totalPages}
        className="p-1 rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        <ChevronRight className="w-4 h-4" />
      </button>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function DocumentCanvas() {
  const {
    documentInfo,
    annotations,
    processingResults,
    selectedFieldId,
    setSelectedFieldId,
    updateFieldBbox,
  } = useWorkspaceStore()

  const [numPages, setNumPages] = useState<number>(0)
  const [page, setPage] = useState(1)
  const [pdfError, setPdfError] = useState(false)

  const handleLoadSuccess = useCallback(({ numPages }: { numPages: number }) => {
    setNumPages(numPages)
    setPdfError(false)
  }, [])

  const handleError = useCallback(() => {
    setPdfError(true)
  }, [])

  // Escape key clears selection (T3.3)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelectedFieldId(null)
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [setSelectedFieldId])

  // Filter annotations for current page
  const visibleAnnotations = annotations.filter((a) => a.page === page)

  // ── Placeholder when no document ──────────────────────────────────────────
  if (!documentInfo) {
    return (
      <div className="flex flex-col h-full items-center justify-center bg-gray-50">
        <div className="bg-white shadow-lg rounded-2xl w-full max-w-2xl mx-6 aspect-[8.5/11] flex flex-col items-center justify-center gap-3 border border-gray-100">
          <FileText className="w-12 h-12 text-gray-200" />
          <p className="text-sm text-gray-400">正在加载文档…</p>
        </div>
      </div>
    )
  }

  // ── Image render ──────────────────────────────────────────────────────────
  if (documentInfo.fileType === 'image') {
    return (
      <div
        className="flex flex-col h-full items-center justify-center bg-gray-50 overflow-auto p-6"
        onClick={() => setSelectedFieldId(null)}
      >
        <div className="relative inline-block shadow-xl rounded-xl overflow-hidden">
          <img
            src={documentInfo.fileUrl}
            alt={documentInfo.filename}
            className="block max-w-full"
            draggable={false}
          />
          <BboxLayer
            annotations={visibleAnnotations}
            results={processingResults}
            selectedFieldId={selectedFieldId}
            onSelect={setSelectedFieldId}
            onUpdateBbox={updateFieldBbox}
          />
        </div>
      </div>
    )
  }

  // ── PDF render ────────────────────────────────────────────────────────────
  return (
    <div
      className="flex flex-col h-full items-center justify-start bg-gray-50 overflow-auto"
      onClick={() => setSelectedFieldId(null)}
    >
      <div className="flex-1 flex items-start justify-center w-full p-6">
        <div className="relative shadow-xl rounded-xl overflow-hidden bg-white">
          {pdfError ? (
            <div className="flex flex-col items-center justify-center w-[680px] h-[900px] gap-3 bg-white">
              <AlertCircle className="w-8 h-8 text-red-400" />
              <p className="text-sm text-gray-500">无法渲染 PDF</p>
              <p className="text-xs text-gray-400 font-mono">{documentInfo.fileUrl}</p>
            </div>
          ) : (
            <Document
              file={documentInfo.fileUrl}
              onLoadSuccess={handleLoadSuccess}
              onLoadError={handleError}
              loading={
                <div className="flex items-center justify-center w-[680px] h-[900px]">
                  <div className="flex flex-col items-center gap-2">
                    <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                    <p className="text-sm text-gray-400">加载 PDF…</p>
                  </div>
                </div>
              }
            >
              <div className="relative">
                <Page
                  pageNumber={page}
                  width={680}
                />
                <BboxLayer
                  annotations={visibleAnnotations}
                  results={processingResults}
                  selectedFieldId={selectedFieldId}
                  onSelect={setSelectedFieldId}
                  onUpdateBbox={updateFieldBbox}
                />
              </div>
            </Document>
          )}
        </div>
      </div>

      <div className="w-full max-w-[680px] pb-4">
        <PageNav
          page={page}
          totalPages={numPages}
          onPrev={() => setPage((p) => Math.max(1, p - 1))}
          onNext={() => setPage((p) => Math.min(numPages, p + 1))}
        />
      </div>
    </div>
  )
}
