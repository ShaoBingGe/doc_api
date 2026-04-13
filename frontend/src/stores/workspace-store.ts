import { create } from 'zustand'
import apiClient, { updateAnnotation, deleteAnnotation } from '../lib/api-client'
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
  reset: () => void
}

// ─── Structured data parser ───────────────────────────────────────────────────

interface StructuredDataItem {
  id?: string
  keyName: string
  value: string | number | boolean | null
  confidence?: number | null
  bbox?: { x: number; y: number; width: number; height: number } | null
}

function parseStructuredData(sd: unknown): {
  annotations: Annotation[]
  results: ProcessingResult[]
} {
  const annotations: Annotation[] = []
  const results: ProcessingResult[] = []

  // Handle list format: [{id, keyName, value, confidence, bbox}]
  if (Array.isArray(sd)) {
    sd.forEach((item: StructuredDataItem, idx: number) => {
      const id = item.id ?? `field-${idx}`
      const confidence = item.confidence != null
        ? (item.confidence <= 1 ? item.confidence * 100 : item.confidence)
        : 90
      const bbox: Annotation['boundingBox'] = item.bbox ?? {
        x: 5 + (idx % 4) * 22,
        y: 10 + Math.floor(idx / 4) * 9,
        width: 20,
        height: 4,
      }

      const fieldType: Annotation['fieldType'] = typeof item.value === 'number' ? 'number' : 'text'

      annotations.push({ id, label: item.keyName, fieldType, page: 1, boundingBox: bbox })
      results.push({ annotationId: id, value: item.value, confidence })
    })
    return { annotations, results }
  }

  // Handle dict format: {key: value} or {key: {value, confidence, bbox}}
  if (sd && typeof sd === 'object') {
    let idx = 0
    for (const [key, val] of Object.entries(sd as Record<string, unknown>)) {
      const id = `field-${idx}`
      let fieldValue: string | number | boolean | null
      let confidence = 90
      let bbox: Annotation['boundingBox'] = {
        x: 5 + (idx % 4) * 22,
        y: 10 + Math.floor(idx / 4) * 9,
        width: 20,
        height: 4,
      }

      if (val !== null && typeof val === 'object' && !Array.isArray(val)) {
        const v = val as Record<string, unknown>
        fieldValue = v.value as string | number | boolean | null
        if (typeof v.confidence === 'number') {
          confidence = v.confidence <= 1 ? v.confidence * 100 : v.confidence
        }
        if (v.bbox && typeof v.bbox === 'object') {
          bbox = v.bbox as Annotation['boundingBox']
        }
      } else {
        fieldValue = val as string | number | boolean | null
      }

      annotations.push({ id, label: key, fieldType: 'text', page: 1, boundingBox: bbox })
      results.push({ annotationId: id, value: fieldValue, confidence })
      idx++
    }
  }

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
    } catch {
      // fail silently — local state already up to date
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
  },

  reset: () => {
    set(initialState)
  },
}))
