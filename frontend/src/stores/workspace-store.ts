import { create } from 'zustand'
import apiClient, { updateAnnotation, deleteAnnotation, reprocessDocument } from '../lib/api-client'
import { toast } from '../lib/toast'

export type WorkspaceStep = 'annotate' | 'configure' | 'test' | 'publish'
export type WorkspaceTab = 'fields' | 'api'

export interface Annotation {
  id: string
  label: string
  fieldType: 'text' | 'number' | 'date' | 'boolean' | 'string' | 'array'
  page: number
  boundingBox: { x: number; y: number; width: number; height: number }
  value?: string | number | boolean | null
  isManual?: boolean
}

export interface ProcessingResult {
  annotationId: string
  value: string | number | boolean | null
  confidence: number // 0–100
}

export interface DocumentVersion {
  version: number
  structured_data: Record<string, unknown>
}

export interface DocumentInfo {
  id: string
  filename: string
  fileType: 'pdf' | 'image'
  fileUrl: string
  status: string
}

export interface ApiDefinition {
  id?: string
  name?: string
  description?: string
  apiCode: string
  endpoint: string
  isExisting?: boolean
}

export interface PendingField {
  tempId: string
  name: string
  value: string
  fieldType: 'text' | 'number' | 'date' | 'boolean' | 'array'
}

// ─── Customer customize flow ──────────────────────────────────────────────────
//
// When the user double-clicks a field, we record a draft edit (original vs
// corrected) here. When they save, all drafts (edits + adds) get sent to the
// /customize endpoint which kicks off reflection + 3-round optimization.

export interface FieldEditDraft {
  /** module_key — undefined for new fields (kind=add) */
  moduleKey?: string
  kind: 'edit' | 'add'
  /** Document this draft was made against. Edits and add-row values are
   * per-document because the same field has different correct values on
   * different files. */
  documentId?: string
  originalName?: string
  correctedName: string
  originalValue?: string | number | boolean | null
  correctedValue: string
  originalFormat?: string
  correctedFormat: string
}

/**
 * Add-field rows are TEMPLATE-level (name + format shared across all docs)
 * but each document has its own value evidence. `values[docId]` lets the
 * customer fill in the new field's value on whichever sample they're looking
 * at without losing the row when they switch docs.
 */
export interface AddFieldRow {
  /** Stable id for the row (independent of name to allow renaming) */
  rowId: string
  correctedName: string
  correctedFormat: string
  /** Per-document value evidence; key = documentId */
  values: Record<string, string>
}

export type CustomizeJobPhase =
  | 'queued'
  | 'waiting_for_samples'
  | 'reflecting'
  | 'forking'
  | 'optimizing'
  | 'completed'
  | 'failed'

export interface CustomizeJobStatus {
  jobId: string
  status: CustomizeJobPhase
  phaseDetail: string
  roundsDone: number
  roundsTotal: number
  overallAccuracy?: number | null
  newApiDefinitionId?: string | null
  newApiCode?: string | null
  errorMessage?: string | null
  reflectionSummary?: Array<Record<string, unknown>>
}

// ─── Sample GT review state ───────────────────────────────────────────────────
// Drives the "已审视 X/3" gate. Customer confirms a sample's OCR output as
// ground truth (or fixes individual fields first) before it counts toward
// triggering the 3-round iteration.
export interface SamplesReviewStatus {
  confirmedDocIds: string[]
  confirmedCount: number
  totalCount: number
  requiredCount: number
}

/**
 * Lightweight summary of a sample document in the batch sample set.
 * Comes from GET /api-definitions/{id}/documents.
 */
export interface SampleDoc {
  id: string
  filename: string
  fileType: string
  status: string
}

interface WorkspaceStore {
  annotations: Annotation[]
  selectedFieldId: string | null
  hoveredFieldId: string | null
  processingResults: ProcessingResult[]
  processingVersions: DocumentVersion[]
  currentVersion: number
  step: WorkspaceStep
  activeTab: WorkspaceTab
  documentInfo: DocumentInfo | null
  documentLoading: boolean
  apiDefinition: ApiDefinition | null
  correctionHistory: string[]
  pendingFields: PendingField[]
  drawingFieldId: string | null
  reprocessing: boolean

  // ── Customize / field-edit flow ──────────────────────────────────────────
  editingFieldId: string | null              // when set, middle column expands and JSON column slides out
  fieldEditDrafts: Record<string, FieldEditDraft>  // keyed by moduleKey (or tempId for adds)
  addFieldDrafts: FieldEditDraft[]           // separate list for "add new field" rows
  customizeJob: CustomizeJobStatus | null
  customizeSubmitting: boolean

  // ── Cross-sample edit overlay (design v8 — pending_edits) ───────────────
  // Mirrors ApiDefinition.pending_edits from the backend. Used to render the
  // union of edits across all sample documents (so a field added on doc1
  // appears in doc2's view, a rename on doc1 shows in doc2's annotation
  // labels, etc.). See backend/app/services/pending_edits_service.py.
  pendingEdits: {
    added_fields: Array<{
      field_name: string
      type: string
      description: string
      added_at_doc_id: string | null
      default_value: unknown
    }>
    renames: Record<string, string>           // old → new
    modifications: Record<string, Record<string, unknown>>  // docId → {field: value}
    deleted_fields: string[]                  // Phase 11a
  } | null
  loadPendingEdits: () => Promise<void>
  /** Phase 10 — commit the currently-open FieldEditorPanel draft to the
   * backend pending_edits overlay so badges + cascade rename fire even
   * BEFORE the customize submit. */
  commitCurrentDraft: () => Promise<void>
  /** Phase 11c — delete the currently-edited field. Cascades across all
   * docs of the ApiDef, removes from overlay's added_fields/modifications,
   * and excludes the field from the next customize fork's modules entirely
   * (no meta, no reflection, no schema slot). */
  commitFieldDeletion: () => Promise<void>

