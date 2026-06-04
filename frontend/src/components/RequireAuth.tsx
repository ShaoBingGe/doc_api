import { useEffect } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/auth-store'
import type { UserRole } from '../lib/api-client'

/**
 * Route guard. Redirects unauthenticated users to a login page and, when
 * `roles` is provided, blocks users whose role isn't allowed.
 *
 *  - `loginPath` lets the admin area bounce to /admin/login instead of /login.
 */
export default function RequireAuth({
  children,
  roles,
  loginPath = '/login',
}: {
  children: React.ReactNode
  roles?: UserRole[]
  loginPath?: string
}) {
  const location = useLocation()
  const token = useAuthStore((s) => s.token)
  const user = useAuthStore((s) => s.user)
  const initializing = useAuthStore((s) => s.initializing)
  const refresh = useAuthStore((s) => s.refresh)

  // Validate a persisted token once on mount.
  useEffect(() => {
    if (token && initializing) refresh()
  }, [token, initializing, refresh])

  if (token && initializing) {
    return (
      <div className="min-h-screen flex items-center justify-center text-sm text-gray-400">
        加载中…
      </div>
    )
  }

  if (!token || !user) {
    return <Navigate to={loginPath} replace state={{ from: location.pathname }} />
  }

  if (roles && !roles.includes(user.role)) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-2 text-center px-4">
        <p className="text-lg font-semibold text-gray-900">无权访问</p>
        <p className="text-sm text-gray-500">
          当前角色（{user.role}）无权访问此页面。
        </p>
        <Navigate to="/" replace />
      </div>
    )
  }

  return <>{children}</>
}
