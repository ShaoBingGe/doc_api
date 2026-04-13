import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { Home } from 'lucide-react'

const tabs = [
  { to: '/settings/users', label: '用户管理' },
  { to: '/settings/api-keys', label: 'API Key' },
  { to: '/settings/traffic', label: '流量监控' },
  { to: '/settings/billing', label: '账单续费' },
]

export default function SettingsLayout() {
  const navigate = useNavigate()

  return (
    <div className="min-h-screen bg-white flex flex-col">
      {/* Top bar */}
      <header className="flex items-center gap-4 px-6 py-4 border-b border-gray-200">
        <button
          onClick={() => navigate('/')}
          className="p-2 rounded-lg text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          title="返回首页"
        >
          <Home className="w-5 h-5" />
        </button>
        <h1 className="text-lg font-semibold text-gray-900">设置中心</h1>
      </header>

      {/* Tab navigation */}
      <nav className="flex gap-6 px-6 border-b border-gray-200">
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

      {/* Content */}
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