  /** Phase 13 — canonical field set the customer wants on every sample.
   * Computed by the backend as:
   *   union(active_modules' fields, overlay.added_fields)
   *   - overlay.deleted_fields
   *   - apply overlay.renames
   * Used to render "本样本未识别字段" placeholder rows.
   */
  requiredFields: string[]
  loadRequiredFields: () => Promise<void>

  setStep: (step: WorkspaceStep) => void
  setSelectedFieldId: (id: string | null) => void
  setHoveredFieldId: (id: string | null) => void
  setActiveTab: (tab: WorkspaceTab) => void
  addAnnotation: (annotation: Annotation) => void
  removeAnnotation: (id: string) => void
  updateFieldValue: (id: string, value: string) => void
  updateFieldBbox: (id: string, bbox: Annotation['boundingBox']) => void
  setProcessingResults: (results: ProcessingResult[]) => void
  setDocumentInfo: (info: DocumentInfo) => void
  setApiDefinition: (def: ApiDefinition) => void
  addCorrectionHistory: (item: string) => void
  setCurrentVersion: (version: number) => void
  saveAnnotation: (docId: string, fieldId: string, data: Record<string, unknown>) => Promise<void>
  deleteAnnotationRemote: (docId: string, fieldId: string) => Promise<void>
  loadDocument: (documentId: string) => Promise<void>
  addPendingField: (name: string, value?: string, fieldType?: PendingField['fieldType']) => void
  removePendingField: (tempId: string) => void
  clearPendingFields: () => void
  setDrawingFieldId: (id: string | null) => void
  commitDrawingBbox: (id: string, bbox: Annotation['boundingBox']) => Promise<void>
  confirmPendingFields: () => Promise<void>
  reset: () => void

  // ── Multi-doc batch sample layer (v3) ────────────────────────────────────
  apiDefinitionId: string | null
  documents: SampleDoc[]
  selectedDocId: string | null
  samplesLoading: boolean
  uploadingSample: boolean

  loadApiDefinition: (apiDefinitionId: string) => Promise<void>
  selectDocument: (docId: string) => Promise<void>
  addSampleDocument: (file: File) => Promise<SampleDoc | null>
  removeSampleDocument: (docId: string) => Promise<void>

  // ── Customize / field-edit actions ──────────────────────────────────────
  startEditingField: (annotationId: string) => void
  cancelEditingField: () => void
  updateEditDraft: (key: string, patch: Partial<FieldEditDraft>) => void
  addNewFieldDraft: () => void
  updateAddDraft: (idx: number, patch: Partial<FieldEditDraft>) => void
  removeAddDraft: (idx: number) => void
  submitCustomize: () => Promise<string | null>
  pollCustomizeJob: () => Promise<void>
  clearCustomizeJob: () => void

  // ── Document viewer pan tool ───────────────────────────────────────────
  fieldPanOffsets: Record<string, { dx: number; dy: number }>
  /** Pan offset applied when no field is focused (the user just drags
   * the document around at native zoom). Persists across selections. */
  globalPanOffset: { dx: number; dy: number }
  panMode: boolean
  setPanMode: (on: boolean) => void
  setFieldPanOffset: (annotationId: string, offset: { dx: number; dy: number }) => void
  clearFieldPanOffset: (annotationId: string) => void
  setGlobalPanOffset: (offset: { dx: number; dy: number }) => void

  // ── Sample GT review ───────────────────────────────────────────────────
  samplesReview: SamplesReviewStatus | null
  loadSamplesReview: () => Promise<void>
  confirmSampleGT: (docId: string, confirmed: boolean) => Promise<void>
  retrySampleOCR: (docId: string) => Promise<void>
}

// ─── Structured data parser ───────────────────────────────────────────────────

interface StructuredDataItem {
  id?: string
  keyName: string
  value: string | number | boolean | null
  confidence?: number | null
  bbox?: { x: number; y: number; width: number; height: number } | null
}

function _isLeaf(v: unknown): boolean {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return false
  return 'value' in (v as Record<string, unknown>)
}

function _normConfidence(c: unknown): number {
  if (typeof c !== 'number') return 90
  return c <= 1 ? c * 100 : c
}

function _defaultBbox(idx: number): Annotation['boundingBox'] {
  return {
    x: 5 + (idx % 4) * 22,
    y: 10 + Math.floor(idx / 4) * 9,
    width: 20,
    height: 4,
  }
}

