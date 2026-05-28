import { useState, useCallback, useRef, useEffect } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'
import { Search, ZoomIn, ZoomOut, Upload, ChevronLeft, ChevronRight, FileText, AlertCircle, Hand, RotateCcw } from 'lucide-react'
import { cn } from '../../lib/utils'
import { useWorkspaceStore, type Annotation, type ProcessingResult } from '../../stores/workspace-store'

pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`

// ─── Anchor point overlay ────────────────────────────────────────────────────
//
// Each field is shown as a single point (the center of its stored bbox). No
// rectangle, no resize handle — the LLM is only expected to localize a center
// position for each field. The bbox shape stays in the data model for legacy
// compatibility, but its width/height are no longer rendered.

interface AnchorLayerProps {
  annotations: Annotation[]
  results: ProcessingResult[]
  selectedFieldId: string | null
  hoveredFieldId: string | null
  onSelect: (id: string | null) => void
  onHover: (id: string | null) => void
}

function AnchorLayer({ annotations, results, selectedFieldId, hoveredFieldId, onSelect, onHover }: AnchorLayerProps) {
  const resultMap = new Map(results.map((r) => [r.annotationId, r]))

  return (
    <div className="absolute inset-0 pointer-events-none" style={{ zIndex: 10 }}>
      {annotations.map((ann) => {
        const result = resultMap.get(ann.id)
        const confidence = result?.confidence ?? -1
        const isSelected = ann.id === selectedFieldId
        const isHovered = ann.id === hoveredFieldId
        const { x, y, width, height } = ann.boundingBox
        const cx = x + width / 2
        const cy = y + height / 2

        let dotColor = 'bg-emerald-500'
        if (confidence < 90) dotColor = 'bg-red-500'
        else if (confidence < 95) dotColor = 'bg-amber-500'

        const active = isSelected || isHovered

        return (
          <div
            key={ann.id}
            className="absolute pointer-events-auto"
            style={{
              left: `${cx}%`,
              top: `${cy}%`,
              transform: 'translate(-50%, -50%)',
            }}
            onClick={(e) => {
              e.stopPropagation()
              onSelect(isSelected ? null : ann.id)
            }}
            onMouseEnter={() => onHover(ann.id)}
            onMouseLeave={() => onHover(null)}
          >
            {/* Dot */}
            <div
              className={cn(
                'rounded-full cursor-pointer transition-all duration-150',
                active
                  ? 'w-3 h-3 bg-purple-500 ring-2 ring-purple-300/70 shadow-[0_0_8px_rgba(168,85,247,0.7)]'
                  : `w-2 h-2 ${dotColor} hover:scale-150`,
              )}
            />
            {/* Label tag — only visible when active (selected or hovered) */}
            {active && (
              <div className="absolute left-1/2 top-4 -translate-x-1/2 px-1.5 py-0.5 text-[9px] font-semibold text-white leading-none rounded bg-purple-500 whitespace-nowrap max-w-[140px] overflow-hidden text-ellipsis shadow-lg">
                {ann.label}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ─── Main component ──────────────────────────────────────────────────────────

// ─── Click-to-place overlay (active when drawingFieldId is set) ──────────────
//
// Replaces the legacy drag-a-rectangle flow: the user clicks once at the
// field's location on the document, and we commit a zero-size bbox centered
// on that point (so the focus zoom can re-target it).

interface PlacePointOverlayProps {
  fieldLabel: string
  onCommit: (bbox: { x: number; y: number; width: number; height: number }) => void
  onCancel: () => void
}

function PlacePointOverlay({ fieldLabel, onCommit, onCancel }: PlacePointOverlayProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  const handleClick = (e: React.MouseEvent) => {
    if (!containerRef.current) return
    const r = containerRef.current.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width) * 100
    const y = ((e.clientY - r.top) / r.height) * 100
    onCommit({ x, y, width: 0, height: 0 })
  }

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 cursor-crosshair"
      style={{ zIndex: 30 }}
      onClick={handleClick}
    >
      <div className="absolute top-2 left-1/2 -translate-x-1/2 px-3 py-1.5 rounded-full bg-purple-600 text-white text-xs font-medium shadow-lg pointer-events-none z-10 whitespace-nowrap">
        正在为 "{fieldLabel}" 标记位置 · 单击文档上的字段中心，Esc 取消
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onCancel() }}
        onMouseDown={(e) => e.stopPropagation()}
        className="absolute top-2 right-2 px-2 py-1 rounded bg-white/10 hover:bg-white/20 text-white text-xs"
      >
        取消
      </button>
    </div>
  )
}

// ─── Field-focus zoom ────────────────────────────────────────────────────────
//
// When the user selects a field, smoothly zoom the document so the field's
// bbox sits at the visual center of the viewport at 2× scale. Switching from
// one field to another runs a two-phase animation: zoom OUT to native (1s),
// then zoom IN to the new bbox (1s). See UI_DESIGN §field-focus.
//
// Math: with the document positioned via flex-centering inside the viewport,
//   - (Lx, Ly): doc's natural top-left in viewport coords (offsetLeft/Top)
//   - (Dw, Dh): doc's pre-transform size (offsetWidth/Height)
//   - (Vw, Vh): viewport client size
//   - bbox center in doc px: (bcx, bcy) = ((bx + bw/2)% × Dw, (by + bh/2)% × Dh)
//   - target: bbox center at (Vw/2, Vh/2)
//   - transform: `translate(tx, ty) scale(S)` with origin (0,0)
//     ⇒ tx = Vw/2 − Lx − S × bcx,  ty = Vh/2 − Ly − S × bcy

const FOCUS_SCALE = 2
const FOCUS_TRANSITION_MS = 1000

export default function DarkDocumentViewer() {
  const {
    documentInfo,
    annotations,
    processingResults,
    selectedFieldId,
    hoveredFieldId,
    setSelectedFieldId,
    setHoveredFieldId,
    drawingFieldId,
    setDrawingFieldId,
    commitDrawingBbox,
    panMode,
    setPanMode,
    fieldPanOffsets,
    setFieldPanOffset,
    clearFieldPanOffset,
  } = useWorkspaceStore()

  const [numPages, setNumPages] = useState<number>(0)
  const [page, setPage] = useState(1)
  const [pdfError, setPdfError] = useState(false)
  const [zoom, setZoom] = useState(100)

  // Field-focus state
  const viewportRef = useRef<HTMLDivElement>(null)
  const docRef = useRef<HTMLDivElement>(null)
  const [focusBbox, setFocusBbox] = useState<Annotation['boundingBox'] | null>(null)
  const [docMetrics, setDocMetrics] = useState({ Lx: 0, Ly: 0, Dw: 0, Dh: 0 })
  const [viewMetrics, setViewMetrics] = useState({ Vw: 0, Vh: 0 })
  const prevSelIdRef = useRef<string | null>(null)
  const transitionTimerRef = useRef<number | null>(null)

  const handleLoadSuccess = useCallback(({ numPages }: { numPages: number }) => {
    setNumPages(numPages)
    setPdfError(false)
  }, [])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (drawingFieldId) setDrawingFieldId(null)
        else setSelectedFieldId(null)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [setSelectedFieldId, drawingFieldId, setDrawingFieldId])

  // Two-phase field-focus animation
  //   null  → A : single 1s zoom-in
  //   A     → null : single 1s zoom-out
  //   A     → B : zoom-out 1s, then zoom-in 1s
  useEffect(() => {
    if (transitionTimerRef.current) {
      window.clearTimeout(transitionTimerRef.current)
      transitionTimerRef.current = null
    }
    const prev = prevSelIdRef.current
    const next = selectedFieldId
    prevSelIdRef.current = next

    const nextAnn = next ? annotations.find((a) => a.id === next) : null
    const nextBbox = nextAnn?.boundingBox ?? null

    if (prev && next && prev !== next && nextBbox) {
      setFocusBbox(null)
      transitionTimerRef.current = window.setTimeout(() => {
        setFocusBbox(nextBbox)
        transitionTimerRef.current = null
      }, FOCUS_TRANSITION_MS)
    } else {
      setFocusBbox(nextBbox)
    }
  }, [selectedFieldId, annotations])

  useEffect(() => {
    return () => {
      if (transitionTimerRef.current) window.clearTimeout(transitionTimerRef.current)
    }
  }, [])

  // Measure doc + viewport (pre-transform), refresh on resize / zoom / page swap
  useEffect(() => {
    const update = () => {
      if (docRef.current) {
        setDocMetrics({
          Lx: docRef.current.offsetLeft,
          Ly: docRef.current.offsetTop,
          Dw: docRef.current.offsetWidth,
          Dh: docRef.current.offsetHeight,
        })
      }
      if (viewportRef.current) {
        setViewMetrics({
          Vw: viewportRef.current.clientWidth,
          Vh: viewportRef.current.clientHeight,
        })
      }
    }
    update()
    const ro = new ResizeObserver(update)
    if (docRef.current) ro.observe(docRef.current)
    if (viewportRef.current) ro.observe(viewportRef.current)
    window.addEventListener('resize', update)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', update)
    }
  }, [documentInfo, zoom, page, numPages])

  const visibleAnnotations = annotations.filter((a) => a.page === page)
  const pageWidth = Math.round(680 * (zoom / 100))
  const drawingAnn = drawingFieldId ? annotations.find((a) => a.id === drawingFieldId) : null
  const isDrawing = !!drawingAnn

  // ── Pan offset (extra translate on top of focus zoom) ────────────────
  // Per-field offset stored in the store; mutated by drag-to-pan. The
  // offset is applied AFTER the focus translate, so the user's adjustments
  // stay anchored to that field's center.
  const activePanOffset =
    selectedFieldId && fieldPanOffsets[selectedFieldId]
      ? fieldPanOffsets[selectedFieldId]
      : { dx: 0, dy: 0 }
  // Live drag delta during an in-progress pan gesture (not yet committed).
  const [liveDrag, setLiveDrag] = useState<{ dx: number; dy: number } | null>(null)
  const dragStartRef = useRef<{ x: number; y: number; baseDx: number; baseDy: number } | null>(null)

  const focusTransform = (() => {
    if (!focusBbox) return 'none'
    const { Lx, Ly, Dw, Dh } = docMetrics
    const { Vw, Vh } = viewMetrics
    if (Dw === 0 || Vw === 0) return 'none'
    const bcx = ((focusBbox.x + focusBbox.width / 2) / 100) * Dw
    const bcy = ((focusBbox.y + focusBbox.height / 2) / 100) * Dh
    const tx = Vw / 2 - Lx - FOCUS_SCALE * bcx
    const ty = Vh / 2 - Ly - FOCUS_SCALE * bcy
    const panDx = (liveDrag?.dx ?? activePanOffset.dx)
    const panDy = (liveDrag?.dy ?? activePanOffset.dy)
    return `translate(${tx + panDx}px, ${ty + panDy}px) scale(${FOCUS_SCALE})`
  })()

  // ── Drag-to-pan handlers ─────────────────────────────────────────────
  const handlePanMouseDown = (e: React.MouseEvent) => {
    if (!panMode || !focusBbox || !selectedFieldId) return
    e.preventDefault()
    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      baseDx: activePanOffset.dx,
      baseDy: activePanOffset.dy,
    }
    setLiveDrag({ dx: activePanOffset.dx, dy: activePanOffset.dy })
  }

  useEffect(() => {
    if (!liveDrag || !dragStartRef.current) return
    const onMove = (e: MouseEvent) => {
      if (!dragStartRef.current) return
      const { x, y, baseDx, baseDy } = dragStartRef.current
      setLiveDrag({ dx: baseDx + (e.clientX - x), dy: baseDy + (e.clientY - y) })
    }
    const onUp = () => {
      if (liveDrag && selectedFieldId) {
        setFieldPanOffset(selectedFieldId, liveDrag)
      }
      dragStartRef.current = null
      setLiveDrag(null)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [liveDrag, selectedFieldId, setFieldPanOffset])

  const resetPan = () => {
    if (selectedFieldId) clearFieldPanOffset(selectedFieldId)
  }

  return (
    <div className={cn(
      'flex flex-col h-full bg-[#18181c] border-r border-white/10 relative transition-all duration-200',
      isDrawing && 'ring-2 ring-purple-500/60 brightness-110',
    )}>
      {/* Dim-rest-of-app overlay when drawing (covers viewport outside this panel via fixed positioning) */}
      {isDrawing && (
        <div
          className="fixed inset-0 bg-black/60 pointer-events-none"
          style={{ zIndex: 20 }}
        />
      )}
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/10 text-gray-400 text-sm relative z-30">
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
          {/* Pan tool — only meaningful when a field is focused (i.e. zoomed in). */}
          <div className="flex items-center gap-2 border-l border-white/10 pl-4">
            <button
              onClick={() => setPanMode(!panMode)}
              disabled={!selectedFieldId}
              title={
                !selectedFieldId
                  ? '先点选一个字段再使用抓手'
                  : panMode ? '关闭抓手（再次点击退出）' : '开启抓手 — 拖动图像调整可视区域'
              }
              className={cn(
                'p-1 rounded transition-colors',
                !selectedFieldId
                  ? 'text-gray-600 cursor-not-allowed'
                  : panMode
                  ? 'bg-purple-500/30 text-purple-200'
                  : 'hover:bg-white/10 text-gray-400 hover:text-white',
              )}
            >
              <Hand className="w-4 h-4" />
            </button>
            <button
              onClick={resetPan}
              disabled={!selectedFieldId || (activePanOffset.dx === 0 && activePanOffset.dy === 0)}
              title="重置该字段的可视位置"
              className={cn(
                'p-1 rounded transition-colors',
                !selectedFieldId || (activePanOffset.dx === 0 && activePanOffset.dy === 0)
                  ? 'text-gray-600 cursor-not-allowed'
                  : 'hover:bg-white/10 text-gray-400 hover:text-white',
              )}
            >
              <RotateCcw className="w-4 h-4" />
            </button>
          </div>
        </div>
        <button className="flex items-center gap-2 px-3 py-1.5 border border-white/20 hover:bg-white/5 rounded text-white transition-colors text-xs">
          <Upload className="w-3.5 h-3.5" />
          上传新文档
        </button>
      </div>

      {/* Document area — overflow-hidden viewport with a CSS-transformed doc wrapper.
          Manual zoom drives the page's rendered width; field-focus zoom is layered
          on top via `transform: translate(...) scale(2)` on docRef. */}
      <div
        ref={viewportRef}
        className={cn(
          'flex-1 overflow-hidden p-4 flex items-center justify-center bg-[#1e1e24] relative',
          isDrawing && 'z-30',
          panMode && selectedFieldId && (liveDrag ? 'cursor-grabbing' : 'cursor-grab'),
        )}
        onMouseDown={handlePanMouseDown}
        onClick={(e) => {
          // Don't deselect when releasing a pan drag — the doc has moved
          // and the user is still focused on the same field.
          if (panMode || isDrawing) return
          // Only deselect when click target is the viewport background, not
          // an anchor dot (those have stopPropagation already).
          if (e.target === viewportRef.current) setSelectedFieldId(null)
        }}
      >
        {!documentInfo ? (
          <div className="flex flex-col items-center justify-center gap-3">
            <FileText className="w-12 h-12 text-gray-600" />
            <p className="text-sm text-gray-500">正在加载文档...</p>
          </div>
        ) : (
          <div
            ref={docRef}
            style={{
              transformOrigin: '0 0',
              // During an active pan drag, kill the transition so the doc
              // tracks the cursor 1:1; otherwise keep the smooth 1s focus zoom.
              transition: liveDrag ? 'none' : `transform ${FOCUS_TRANSITION_MS}ms ease`,
              transform: focusTransform,
              willChange: 'transform',
            }}
          >
            {documentInfo.fileType === 'image' ? (
              <div className="relative inline-block shadow-2xl rounded-lg overflow-hidden">
                <img
                  src={documentInfo.fileUrl}
                  alt={documentInfo.filename}
                  className="block"
                  style={{ width: pageWidth }}
                  draggable={false}
                />
                <AnchorLayer
                  annotations={visibleAnnotations}
                  results={processingResults}
                  selectedFieldId={selectedFieldId}
                  hoveredFieldId={hoveredFieldId}
                  onSelect={setSelectedFieldId}
                  onHover={setHoveredFieldId}
                />
                {drawingAnn && (
                  <PlacePointOverlay
                    fieldLabel={drawingAnn.label}
                    onCommit={(bbox) => commitDrawingBbox(drawingAnn.id, bbox)}
                    onCancel={() => setDrawingFieldId(null)}
                  />
                )}
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
                    <AnchorLayer
                      annotations={visibleAnnotations}
                      results={processingResults}
                      selectedFieldId={selectedFieldId}
                      hoveredFieldId={hoveredFieldId}
                      onSelect={setSelectedFieldId}
                      onHover={setHoveredFieldId}
                    />
                    {drawingAnn && (
                      <PlacePointOverlay
                        fieldLabel={drawingAnn.label}
                        onCommit={(bbox) => commitDrawingBbox(drawingAnn.id, bbox)}
                        onCancel={() => setDrawingFieldId(null)}
                      />
                    )}
                  </div>
                </Document>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
