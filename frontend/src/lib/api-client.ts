import axios, { AxiosError } from 'axios'

const baseURL = import.meta.env.VITE_API_URL ?? ''

const apiClient = axios.create({
  baseURL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 30_000,
})

apiClient.interceptors.request.use((config) => {
  const apiKey = localStorage.getItem('api_key')
  if (apiKey) {
    config.headers['X-API-Key'] = apiKey
  }
  return config
})

// Console-noise control:
//   - request aborts (component unmount, HMR, throttled poller) get
//     silenced — they're routine, not errors
//   - genuine connect failures coalesce to ONE warning per 5s window
//     so a stuck backend doesn't spam the console with 300 lines
let _lastNetworkWarn = 0

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response) {
      const status = error.response.status
      if (status === 401) {
        console.error('Unauthorized — check your API key in Settings.')
      } else if (status === 404) {
        // Stale-annotation 404s are a routine race; downgrade to silent
        // (workspace-store.saveAnnotation already auto-reloads the doc).
        const url = error.config?.url || ''
        if (!url.includes('/annotations/')) {
          console.warn('Resource not found:', url)
        }
      } else if (status >= 500) {
        console.error('Server error:', error.response.data)
      }
    } else if (error.code === 'ERR_CANCELED' || error.message === 'canceled') {
      // Request was aborted (component unmount / poller throttle) — no log
    } else if (error.request) {
      const now = Date.now()
      if (now - _lastNetworkWarn > 5_000) {
        _lastNetworkWarn = now
        console.warn('Network error — server may be busy or unreachable at', baseURL || '/')
      }
    }
    return Promise.reject(error)
  },
)

export default apiClient

export function reprocessDocument(
  docId: string,
  processorType?: string,
  extra?: { prompt?: string; extra_fields?: string[] },
) {
  return apiClient.post(`/api/v1/documents/${docId}/reprocess`, {
    processor_type: processorType ?? null,
    prompt: extra?.prompt ?? null,
    extra_fields: extra?.extra_fields ?? null,
  })
}