function parseStructuredData(sd: unknown): {
  annotations: Annotation[]
  results: ProcessingResult[]
} {
  const annotations: Annotation[] = []
  const results: ProcessingResult[] = []
  let idxCounter = 0

  // Recursive walker: flattens nested objects / table rows into a flat list of
  // leaf fields, prefixing keys with a dot path so the right-pane field viewer
  // shows hierarchy via "seller.tax_id", "line_items[0].description", etc.
  function walk(node: unknown, pathPrefix: string) {
    if (node == null) return

    // Leaf: { value, confidence, bbox }
    if (_isLeaf(node)) {
      const v = node as { value: unknown; confidence?: unknown; bbox?: unknown }
      const id = `field-${idxCounter++}`
      const rawBbox = v.bbox && typeof v.bbox === 'object' ? (v.bbox as Record<string, unknown>) : null
      const bbox = rawBbox
        ? {
            x: Number(rawBbox.x ?? 0),
            y: Number(rawBbox.y ?? 0),
            width: Number(rawBbox.width ?? rawBbox.w ?? 0),
            height: Number(rawBbox.height ?? rawBbox.h ?? 0),
          }
        : _defaultBbox(idxCounter)
      const page = rawBbox && typeof rawBbox.page === 'number' ? (rawBbox.page as number) : 1
      const value = v.value as string | number | boolean | null
      const fieldType: Annotation['fieldType'] = typeof value === 'number' ? 'number' : 'text'
      annotations.push({ id, label: pathPrefix || `field_${idxCounter}`, fieldType, page, boundingBox: bbox })
      results.push({ annotationId: id, value, confidence: _normConfidence(v.confidence) })
      return
    }

    // Container object: recurse into each child key (skip _meta).
    if (typeof node === 'object' && !Array.isArray(node)) {
      const obj = node as Record<string, unknown>
      // Table-shaped container { _meta, rows: [...] } → walk each row
      if (Array.isArray(obj.rows)) {
        ;(obj.rows as unknown[]).forEach((row, i) => {
          walk(row, `${pathPrefix}[${i}]`)
        })
        return
      }
      for (const [key, val] of Object.entries(obj)) {
        if (key === '_meta') continue
        const nextPath = pathPrefix ? `${pathPrefix}.${key}` : key
        walk(val, nextPath)
      }
      return
    }

    // Bare array (legacy / fallback)
    if (Array.isArray(node)) {
      node.forEach((item, i) => walk(item, `${pathPrefix}[${i}]`))
      return
    }

    // Bare scalar (legacy {key: value} dict format)
    const id = `field-${idxCounter++}`
    annotations.push({
      id,
      label: pathPrefix || `field_${idxCounter}`,
      fieldType: typeof node === 'number' ? 'number' : 'text',
      page: 1,
      boundingBox: _defaultBbox(idxCounter),
    })
    results.push({ annotationId: id, value: node as string | number | boolean | null, confidence: 90 })
  }

  // Legacy list format: [{id, keyName, value, confidence, bbox}]
  if (Array.isArray(sd) && sd.length > 0 && typeof sd[0] === 'object' && sd[0] && 'keyName' in sd[0]) {
    sd.forEach((item: StructuredDataItem, idx: number) => {
      const id = item.id ?? `field-${idx}`
      const rawBbox = item.bbox as (Annotation['boundingBox'] & { page?: number }) | null | undefined
      const bbox: Annotation['boundingBox'] = rawBbox
        ? { x: rawBbox.x, y: rawBbox.y, width: rawBbox.width, height: rawBbox.height }
        : _defaultBbox(idx)
      const page = rawBbox && typeof rawBbox.page === 'number' ? rawBbox.page : 1
      const fieldType: Annotation['fieldType'] = typeof item.value === 'number' ? 'number' : 'text'
      annotations.push({ id, label: item.keyName, fieldType, page, boundingBox: bbox })
      results.push({
        annotationId: id,
        value: item.value,
        confidence: _normConfidence(item.confidence),
      })
    })
    return { annotations, results }
  }

  walk(sd, '')
  return { annotations, results }
}

// ─── Initial state ────────────────────────────────────────────────────────────

const initialState = {
  annotations: [] as Annotation[],
  selectedFieldId: null as string | null,
  hoveredFieldId: null as string | null,
  processingResults: [] as ProcessingResult[],
  processingVersions: [] as DocumentVersion[],
  currentVersion: 1,
  step: 'annotate' as WorkspaceStep,
  activeTab: 'fields' as WorkspaceTab,
  documentInfo: null as DocumentInfo | null,
  documentLoading: false,
  apiDefinition: null as ApiDefinition | null,
  correctionHistory: [] as string[],
  pendingFields: [] as PendingField[],
  drawingFieldId: null as string | null,
  reprocessing: false,

  // ── batch layer ────────────────────────────────────────────────────────────
  apiDefinitionId: null as string | null,
  documents: [] as SampleDoc[],
  selectedDocId: null as string | null,
  samplesLoading: false,
  uploadingSample: false,

  // ── Customize / field-edit ─────────────────────────────────────────────────
  editingFieldId: null as string | null,
  fieldEditDrafts: {} as Record<string, FieldEditDraft>,
  addFieldDrafts: [] as FieldEditDraft[],
  customizeJob: null as CustomizeJobStatus | null,
  // design v8 — pending_edits overlay; reloaded after every save
  pendingEdits: null as WorkspaceStore['pendingEdits'],
  // Phase 13 — canonical field set (modules ∪ added − deleted, renames applied)
  requiredFields: [] as string[],
  customizeSubmitting: false,
  // Per-annotation pan offset (extra translate on top of the focus zoom).
  // Updated while the user pans the document with the hand tool, replayed
  // next time that field is focused.
  fieldPanOffsets: {} as Record<string, { dx: number; dy: number }>,
  // Offset applied when no field is focused — lets the user shift the
  // document around at native zoom for general inspection.
  globalPanOffset: { dx: 0, dy: 0 } as { dx: number; dy: number },
  // Hand tool active state (drives cursor + drag-to-pan in the doc viewer).
  panMode: false as boolean,
  // GT review state for current ApiDef's sample set
  samplesReview: null as SamplesReviewStatus | null,
}

