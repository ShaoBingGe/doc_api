import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { LogOut, ShieldCheck, Sparkles } from 'lucide-react'
import { useAuthStore, ROLE_LABELS } from '../stores/auth-store'

export default function AdminLayout() {
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  const isSuper = user?.role === 'super_admin'

  const tabs = [
    ...(isSuper ? [{ to: '/admin/system-admins', label: '系统管理员' }] : []),
    { to: '/admin/tenant-admins', label: '用户管理员 / 租户' },
    { to: '/admin/country-templates', label: '国家模板' },
    { to: '/admin/skill-promotion', label: '技能晋升' },
  ]

  function handleLogout() {
    logout()
    navigate('/admin/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      {/* Top bar */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-gray-200 bg-white">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-indigo-600" />
          <span className="text-lg font-semibold text-gray-900 tracking-tight">管理控制台</span>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/settings/ocr-optimizer')}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-indigo-700 bg-indigo-50 hover:bg-indigo-100 transition-colors"
            title="国家模板 / 黄金种子 / 优化迭代"
          >
            <Sparkles className="w-4 h-4" />
            模板优化平台
          </button>
          <span className="text-sm text-gray-500">
            {user?.display_name || user?.email}
            <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700">
              {user ? ROLE_LABELS[user.role] : ''}
            </span>
          </span>
          <button
            onClick={handleLogout}
            className="p-2 rounded-lg text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
            title="退出登录"
          >
            <LogOut className="w-5 h-5" />
          </button>
        </div>
      </header>

      {/* Tabs */}
      <nav className="flex gap-6 px-6 border-b border-gray-200 bg-white">
        {tabs.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              [
                'py-3 text-sm font-medium transition-colors border-b-2',
                isActive
                  ? 'border-indigo-600 text-indigo-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700',
              ].join(' ')
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>

      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  )
}
