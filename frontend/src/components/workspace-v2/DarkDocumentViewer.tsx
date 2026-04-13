import { useState, useCallback, useRef, useEffect } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'
import { Search, ZoomIn, ZoomOut, Upload, ChevronLeft, ChevronRight, FileText, AlertCircle } from 'lucide-react'
import { cn } from '../../lib/utils'
import { useWorkspaceStore, type Annotation, type ProcessingResult } from '../../stores/workspace-store'

pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`

// ─── Bbox overlay for dark theme ─────────────────────────────────────────────

interface BboxLayerProps {
  annotations: Annotation[]
  results: ProcessingResult[]
  selectedFieldId: string | null
  hoveredFieldId: string | null
  onSelect: (id: string | null) => void
  onHover: (id: string | null) => void
  onUpdateBbox: (id: string, bbox: Annotation['boundingBox']) => void
}

function BboxLayer({ annotations, results, selectedFieldId, hoveredFieldId, onSelect, onHover, onUpdateBbox }: BboxLayerProps) {
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
    [],
  )

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
    <div ref={containerRef} className="absolute inset-0 pointer-events-none" style={{ zIndex: 10 }}>
      {annotations.map((ann) => {
        const result = resultMap.get(ann.id)
        const confidence = result?.confidence ?? -1
        const isSelected = ann.id === selectedFieldId
        const isHovered = ann.id === hoveredFieldId
        const { x, y, width, height } = ann.boundingBox

        let borderColor = 'border-emerald-500'
        let labelBg = 'bg-emerald-500'
        if (confidence < 90) {
          borderColor = 'border-red-500'
          labelBg = 'bg-red-500'
        } else if (confidence < 95) {
          borderColor = 'border-amber-500'
          labelBg = 'bg-amber-500'
        }

        return (
          <div
            key={ann.id}
            className={cn(
              'absolute border-2 pointer-events-auto transition-all duration-150',
              isSelected || isHovered
                ? 'border-purple-500 bg-purple-500/20 ring-2 ring-purple-500/50'
                : `${borderColor} hover:bg-white/10`,
            )}
            style={{
              left: `${x}%`,
              top: `${y}%`,
              width: `${width}%`,
              height: `${height}%`,
              cursor: isSelected ? (isDragging ? 'grabbing' : 'grab') : 'pointer',
              transition: isDragging && isSelected ? 'none' : undefined,
            }}
            onClick={(e) => {
              e.stopPropagation()
              if (!isDragging) onSelect(isSelected ? null : ann.id)
            }}
            onMouseEnter={() => onHover(ann.id)}
            onMouseLeave={() => onHover(null)}
            onMouseDown={(e) => {
              if (isSelected) startDrag(e, ann, 'move')
            }}
          >
            {/* Label tag */}
            <div
              className={cn(
                'absolute -top-5 left-0 px-1.5 py-0.5 text-[9px] font-semibold text-white leading-none rounded-t whitespace-nowrap max-w-[120px] overflow-hidden text-ellipsis',
                isSelected || isHovered ? 'bg-purple-500' : labelBg,
              )}
            >
              {ann.label}
            </div>

            {/* Resize handle */}
            {isSelected && (
              <div
                className="absolute bottom-0 right-0 w-2 h-2 bg-purple-500 cursor-nwse-resize rounded-sm"
                style={{ transform: 'translate(50%, 50%)' }}
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

// ─── Main component ──────────────────────────────────────────────────────────

export default function DarkDocumentViewer() {
  const {
    documentInfo,
    annotations,
    processingResults,
    selectedFieldId,
    hoveredFieldId,
    setSelectedFieldId,
    setHoveredFieldId,
    updateFieldBbox,
  } = useWorkspaceStore()

  const [numPages, setNumPages] = useState<number>(0)
  const [page, setPage] = useState(1)
  const [pdfError, setPdfError] = useState(false)
  const [zoom, setZoom] = useState(100)

  const handleLoadSuccess = useCallback(({ numPages }: { numPages: number }) => {
    setNumPages(numPages)
    setPdfError(false)
  }, [])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelectedFieldId(null)
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [setSelectedFieldId])

  const visibleAnnotations = annotations.filter((a) => a.page === page)
  const pageWidth = Math.round(680 * (zoom / 100))

  return (
    <div className="flex flex-col h-full bg-[#18181c] border-r border-white/10">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/10 text-gray-400 text-sm">
        <div className="flex items-center gap-4">
          <Search className="w-4 h-4 cursor-pointer hover:text-white transition-colors" />
          <div className="flex items-center gap-2">
            <ZoomOut
              className="w-4 h-4 cursor-pointer hover:text-white transition-colors"
              onClick={() => setZoom((z) => Math.max(50, z - 10))}
            />
            <span className="text-xs w-8 text-center">{zoom}%</span>
            <ZoomIn
              className="w-4 h-4 cursor-pointer hover:text-white transition-colors"
              onClick={() => setZoom((z) => Math.min(200, z + 10))}
            />
          </div>
          {numPages > 1 && (
            <div className="flex items-center gap-2 border-l border-white/10 pl-4">
              <ChevronLeft
                className={cn('w-4 h-4 cursor-pointer', page <= 1 ? 'opacity-30' : 'hover:text-white')}
                onClick={() => page > 1 && setPage(page - 1)}
              />
              <span className="text-xs">第 {page} 页</span>
              <ChevronRight
                className={cn('w-4 h-4 cursor-pointer', page >= numPages ? 'opacity-30' : 'hover:text-white')}
                onClick={() => page < numPages && setPage(page + 1)}
              />
            </div>
          )}
        </div>
        <button className="flex items-center gap-2 px-3 py-1.5 border border-white/20 hover:bg-white/5 rounded text-white transition-colors text-xs">
          <Upload className="w-3.5 h-3.5" />
          上传新文档
        </button>
      </div>

      {/* Document area */}
      <div
        className="flex-1 overflow-auto p-8 flex justify-center bg-[#1e1e24]"
        onClick={() => setSelectedFieldId(null)}
      >
        {!documentInfo ? (
          <div className="flex flex-col items-center justify-center gap-3">
            <FileText className="w-12 h-12 text-gray-600" />
            <p className="text-sm text-gray-500">正在加载文档...</p>
          </div>
        ) : documentInfo.fileType === 'image' ? (
          <div className="relative inline-block shadow-2xl rounded-lg overflow-hidden">
            <img
              src={documentInfo.fileUrl}
              alt={documentInfo.filename}
              className="block"
              style={{ width: pageWidth }}
              draggable={false}
            />
            <BboxLayer
              annotations={visibleAnnotations}
              results={processingResults}
              selectedFieldId={selectedFieldId}
              hoveredFieldId={hoveredFieldId}
              onSelect={setSelectedFieldId}
              onHover={setHoveredFieldId}
              onUpdateBbox={updateFieldBbox}
            />
          </div>
        ) : pdfError ? (
          <div className="flex flex-col items-center justify-center w-[680px] h-[900px] gap-3 bg-[#2a2a32] rounded-lg">
            <AlertCircle className="w-8 h-8 text-red-400" />
            <p className="text-sm text-gray-400">无法渲染 PDF</p>
          </div>
        ) : (
          <div className="relative shadow-2xl rounded-lg overflow-hidden bg-white">
            <Document
              file={documentInfo.fileUrl}
              onLoadSuccess={handleLoadSuccess}
              onLoadError={() => setPdfError(true)}
              loading={
                <div className="flex items-center justify-center" style={{ width: pageWidth, height: pageWidth * 1.3 }}>
                  <div className="flex flex-col items-center gap-2">
                    <div className="w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full animate-spin" />
                    <p className="text-sm text-gray-400">加载 PDF...</p>
                  </div>
                </div>
              }
            >
              <div className="relative">
                <Page pageNumber={page} width={pageWidth} />
                <BboxLayer
                  annotations={visibleAnnotations}
                  results={processingResults}
                  selectedFieldId={selectedFieldId}
                  hoveredFieldId={hoveredFieldId}
                  onSelect={setSelectedFieldId}
                  onHover={setHoveredFieldId}
                  onUpdateBbox={updateFieldBbox}
                />
              </div>
            </Document>
          </div>
        )}
      </div>
    </div>
  )
}
