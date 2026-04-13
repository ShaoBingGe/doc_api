import { create } from 'zustand'
import apiClient from '../lib/api-client'
import { toast } from '../lib/toast'

export interface Document {
  id: string
  name: string
  filename: string
  uploadedAt: string
  status: 'processing' | 'ready' | 'error'
  pageCount?: number
}

// Backend response shapes
interface BackendDocumentResponse {
  id: string
  filename: string
  file_type: string
  file_size: number
  status: string // 'pending' | 'processing' | 'completed' | 'failed'
  error_message?: string | null
  created_at: string
  updated_at?: string
}

interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

function mapStatus(backendStatus: string): Document['status'] {
  if (backendStatus === 'completed') return 'ready'
  if (backendStatus === 'failed' || backendStatus === 'error') return 'error'
  return 'processing'
}

function mapDocument(raw: BackendDocumentResponse): Document {
  return {
    id: raw.id,
    name: raw.filename.replace(/\.[^.]+$/, ''), // strip extension for display name
    filename: raw.filename,
    uploadedAt: raw.created_at,
    status: mapStatus(raw.status),
  }
}

export type UploadStatus = 'idle' | 'uploading' | 'processing' | 'completed'

interface DocumentStore {
  documents: Document[]
  currentDocument: Document | null
  isLoading: boolean
  error: string | null

  uploadStatus: UploadStatus
  uploadedDocumentId: string | null

  fetchDocuments: () => Promise<void>
  uploadDocument: (file: File) => Promise<Document>
  setCurrentDocument: (doc: Document | null) => void
  resetUploadStatus: () => void
}

export const useDocumentStore = create<DocumentStore>((set) => ({
  documents: [],
  currentDocument: null,
  isLoading: false,
  error: null,
  uploadStatus: 'idle',
  uploadedDocumentId: null,

  fetchDocuments: async () => {
    set({ isLoading: true, error: null })
    try {
      const { data } = await apiClient.get<PaginatedResponse<BackendDocumentResponse>>(
        '/api/v1/documents',
      )
      set({ documents: data.items.map(mapDocument), isLoading: false })
    } catch {
      set({ error: 'Failed to load documents', isLoading: false })
    }
  },

  uploadDocument: async (file: File) => {
    set({ uploadStatus: 'uploading', uploadedDocumentId: null, error: null })
    try {
      const formData = new FormData()
      formData.append('file', file)
      // Do NOT set Content-Type manually — axios auto-sets it with the correct multipart boundary
      const { data } = await apiClient.post<BackendDocumentResponse>(
        '/api/v1/documents/upload',
        formData,
      )
      const doc = mapDocument(data)
      set((state) => ({
        documents: [doc, ...state.documents],
        uploadStatus: 'processing',
        uploadedDocumentId: doc.id,
      }))
      // Brief processing phase for UX feedback
      await new Promise<void>((resolve) => setTimeout(resolve, 1200))
      set({ uploadStatus: 'completed' })
      return doc
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail
      const msg = typeof detail === 'string' ? detail : '上传失败，请检查文件格式或网络'
      set({ uploadStatus: 'idle', error: msg })
      toast.error(msg)
      throw err
    }
  },

  setCurrentDocument: (doc) => set({ currentDocument: doc }),

  resetUploadStatus: () =>
    set({ uploadStatus: 'idle', uploadedDocumentId: null }),
}))
