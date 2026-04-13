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

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response) {
      const status = error.response.status
      if (status === 401) {
        console.error('Unauthorized — check your API key in Settings.')
      } else if (status === 404) {
        console.error('Resource not found:', error.config?.url)
      } else if (status >= 500) {
        console.error('Server error:', error.response.data)
      }
    } else if (error.request) {
      console.error('Network error — is the API server running at', baseURL)
    }
    return Promise.reject(error)
  },
)

export default apiClient

export function reprocessDocument(docId: string, processorType?: string) {
  return apiClient.post(`/api/v1/documents/${docId}/reprocess`, {
    processor_type: processorType ?? null,
  })
}

export function uploadDocument(formData: FormData) {
  return apiClient.post('/api/v1/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
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

export function fetchUsageStats(range?: string) {
  return apiClient.get('/api/v1/usage/stats', { params: { range: range ?? '7d' } })
}

export function triggerOptimization(apiDefId: string) {
  return apiClient.post(`/api/v1/api-definitions/${apiDefId}/optimize`)
}

export function fetchPromptVersions(apiDefId: string) {
  return apiClient.get(`/api/v1/api-definitions/${apiDefId}/prompt-versions`)
}

export function activatePromptVersion(apiDefId: string, versionId: string) {
  return apiClient.patch(`/api/v1/api-definitions/${apiDefId}/prompt-versions/${versionId}/activate`)
}
