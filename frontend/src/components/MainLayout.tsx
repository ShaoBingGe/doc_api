import { useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { Plus, BookTemplate, Settings } from 'lucide-react'
import TemplateBrowserModal from './templates/TemplateBrowserModal'

export default function MainLayout() {
  const navigate = useNavigate()
  const [templateModalOpen, setTemplateModalOpen] = useState(false)

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

          <button
            onClick={() => navigate('/settings')}
            className="p-2 rounded-lg text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
            title="设置"
          >
            <Settings className="w-5 h-5" />
          </button>
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
