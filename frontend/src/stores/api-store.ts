import { create } from 'zustand'
import {
  fetchApiDefinitions as apiFetchDefinitions,
  fetchApiKeys as apiFetchKeys,
  createApiKey as apiCreateKey,
  deleteApiKey as apiDeleteKey,
  deleteApiDefinition as apiDeleteDefinition,
} from '../lib/api-client'

// Backend response shape from GET /api/v1/api-definitions
export interface ApiDefinition {
  id: string
  name: string
  description: string
  api_code: string
  status: string
  created_at: string
  endpoint_url?: string
  sample_document_id?: string | null
  source_type?: string
  response_schema?: Record<string, unknown> | null
  config?: Record<string, unknown> | null
}

// Backend response shape from GET /api/v1/api-keys
export interface ApiKey {
  id: string
  name: string
  key_prefix: string
  created_at: string
  is_active: boolean
}

interface ApiStore {
  apiDefinitions: ApiDefinition[]
  apiKeys: ApiKey[]
  isLoading: boolean
  error: string | null

  fetchApiDefinitions: () => Promise<void>
  fetchApiKeys: () => Promise<void>
  createApiKey: (name: string) => Promise<{ raw_key: string; record: ApiKey }>
  deleteApiKey: (id: string) => Promise<void>
  deleteApiDefinition: (id: string) => Promise<void>
}

export const useApiStore = create<ApiStore>((set) => ({
  apiDefinitions: [],
  apiKeys: [],
  isLoading: false,
  error: null,

  fetchApiDefinitions: async () => {
    set({ isLoading: true, error: null })
    try {
      const { data } = await apiFetchDefinitions()
      // Backend returns paginated: { items: [...], total, page, ... }
      const items = Array.isArray(data) ? data : (data.items ?? [])
      set({ apiDefinitions: items, isLoading: false })
    } catch {
      set({ error: 'Failed to load API definitions', isLoading: false })
    }
  },

  fetchApiKeys: async () => {
    set({ isLoading: true, error: null })
    try {
      const { data } = await apiFetchKeys()
      set({ apiKeys: data, isLoading: false })
    } catch {
      set({ error: 'Failed to load API keys', isLoading: false })
    }
  },

  createApiKey: async (name: string) => {
    const { data } = await apiCreateKey({ name })
    const record: ApiKey = {
      id: data.id,
      name: data.name,
      key_prefix: data.key_prefix,
      created_at: data.created_at ?? new Date().toISOString(),
      is_active: true,
    }
    set((state) => ({ apiKeys: [record, ...state.apiKeys] }))
    return { raw_key: data.raw_key, record }
  },

  deleteApiKey: async (id: string) => {
    await apiDeleteKey(id)
    set((state) => ({
      apiKeys: state.apiKeys.filter((k) => k.id !== id),
    }))
  },

  deleteApiDefinition: async (id: string) => {
    await apiDeleteDefinition(id)
    set((state) => ({
      apiDefinitions: state.apiDefinitions.filter((a) => a.id !== id),
    }))
  },
}))
