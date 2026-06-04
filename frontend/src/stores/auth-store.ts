import { create } from 'zustand'
import {
  loginPassword as apiLoginPassword,
  loginCode as apiLoginCode,
  fetchMe as apiFetchMe,
  type AuthUser,
  type TenantBrief,
  type UserRole,
} from '../lib/api-client'

const TOKEN_KEY = 'auth_token'
const USER_KEY = 'auth_user'
const TENANT_KEY = 'auth_tenant'

function loadJSON<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

interface AuthStore {
  token: string | null
  user: AuthUser | null
  tenant: TenantBrief | null
  initializing: boolean

  loginWithPassword: (email: string, password: string) => Promise<AuthUser>
  loginWithCode: (email: string, code: string) => Promise<AuthUser>
  refresh: () => Promise<void>
  logout: () => void
  // role helpers
  hasRole: (...roles: UserRole[]) => boolean
  isPlatformAdmin: () => boolean
}

function persist(token: string, user: AuthUser, tenant: TenantBrief | null) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  if (tenant) localStorage.setItem(TENANT_KEY, JSON.stringify(tenant))
  else localStorage.removeItem(TENANT_KEY)
}

export const useAuthStore = create<AuthStore>((set, get) => ({
  token: localStorage.getItem(TOKEN_KEY),
  user: loadJSON<AuthUser>(USER_KEY),
  tenant: loadJSON<TenantBrief>(TENANT_KEY),
  initializing: !!localStorage.getItem(TOKEN_KEY),

  loginWithPassword: async (email, password) => {
    const { data } = await apiLoginPassword(email, password)
    persist(data.access_token, data.user, data.tenant)
    set({ token: data.access_token, user: data.user, tenant: data.tenant, initializing: false })
    return data.user
  },

  loginWithCode: async (email, code) => {
    const { data } = await apiLoginCode(email, code)
    persist(data.access_token, data.user, data.tenant)
    set({ token: data.access_token, user: data.user, tenant: data.tenant, initializing: false })
    return data.user
  },

  // Validate the stored token on app boot; clear if it's stale.
  refresh: async () => {
    const token = localStorage.getItem(TOKEN_KEY)
    if (!token) {
      set({ initializing: false })
      return
    }
    try {
      const { data } = await apiFetchMe()
      localStorage.setItem(USER_KEY, JSON.stringify(data.user))
      if (data.tenant) localStorage.setItem(TENANT_KEY, JSON.stringify(data.tenant))
      set({ user: data.user, tenant: data.tenant, token, initializing: false })
    } catch {
      // token invalid/expired → drop it
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
      localStorage.removeItem(TENANT_KEY)
      set({ token: null, user: null, tenant: null, initializing: false })
    }
  },

  logout: () => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    localStorage.removeItem(TENANT_KEY)
    set({ token: null, user: null, tenant: null, initializing: false })
  },

  hasRole: (...roles) => {
    const u = get().user
    return !!u && roles.includes(u.role)
  },
  isPlatformAdmin: () => {
    const u = get().user
    return !!u && (u.role === 'super_admin' || u.role === 'system_admin')
  },
}))

export const ROLE_LABELS: Record<UserRole, string> = {
  super_admin: '超级管理员',
  system_admin: '系统管理员',
  tenant_admin: '用户管理员',
  normal_user: '普通用户',
}