export function uploadDocument(formData: FormData) {
  return apiClient.post('/api/v1/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// ── Country template (§6.4) ──────────────────────────────────────────────────

export interface CountryTemplateInfo {
  country: string
  available: boolean
}

export function fetchCountryTemplates() {
  return apiClient.get<CountryTemplateInfo[]>('/api/v1/country-templates')
}

export function initFromCountryTemplate(country: string) {
  return apiClient.post<{
    api_definition_id: string
    version_id: string
    redirect_url: string
    module_count: number
  }>('/api/v1/api-definitions/from-country-template', { country })
}

export function activateApiDefinition(apiCode: string) {
  return apiClient.post(`/api/v1/api-definitions/${apiCode}/activate`)
}

export function createAnnotation(docId: string, data: Record<string, unknown>) {
  return apiClient.post(`/api/v1/documents/${docId}/annotations`, data)
}

export function updateAnnotation(docId: string, fieldId: string, data: Record<string, unknown>) {
  return apiClient.patch(`/api/v1/documents/${docId}/annotations/${fieldId}`, data)
}

export function deleteAnnotation(docId: string, fieldId: string) {
  return apiClient.delete(`/api/v1/documents/${docId}/annotations/${fieldId}`)
}

export function createApiDefinition(data: Record<string, unknown>) {
  return apiClient.post('/api/v1/api-definitions', data)
}

export function fetchApiDefinitions() {
  return apiClient.get('/api/v1/api-definitions')
}

export function fetchApiKeys() {
  return apiClient.get('/api/v1/api-keys')
}

export function createApiKey(data: { name: string }) {
  return apiClient.post('/api/v1/api-keys', data)
}

export function deleteApiKey(id: string) {
  return apiClient.delete(`/api/v1/api-keys/${id}`)
}

export function fetchTemplates(params?: { country?: string; language?: string }) {
  return apiClient.get('/api/v1/templates', { params })
}

export function subscribeTemplate(templateId: string, name?: string) {
  return apiClient.post(`/api/v1/templates/${templateId}/subscribe`, null, { params: name ? { name } : {} })
}

export function updateApiDefinition(id: string, data: Record<string, unknown>) {
  return apiClient.put(`/api/v1/api-definitions/${id}`, data)
}

export function deleteApiDefinition(id: string) {
  return apiClient.delete(`/api/v1/api-definitions/${id}`)
}

// Status lifecycle: submit_review (→待验证) / activate (→已发布) / deactivate (→已停用)
export type ApiStatusAction = 'submit_review' | 'activate' | 'deactivate'
export function updateApiStatus(id: string, action: ApiStatusAction) {
  return apiClient.patch(`/api/v1/api-definitions/${id}/status`, { action })
}

export function fetchUsageStats(range?: string) {
  return apiClient.get('/api/v1/usage/stats', { params: { range: range ?? '7d' } })
}

// ── OCR Optimizer (modular) ─────────────────────────────────────────────────

export function initOcrOptimizer(
  apiDefId: string,
  body: { sample_document_ids?: string[]; activate?: boolean; use_llm_for_modules?: boolean } = {},
) {
  return apiClient.post(`/api/v1/api-definitions/${apiDefId}/ocr-optimizer/init`, {
    sample_document_ids: body.sample_document_ids ?? [],
    activate: body.activate ?? true,
    use_llm_for_modules: body.use_llm_for_modules ?? false,
  })
}

export function triggerOptimization(
  apiDefId: string,
  body: {
    max_rounds?: number | null
    target_accuracy?: number | null
    sample_document_ids?: string[] | null
    llm_provider?: string | null
  } = {},
) {
  return apiClient.post(`/api/v1/api-definitions/${apiDefId}/ocr-optimizer/optimize`, {
    max_rounds: body.max_rounds ?? null,
    target_accuracy: body.target_accuracy ?? null,
    sample_document_ids: body.sample_document_ids ?? null,
    llm_provider: body.llm_provider ?? null,
  })
}

export function fetchOcrVersions(apiDefId: string) {
  return apiClient.get(`/api/v1/api-definitions/${apiDefId}/ocr-optimizer/versions`)
}

export function fetchOcrVersion(apiDefId: string, versionId: string) {
  return apiClient.get(`/api/v1/api-definitions/${apiDefId}/ocr-optimizer/versions/${versionId}`)
}

export function activateOcrVersion(apiDefId: string, versionId: string) {
  return apiClient.patch(`/api/v1/api-definitions/${apiDefId}/ocr-optimizer/versions/${versionId}/activate`)
}

export function fetchOcrRuns(apiDefId: string) {
  return apiClient.get(`/api/v1/api-definitions/${apiDefId}/ocr-optimizer/runs`)
}

export function fetchOcrRun(apiDefId: string, runId: string) {
  return apiClient.get(`/api/v1/api-definitions/${apiDefId}/ocr-optimizer/runs/${runId}`)
}

export function fetchOcrRound(apiDefId: string, runId: string, roundNum: number) {
  return apiClient.get(
    `/api/v1/api-definitions/${apiDefId}/ocr-optimizer/runs/${runId}/rounds/${roundNum}`,
  )
}

export function fetchOcrIterations(apiDefId: string, runId: string) {
  return apiClient.get(`/api/v1/api-definitions/${apiDefId}/ocr-optimizer/runs/${runId}/iterations`)
}

// ── Run state machine (paused_for_review) ───────────────────────────────────

export function advanceRound(
  apiDefId: string,
  runId: string,
  body: { use_version_id?: string | null } = {},
) {
  return apiClient.post(
    `/api/v1/api-definitions/${apiDefId}/ocr-optimizer/runs/${runId}/advance`,
    { use_version_id: body.use_version_id ?? null },
  )
}

export function finalizeRun(apiDefId: string, runId: string, versionId: string) {
  return apiClient.post(
    `/api/v1/api-definitions/${apiDefId}/ocr-optimizer/runs/${runId}/finalize`,
    { version_id: versionId },
  )
}

export function abortRun(apiDefId: string, runId: string) {
  return apiClient.post(`/api/v1/api-definitions/${apiDefId}/ocr-optimizer/runs/${runId}/abort`)
}

// ── Manual patch (derive v_n.x from user edits) ─────────────────────────────

export interface ModuleEditPayload {
  module_key: string
  description?: string
  ocr_suggestions?: Record<string, unknown>
}

export function manualPatchVersion(
  apiDefId: string,
  versionId: string,
  edits: ModuleEditPayload[],
) {
  return apiClient.post(
    `/api/v1/api-definitions/${apiDefId}/ocr-optimizer/versions/${versionId}/manual-patch`,
    { edits },
  )
}

// ── Skills (TODO placeholders — backend returns 501) ────────────────────────

export function fetchOcrSkills(apiDefId: string) {
  return apiClient.get(`/api/v1/api-definitions/${apiDefId}/ocr-optimizer/skills`)
}

// ── Upload with annotations (image + JSON GT pair) ──────────────────────────

export function uploadDocumentWithAnnotations(file: File, annotationsFile: File) {
  const form = new FormData()
  form.append('file', file)
  form.append('annotations', annotationsFile)
  return apiClient.post('/api/v1/documents/upload-with-annotations', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
