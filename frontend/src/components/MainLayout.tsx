import { useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { Plus, BookTemplate, Settings, Sparkles, LogOut, ShieldCheck } from 'lucide-react'
import TemplateBrowserModal from './templates/TemplateBrowserModal'
import { useAuthStore, ROLE_LABELS } from '../stores/auth-store'

export default function MainLayout() {
  const navigate = useNavigate()
  const [templateModalOpen, setTemplateModalOpen] = useState(false)
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const isPlatformAdmin = user?.role === 'super_admin' || user?.role === 'system_admin'

  function handleLogout() {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-white flex flex-col">
      {/* Top bar */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
        <span className="text-lg font-semibold text-gray-900 tracking-tight">
          ApiAnything
        </span>

        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/workspace/new')}
            className="animate-gradient-flow inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white shadow-sm hover:shadow-md transition-shadow"
          >
            <Plus className="w-4 h-4" />
            定制新 API
          </button>

          <button
            onClick={() => setTemplateModalOpen(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 transition-colors shadow-sm"
          >
            <BookTemplate className="w-4 h-4" />
            订阅模板
          </button>

          {isPlatformAdmin && (
            <>
              <button
                onClick={() => navigate('/settings/ocr-optimizer')}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-indigo-700 bg-indigo-50 hover:bg-indigo-100 transition-colors"
                title="查看 OCR Prompt 优化器所有字段"
              >
                <Sparkles className="w-4 h-4" />
                OCR 优化器
              </button>
              <button
                onClick={() => navigate('/admin')}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-slate-700 bg-slate-100 hover:bg-slate-200 transition-colors"
                title="管理控制台"
              >
                <ShieldCheck className="w-4 h-4" />
                管理控制台
              </button>
            </>
          )}

          <button
            onClick={() => navigate('/settings')}
            className="p-2 rounded-lg text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
            title="设置"
          >
            <Settings className="w-5 h-5" />
          </button>

          {user && (
            <div className="flex items-center gap-2 pl-2 ml-1 border-l border-gray-200">
              <span className="text-sm text-gray-600 hidden sm:inline">
                {user.display_name || user.email}
                <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
                  {ROLE_LABELS[user.role]}
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
          )}
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>

      {/* Template browser modal */}
      <TemplateBrowserModal
        isOpen={templateModalOpen}
        onClose={() => setTemplateModalOpen(false)}
      />
    </div>
  )
}