export const useWorkspaceStore = create<WorkspaceStore>((set, get) => ({
  ...initialState,

  setStep: (step) => set({ step }),

  setSelectedFieldId: (id) => set({ selectedFieldId: id }),

  setHoveredFieldId: (id) => set({ hoveredFieldId: id }),

  setActiveTab: (tab) => set({ activeTab: tab }),

  addAnnotation: (annotation) =>
    set((state) => ({ annotations: [...state.annotations, annotation] })),

  removeAnnotation: (id) =>
    set((state) => ({
      annotations: state.annotations.filter((a) => a.id !== id),
    })),

  updateFieldValue: (id, value) =>
    set((state) => {
      const hasResult = state.processingResults.some((r) => r.annotationId === id)
      if (hasResult) {
        return {
          processingResults: state.processingResults.map((r) =>
            r.annotationId === id ? { ...r, value } : r
          ),
        }
      }
      return {
        annotations: state.annotations.map((a) =>
          a.id === id ? { ...a, value } : a
        ),
      }
    }),

  updateFieldBbox: (id, bbox) =>
    set((state) => ({
      annotations: state.annotations.map((a) =>
        a.id === id ? { ...a, boundingBox: bbox } : a
      ),
    })),

  setProcessingResults: (results) => set({ processingResults: results }),

  setDocumentInfo: (info) => set({ documentInfo: info }),

  setApiDefinition: (def) => set({ apiDefinition: def }),

  addCorrectionHistory: (item) =>
    set((state) => ({
      correctionHistory: [item, ...state.correctionHistory].slice(0, 20),
    })),

  setCurrentVersion: (version) => {
    const { processingVersions } = get()
    const found = processingVersions.find((v) => v.version === version)
    if (!found) return
    const { annotations, results } = parseStructuredData(found.structured_data)
    set({ currentVersion: version, annotations, processingResults: results })
  },

  saveAnnotation: async (docId, fieldId, data) => {
    try {
      await updateAnnotation(docId, fieldId, data)
      // Backend mirrors edits to ApiDef.pending_edits — refresh overlay so
      // other samples' views (and the current view's "其他文件已修改" badges)
      // stay in sync.
      void get().loadPendingEdits()
    } catch (err) {
      // Common cause: the frontend held a stale annotation id (e.g. after
      // a doc re-OCR or a cascade rename that recreated the row). The
      // backend will 404. Reload the document so the next interaction
      // uses fresh ids — but only on the actual 404, not on offline/etc.
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 404 && docId) {
        void get().loadDocument(docId)
      }
      // Otherwise silent — local state is already optimistic-updated.
    }
  },

  deleteAnnotationRemote: async (docId, fieldId) => {
    set((state) => ({
      annotations: state.annotations.filter((a) => a.id !== fieldId),
      processingResults: state.processingResults.filter((r) => r.annotationId !== fieldId),
    }))
    try {
      await deleteAnnotation(docId, fieldId)
    } catch {
      // fail silently
    }
  },

  loadDocument: async (documentId) => {
    set({ documentLoading: true })
    try {
      const res = await apiClient.get(`/api/v1/documents/${documentId}`)
      const data = res.data

      const isImage = ['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(
        (data.file_type ?? '').toLowerCase()
      )
      const fileType: 'pdf' | 'image' = isImage ? 'image' : 'pdf'

      // Fetch preview URL from separate endpoint
      let fileUrl = ''
      try {
        const previewRes = await apiClient.get(`/api/v1/documents/${documentId}/preview`)
        fileUrl = previewRes.data.preview_url ?? ''
      } catch {
        // Preview URL not available
      }

      set({
        documentInfo: {
          id: data.id,
          filename: data.filename ?? documentId,
          fileType,
          fileUrl,
          status: data.status ?? 'unknown',
        },
        selectedDocId: data.id,
      })

      // Load processing versions if available
      const versions: DocumentVersion[] = Array.isArray(data.processing_results)
        ? data.processing_results
            .filter((r: Record<string, unknown>) => r.structured_data)
            .map((r: Record<string, unknown>) => ({
              version: r.version as number ?? 1,
              structured_data: r.structured_data as Record<string, unknown>,
            }))
        : []

      if (versions.length > 0) {
        set({ processingVersions: versions, currentVersion: versions[versions.length - 1].version })
      }

      // Try latest_result first (new API), fallback to processing_result (old API)
      const latestResult = data.latest_result ?? data.processing_result
      const sd = latestResult?.structured_data
      if (sd && (typeof sd === 'object' || Array.isArray(sd))) {
        const { annotations, results } = parseStructuredData(sd)
        set({ annotations, processingResults: results })

        // Merge single result into versions if not already there
        if (versions.length === 0 && latestResult) {
          const v = latestResult.version ?? 1
          set({
            processingVersions: [{ version: v, structured_data: sd as Record<string, unknown> }],
            currentVersion: v,
          })
        }
      }

      // Try to find a linked API definition for this document
      let linkedDef: ApiDefinition | null = null
      try {
        const defsRes = await apiClient.get('/api/v1/api-definitions')
        const defs: Array<Record<string, unknown>> = Array.isArray(defsRes.data)
          ? defsRes.data
          : defsRes.data?.items ?? []
        const match = defs.find(
          (d) => d.sample_document_id === documentId || d.document_id === documentId,
        )
        if (match) {
          const code = (match.api_code ?? '') as string
          linkedDef = {
            id: match.id as string,
            name: (match.name ?? '') as string,
            description: (match.description ?? '') as string,
            apiCode: code,
            endpoint:
              (match.endpoint_url as string) ??
              (match.endpoint as string) ??
              `https://api.apianything.io/v1/extract/${code}`,
            isExisting: true,
          }
        }
      } catch {
        // API definitions list not available — fall through
      }

      if (linkedDef) {
        set({ apiDefinition: linkedDef })
      } else {
        // Fallback: use inline api_definition from document or generate placeholder
        const apiCode = data.api_definition?.api_code ?? `doc-${documentId.slice(0, 6).toLowerCase()}`
        const endpoint = data.api_definition?.endpoint_url ??
          data.api_definition?.endpoint ??
          `https://api.apianything.io/v1/extract/${apiCode}`
        set({ apiDefinition: { apiCode, endpoint } })
      }
    } catch (err) {
      console.error('loadDocument error:', err)
      toast.error('文档加载失败，请检查网络连接')
    } finally {
      set({ documentLoading: false })
    }
    // Refresh overlay so this doc's view shows other-files' adds/renames
    void get().loadPendingEdits()
    void get().loadRequiredFields()
  },

  addPendingField: (name, value = '', fieldType = 'text') =>
    set((state) => ({
      pendingFields: [
        ...state.pendingFields,
        {
          tempId: `pending-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          name,
          value,
          fieldType,
        },
      ],
    })),

  removePendingField: (tempId) =>
    set((state) => ({
      pendingFields: state.pendingFields.filter((f) => f.tempId !== tempId),
    })),

  clearPendingFields: () => set({ pendingFields: [] }),

  setDrawingFieldId: (id) => set({ drawingFieldId: id }),

  commitDrawingBbox: async (id, bbox) => {
    set((state) => ({
      annotations: state.annotations.map((a) =>
        a.id === id ? { ...a, boundingBox: bbox } : a,
      ),
      drawingFieldId: null,
    }))
    const docId = get().documentInfo?.id
    if (docId) {
      try {
        await updateAnnotation(docId, id, { bounding_box: bbox })
      } catch {
        toast.error('保存方框失败')
      }
    }
  },

  confirmPendingFields: async () => {
    const { pendingFields, documentInfo } = get()
    if (!documentInfo?.id || pendingFields.length === 0) return
    set({ reprocessing: true })
    try {
      const res = await reprocessDocument(documentInfo.id, undefined, {
        extra_fields: pendingFields.map((f) => f.name),
      })
      const sd = res.data?.structured_data
      if (sd && (typeof sd === 'object' || Array.isArray(sd))) {
        const { annotations, results } = parseStructuredData(sd)
        // Override AI value with user-supplied value when present
        const userValueMap = new Map(
          pendingFields.filter((f) => f.value.trim()).map((f) => [f.name, f.value]),
        )
        const mergedResults = results.map((r) => {
          const ann = annotations.find((a) => a.id === r.annotationId)
          if (ann && userValueMap.has(ann.label)) {
            return { ...r, value: userValueMap.get(ann.label) ?? r.value, confidence: 100 }
          }
          return r
        })
        set({ annotations, processingResults: mergedResults })
      }
      set({ pendingFields: [] })
      toast.success(`已识别 ${pendingFields.length} 个新字段`)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof msg === 'string' ? msg : 'AI 识别失败')
    } finally {
      set({ reprocessing: false })
    }
  },

  reset: () => {
    set(initialState)
  },

  // ── Multi-doc batch sample layer ────────────────────────────────────────
  //
  // Workspace v3 pivots from `/workspace/{docId}` to `/workspace/{apiDefId}`.
  // `apiDefinitionId` is the URL anchor; `selectedDocId` points at whichever
  // doc in the sample set is currently shown in the (existing) single-doc
  // panels. Switching docs calls `selectDocument(id)` which delegates to
  // `loadDocument(id)`, so the rest of the store stays untouched.

  loadApiDefinition: async (apiDefId) => {
    set({ samplesLoading: true, apiDefinitionId: apiDefId })
    try {
      // 1. fetch ApiDefinition itself for header chrome
      try {
        const defRes = await apiClient.get('/api/v1/api-definitions/' + apiDefId)
        const d = defRes.data
        const code = (d.api_code ?? '') as string
        set({
          apiDefinition: {
            id: d.id as string,
            name: (d.name ?? '') as string,
            description: (d.description ?? '') as string,
            apiCode: code,
            endpoint:
              (d.endpoint_url as string) ??
              'https://api.apianything.io/v1/extract/' + code,
            isExisting: true,
          },
        })
      } catch {
        // non-fatal — header just shows placeholder
      }

      // 1b. Rehydrate any active customize job for this ApiDef so the
      // sample-upload banner / progress UI carries across navigations.
      try {
        const jobRes = await apiClient.get(
          '/api/v1/api-definitions/' + apiDefId + '/active-customize-job',
        )
        const d = jobRes.data
        if (d && d.job_id) {
          const rehydrated: CustomizeJobStatus = {
            jobId: d.job_id,
            status: d.status,
            phaseDetail: d.phase_detail || '',
            roundsDone: d.rounds_done ?? 0,
            roundsTotal: d.rounds_total ?? 3,
            overallAccuracy: d.overall_accuracy,
            newApiDefinitionId: d.new_api_definition_id,
            newApiCode: d.new_api_code,
            errorMessage: d.error_message,
            reflectionSummary: d.reflection_summary,
          }
          set({ customizeJob: rehydrated })
          // Keep polling alive
          if (rehydrated.status !== 'completed' && rehydrated.status !== 'failed') {
            setTimeout(() => void get().pollCustomizeJob(), 2000)
          }
        } else {
          set({ customizeJob: null })
        }
      } catch {
        // No active job — fine
      }

      // 1c. fetch pending_edits overlay (design v8) — async, non-blocking
      void get().loadPendingEdits()
      // Phase 13 — fetch the canonical required-field set
      void get().loadRequiredFields()

      // 2. fetch sample set
      const res = await apiClient.get(
        '/api/v1/api-definitions/' + apiDefId + '/documents',
      )
      const list = (Array.isArray(res.data) ? res.data : []) as Array<
        Record<string, unknown>
      >
      const docs: SampleDoc[] = list.map((d) => ({
        id: d.id as string,
        filename: (d.filename ?? '') as string,
        fileType: (d.file_type ?? 'unknown') as string,
        status: (d.status ?? 'unknown') as string,
      }))
      set({ documents: docs })

      // 2b. fetch GT review status for the sample set
      void get().loadSamplesReview()

      // 3. auto-select first doc
      if (docs.length > 0) {
        await get().selectDocument(docs[0].id)
      } else {
        // Empty sample set — clear single-doc view
        set({
          selectedDocId: null,
          documentInfo: null,
          annotations: [],
          processingResults: [],
          processingVersions: [],
        })
      }
    } catch (err) {
      console.error('loadApiDefinition error:', err)
      toast.error('加载样本集失败')
    } finally {
      set({ samplesLoading: false })
    }
  },

  selectDocument: async (docId) => {
    if (get().selectedDocId === docId) return
    // Capture the batch-layer ApiDefinition so loadDocument's per-doc lookup
    // doesn't overwrite it with a doc-derived placeholder.
    const lockedApiDef = get().apiDefinition
    set({ selectedDocId: docId })
    set({
      annotations: [],
      processingResults: [],
      processingVersions: [],
      selectedFieldId: null,
      hoveredFieldId: null,
      pendingFields: [],
    })
    await get().loadDocument(docId)
    // Restore — but only if we're still in batch mode (apiDefinitionId set)
    if (lockedApiDef && get().apiDefinitionId) {
      set({ apiDefinition: lockedApiDef })
    }
  },

  addSampleDocument: async (file) => {
    const apiDefId = get().apiDefinitionId
    if (!apiDefId) {
      toast.error('请先保存 API 后再添加样本')
      return null
    }
    set({ uploadingSample: true })
    try {
      const fd = new FormData()
      fd.append('file', file)
      const res = await apiClient.post(
        '/api/v1/api-definitions/' + apiDefId + '/documents',
        fd,
        { headers: { 'Content-Type': 'multipart/form-data' } },
      )
      const d = res.data
      const newDoc: SampleDoc = {
        id: d.id as string,
        filename: (d.filename ?? file.name) as string,
        fileType: (d.file_type ?? 'unknown') as string,
        status: (d.status ?? 'queued') as string,
      }
      set((s) => ({ documents: [...s.documents, newDoc] }))
      // Auto-select the freshly added doc so user can start labelling right away
      await get().selectDocument(newDoc.id)
      toast.success('样本添加成功')
      return newDoc
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail
      toast.error(typeof msg === 'string' ? msg : '上传失败')
      return null
    } finally {
      set({ uploadingSample: false })
    }
  },

  // ── Customize / field-edit ──────────────────────────────────────────────
  //
  // Double-click a field row → middle column expands (Column C in Workspace.tsx
  // collapses) and shows a side-by-side "原始 / 修正后" form. The draft is keyed
  // by module_key. The draft is sent in batch when the user hits "保存为自定义模板".

  startEditingField: (annotationId) => {
    const { annotations, processingResults, fieldEditDrafts } = get()
    const ann = annotations.find((a) => a.id === annotationId)
    if (!ann) return
    const r = processingResults.find((rr) => rr.annotationId === annotationId)
    const value = r?.value ?? ann.value ?? ''
    // Draft key = the full annotation label (so each array cell gets its own
    // draft, e.g. `details[0].quantity` ≠ `details[1].quantity`). The backend
    // module_key (the parent OcrModule) is the snake_case of the path up to
    // the first `[` so multiple array-cell diffs route to the same module
    // and the reflection / fork logic accumulates them.
    const draftKey = ann.label
    const labelBase = ann.label.split('[')[0]
    const moduleKey = labelBase
      .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
      .replace(/[^a-zA-Z0-9]+/g, '_')
      .toLowerCase()
    if (!fieldEditDrafts[draftKey]) {
      const draft: FieldEditDraft = {
        moduleKey,
        kind: 'edit',
        originalName: ann.label,
        correctedName: ann.label,
        originalValue: value as string | number | boolean | null,
        correctedValue: value === null || value === undefined ? '' : String(value),
        originalFormat: ann.fieldType,
        correctedFormat: ann.fieldType,
      }
      set({
        editingFieldId: annotationId,
        fieldEditDrafts: { ...fieldEditDrafts, [draftKey]: draft },
      })
    } else {
      set({ editingFieldId: annotationId })
    }
  },

  cancelEditingField: () => set({ editingFieldId: null }),

  updateEditDraft: (key, patch) =>
    set((s) => ({
      fieldEditDrafts: {
        ...s.fieldEditDrafts,
        [key]: { ...(s.fieldEditDrafts[key] ?? { kind: 'edit', correctedName: '', correctedValue: '', correctedFormat: 'string' }), ...patch },
      },
    })),

  addNewFieldDraft: () =>
    set((s) => ({
      addFieldDrafts: [
        ...s.addFieldDrafts,
        { kind: 'add', correctedName: '', correctedValue: '', correctedFormat: 'string' },
      ],
    })),

  updateAddDraft: (idx, patch) =>
    set((s) => ({
      addFieldDrafts: s.addFieldDrafts.map((d, i) => (i === idx ? { ...d, ...patch } : d)),
    })),

  removeAddDraft: (idx) =>
    set((s) => ({ addFieldDrafts: s.addFieldDrafts.filter((_, i) => i !== idx) })),

  submitCustomize: async () => {
    const { apiDefinitionId, fieldEditDrafts, addFieldDrafts } = get()
    if (!apiDefinitionId) {
      toast.error('未绑定 API，无法保存')
      return null
    }
    // Only include edits where corrected differs from original (and adds with a name)
    const editDiffs = Object.values(fieldEditDrafts).filter((d) => {
      const nameChanged = (d.originalName || '') !== d.correctedName
      const valueChanged = String(d.originalValue ?? '') !== d.correctedValue
      const fmtChanged = (d.originalFormat || '') !== d.correctedFormat
      return nameChanged || valueChanged || fmtChanged
    })
    const addDiffs = addFieldDrafts.filter((d) => d.correctedName.trim().length > 0)
    if (editDiffs.length === 0 && addDiffs.length === 0) {
      toast.info('没有可保存的字段修改')
      return null
    }

    // Phase 23.1 — FLUSH all in-memory drafts to backend pending_edits
    // overlay BEFORE creating the customize job. The fork step (Phase
    // 23.2) reads renames/added/deleted from overlay as the SINGLE
    // source of truth; if drafts haven't been committed via the
    // "保存到模板（立即生效）" path, this flush guarantees the overlay
    // is in sync at customize time.
    const flushed: Array<Promise<unknown>> = []
    for (const d of editDiffs) {
      const oldN = (d.originalName || '').trim()
      const newN = (d.correctedName || '').trim()
      const valChanged = String(d.originalValue ?? '') !== d.correctedValue
      // Rename branch
      if (oldN && newN && oldN !== newN) {
        flushed.push(apiClient.post(
          `/api/v1/api-definitions/${apiDefinitionId}/pending-edits/commit-draft`,
          { old_name: oldN, new_name: newN },
        ))
      }
      // Value modification branch (per-doc)
      const docIdForMod = d.documentId ?? get().selectedDocId
      if (valChanged && docIdForMod) {
        flushed.push(apiClient.post(
          `/api/v1/api-definitions/${apiDefinitionId}/pending-edits/commit-draft`,
          {
            modification: {
              document_id: docIdForMod,
              field_name: newN || oldN,
              value: d.correctedValue,
            },
          },
        ))
      }
    }
    for (const d of addDiffs) {
      const newN = (d.correctedName || '').trim()
      if (!newN) continue
      flushed.push(apiClient.post(
        `/api/v1/api-definitions/${apiDefinitionId}/pending-edits/commit-draft`,
        {
          new_name: newN,
          field_type: d.correctedFormat || 'string',
          added_value: d.correctedValue || null,
        },
      ))
    }
    try {
      await Promise.all(flushed)
      await get().loadPendingEdits()
    } catch (err) {
      console.warn('Phase 23.1 draft-flush had errors, proceeding anyway', err)
    }
    const payload = {
      diffs: [...editDiffs, ...addDiffs].map((d) => ({
        kind: d.kind,
        module_key: d.moduleKey ?? null,
        original_name: d.originalName ?? null,
        corrected_name: d.correctedName,
        original_value: d.originalValue ?? null,
        corrected_value: d.correctedValue,
        original_format: d.originalFormat ?? null,
        corrected_format: d.correctedFormat,
      })),
    }
    set({ customizeSubmitting: true })
    try {
      const res = await apiClient.post(
        `/api/v1/api-definitions/${apiDefinitionId}/customize`,
        payload,
      )
      const jobId = res.data?.job_id as string
      if (!jobId) throw new Error('No job_id returned')
      set({
        customizeJob: {
          jobId,
          status: 'queued',
          phaseDetail: '已提交，等待处理...',
          roundsDone: 0,
          roundsTotal: 3,
        },
        editingFieldId: null,
      })
      // Kick off polling
      void get().pollCustomizeJob()
      return jobId
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof msg === 'string' ? msg : '保存失败')
      return null
    } finally {
      set({ customizeSubmitting: false })
    }
  },

  pollCustomizeJob: async () => {
    const job = get().customizeJob
    if (!job) return
    try {
      const res = await apiClient.get(
        `/api/v1/api-definitions/customize-jobs/${job.jobId}`,
      )
      const d = res.data
      const updated: CustomizeJobStatus = {
        jobId: d.job_id,
        status: d.status,
        phaseDetail: d.phase_detail || '',
        roundsDone: d.rounds_done ?? 0,
        roundsTotal: d.rounds_total ?? 3,
        overallAccuracy: d.overall_accuracy,
        newApiDefinitionId: d.new_api_definition_id,
        newApiCode: d.new_api_code,
        errorMessage: d.error_message,
        reflectionSummary: d.reflection_summary,
      }
      set({ customizeJob: updated })
      if (updated.status === 'completed' || updated.status === 'failed') return
      // Adaptive cadence:
      //   - waiting_for_samples: every 10s, the user is busy clicking 已审视
      //   - reflecting: 5s — agent runs are slow (~15s/diff) so frequent
      //     polling just wastes a request; the backend won't change state
      //     between rounds anyway
      //   - forking / optimizing: 3s — these phases stream rapid updates
      let delay = 3_000
      if (updated.status === 'waiting_for_samples') delay = 10_000
      else if (updated.status === 'reflecting') delay = 5_000
      setTimeout(() => void get().pollCustomizeJob(), delay)
    } catch {
      setTimeout(() => void get().pollCustomizeJob(), 6_000)
    }
  },

  clearCustomizeJob: () =>
    set({ customizeJob: null, fieldEditDrafts: {}, addFieldDrafts: [] }),

  // ── Pan tool ───────────────────────────────────────────────────────────
  setPanMode: (on) => set({ panMode: on }),

  setFieldPanOffset: (annotationId, offset) =>
    set((s) => ({
      fieldPanOffsets: { ...s.fieldPanOffsets, [annotationId]: offset },
    })),

  clearFieldPanOffset: (annotationId) =>
    set((s) => {
      const next = { ...s.fieldPanOffsets }
      delete next[annotationId]
      return { fieldPanOffsets: next }
    }),

  setGlobalPanOffset: (offset) => set({ globalPanOffset: offset }),

  // ── Pending-edits overlay (design v8) ──────────────────────────────────
  commitCurrentDraft: async () => {
    const { editingFieldId, fieldEditDrafts, annotations, processingResults,
            apiDefinitionId, selectedDocId } = get()
    if (!editingFieldId || !apiDefinitionId) return
    const ann = annotations.find((a) => a.id === editingFieldId)
    if (!ann) return
    // Drafts may be keyed by current label (rename case) OR by the
    // original-pre-rename name — try both.
    const draft = fieldEditDrafts[ann.label] ?? fieldEditDrafts[
      Object.keys(fieldEditDrafts).find((k) =>
        fieldEditDrafts[k].originalName === ann.label) ?? ''
    ]
    if (!draft) return

    const oldName = (draft.originalName ?? ann.label) || ''
    const newName = (draft.correctedName ?? '').trim()
    const correctedValue = (draft.correctedValue ?? '').trim()
    const r = processingResults.find((p) => p.annotationId === editingFieldId)
    const origValue = r?.value ?? ann.value ?? ''
    const origValueStr = origValue === null || origValue === undefined ? '' : String(origValue)

    const payload: Record<string, unknown> = {}
    // Rename intent
    if (newName && oldName && newName !== oldName) {
      payload.old_name = oldName
      payload.new_name = newName
    }
    // Value modification intent
    if (correctedValue !== origValueStr && selectedDocId) {
      payload.modification = {
        document_id: selectedDocId,
        field_name: newName || oldName,
        value: correctedValue,
      }
    }
    if (Object.keys(payload).length === 0) return

    try {
      await apiClient.post(
        `/api/v1/api-definitions/${apiDefinitionId}/pending-edits/commit-draft`,
        payload,
      )
      // Refresh overlay + reload doc so cascade-renamed annotations
      // and updated values appear immediately.
      await get().loadPendingEdits()
      if (selectedDocId) await get().loadDocument(selectedDocId)
    } catch (err) {
      console.error('commitCurrentDraft failed', err)
    }
  },

  loadPendingEdits: async () => {
    const id = get().apiDefinitionId
    if (!id) return
    try {
      const res = await apiClient.get(
        `/api/v1/api-definitions/${id}/pending-edits`,
      )
      const d = (res.data || {}) as {
        added_fields?: Array<{
          field_name: string
          type?: string
          description?: string
          added_at_doc_id?: string | null
          default_value?: unknown
        }>
        renames?: Record<string, string>
        modifications?: Record<string, Record<string, unknown>>
        deleted_fields?: string[]
      }
      set({
        pendingEdits: {
          added_fields: (d.added_fields || []).map((f) => ({
            field_name: f.field_name,
            type: f.type || 'string',
            description: f.description || '',
            added_at_doc_id: f.added_at_doc_id ?? null,
            default_value: f.default_value ?? null,
          })),
          renames: d.renames || {},
          modifications: d.modifications || {},
          deleted_fields: d.deleted_fields || [],
        },
      })
    } catch {
      // non-fatal
    }
  },

  commitFieldDeletion: async () => {
    const { editingFieldId, annotations, apiDefinitionId, selectedDocId } = get()
    if (!editingFieldId || !apiDefinitionId) return
    const ann = annotations.find((a) => a.id === editingFieldId)
    if (!ann) return
    try {
      await apiClient.post(
        `/api/v1/api-definitions/${apiDefinitionId}/pending-edits/commit-draft`,
        { deleted: true, field_name: ann.label },
      )
      // Drop locally + close the editor pane
      set((s) => ({
        annotations: s.annotations.filter((a) => a.id !== editingFieldId),
        editingFieldId: null,
      }))
      await get().loadPendingEdits()
      await get().loadRequiredFields()
      if (selectedDocId) await get().loadDocument(selectedDocId)
    } catch (err) {
      console.error('commitFieldDeletion failed', err)
    }
  },

  loadRequiredFields: async () => {
    const id = get().apiDefinitionId
    if (!id) return
    try {
      const res = await apiClient.get(
        `/api/v1/api-definitions/${id}/required-fields`,
      )
      const fields = (res.data?.fields || []) as string[]
      set({ requiredFields: fields })
    } catch {
      // non-fatal
    }
  },

  // ── Sample GT review ────────────────────────────────────────────────────
  loadSamplesReview: async () => {
    const id = get().apiDefinitionId
    if (!id) return
    try {
      const res = await apiClient.get(
        `/api/v1/api-definitions/${id}/samples-review`,
      )
      const d = res.data
      const confirmedDocIds: string[] = (d.samples || [])
        .filter((s: { confirmed: boolean }) => s.confirmed)
        .map((s: { document_id: string }) => s.document_id)
      set({
        samplesReview: {
          confirmedDocIds,
          confirmedCount: d.confirmed_count ?? confirmedDocIds.length,
          totalCount: d.total_count ?? (d.samples || []).length,
          requiredCount: d.required_for_iteration ?? 3,
        },
      })
    } catch {
      // non-fatal
    }
  },

  confirmSampleGT: async (docId, confirmed) => {
    const apiDefId = get().apiDefinitionId
    if (!apiDefId) return
    try {
      await apiClient.post(
        `/api/v1/api-definitions/${apiDefId}/samples/${docId}/confirm-gt`,
        { confirmed },
      )
      await get().loadSamplesReview()
      const job = get().customizeJob
      if (job) void get().pollCustomizeJob()
    } catch (err: unknown) {
      const e = err as { response?: { data?: { error?: { message?: string }; detail?: string } } }
      const msg = e?.response?.data?.error?.message
        ?? e?.response?.data?.detail
        ?? '保存确认失败'
      toast.error(msg)
    }
  },

  retrySampleOCR: async (docId) => {
    const apiDefId = get().apiDefinitionId
    if (!apiDefId) return
    toast.info('正在重新跑 OCR...')
    try {
      const res = await apiClient.post(
        `/api/v1/api-definitions/${apiDefId}/samples/${docId}/retry-ocr`,
      )
      const { status, annotations_created, error } = res.data
      if (error) {
        // OCR failed but the endpoint succeeded (degraded mode)
        const short = String(error).split('\n')[0].slice(0, 160)
        toast.error(`OCR 仍然失败：${short}`)
        return
      }
      if (status === 'completed' && annotations_created > 0) {
        toast.success(`OCR 完成 · 生成 ${annotations_created} 个标注`)
        if (get().selectedDocId === docId) {
          await get().loadDocument(docId)
        }
        await get().loadSamplesReview()
      } else {
        toast.error(`OCR 未完成 · 状态: ${status}`)
      }
    } catch (err: unknown) {
      const e = err as { response?: { data?: { error?: { message?: string }; detail?: string } } }
      const msg = e?.response?.data?.error?.message
        ?? e?.response?.data?.detail
        ?? 'OCR 重试失败（可能上游服务不可达）'
      toast.error(msg)
    }
  },

  removeSampleDocument: async (docId) => {
    const apiDefId = get().apiDefinitionId
    if (!apiDefId) return
    try {
      await apiClient.delete(
        '/api/v1/api-definitions/' + apiDefId + '/documents/' + docId,
      )
      const remaining = get().documents.filter((d) => d.id !== docId)
      set({ documents: remaining })
      // If the removed doc was selected, pick another (or clear)
      if (get().selectedDocId === docId) {
        if (remaining.length > 0) {
          await get().selectDocument(remaining[0].id)
        } else {
          set({
            selectedDocId: null,
            documentInfo: null,
            annotations: [],
            processingResults: [],
          })
        }
      }
      toast.success('已从样本集移除')
    } catch {
      toast.error('移除失败')
    }
  },
}))
