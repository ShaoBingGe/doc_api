import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, Trash2, Code2, Plus, BookTemplate, Rocket, PauseCircle } from 'lucide-react'
import { useApiStore } from '../stores/api-store'
import { updateApiStatus } from '../lib/api-client'
import { toast } from '../lib/toast'

const statusStyles: Record<string, string> = {
  active: 'bg-green-50 text-green-700',
  pending_review: 'bg-amber-50 text-amber-700',
  draft: 'bg-gray-100 text-gray-500',
  pending_first_doc: 'bg-gray-100 text-gray-400',
  deprecated: 'bg-red-50 text-red-600',
}

// Chinese display labels for the lifecycle states.
const statusLabels: Record<string, string> = {
  active: '已发布',
  pending_review: '待验证',
  draft: '草稿',
  pending_first_doc: '未发布',
  deprecated: '已停用',
}

const sourceStyles: Record<string, string> = {
  custom: 'bg-purple-50 text-purple-700',
  template: 'bg-blue-50 text-blue-700',
}

export default function ApiList() {
  const navigate = useNavigate()
  const { apiDefinitions, isLoading, fetchApiDefinitions, deleteApiDefinition } = useApiStore()

  useEffect(() => {
    fetchApiDefinitions()
  }, [fetchApiDefinitions])

  function handleRowClick(api: typeof apiDefinitions[0]) {
    // v3: navigate by API definition (batch sample set is loaded inside Workspace)
    navigate(`/workspace/api/${api.id}`)
  }

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation()
    try {
      await deleteApiDefinition(id)
      toast.success('已删除')
    } catch {
      toast.error('删除失败')
    }
  }

  async function handleActivate(e: React.MouseEvent, id: string) {
    e.stopPropagation()
    try {
      await updateApiStatus(id, 'activate')
      toast.success('已激活发布')
      await fetchApiDefinitions()
    } catch {
      toast.error('激活失败')
    }
  }

  async function handleDeactivate(e: React.MouseEvent, id: string) {
    e.stopPropagation()
    try {
      await updateApiStatus(id, 'deactivate')
      toast.success('已停用')
      await fetchApiDefinitions()
    } catch {
      toast.error('停用失败')
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-32">
        <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    )
  }

  if (apiDefinitions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-32 px-8">
        <Code2 className="w-16 h-16 text-gray-200 mb-6" />
        <p className="text-lg font-medium text-gray-500 mb-2">还没有定制的 API</p>
        <p className="text-sm text-gray-400 mb-8">创建自定义 API 或订阅公开模板，开始使用</p>
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/workspace/new')}
            className="animate-gradient-flow inline-flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium text-white shadow-sm hover:shadow-md transition-shadow"
          >
            <Plus className="w-4 h-4" />
            定制新 API
          </button>
          <button
            onClick={() => {
              /* TemplateBrowserModal is in MainLayout; navigate home to open it */
              toast.info('请使用顶部"订阅模板"按钮浏览模板')
            }}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 transition-colors shadow-sm"
          >
            <BookTemplate className="w-4 h-4" />
            订阅模板
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="overflow-hidden rounded-xl border border-gray-200">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">名称</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">API Code</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">来源</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">状态</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">创建时间</th>
              <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">操作</th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-100">
            {apiDefinitions.map((api) => (
              <tr
                key={api.id}
                onClick={() => handleRowClick(api)}
                className="hover:bg-gray-50 cursor-pointer transition-colors"
              >
                <td className="px-6 py-4 whitespace-nowrap">
                  <span className="text-sm font-semibold text-gray-900">{api.name}</span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <code className="text-xs font-mono bg-gray-100 text-gray-700 px-2 py-1 rounded">
                    {api.api_code}
                  </code>
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <span
                    className={[
                      'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium',
                      sourceStyles[api.source_type ?? 'custom'] ?? sourceStyles.custom,
                    ].join(' ')}
                  >
                    {api.source_type ?? 'custom'}
                  </span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <span
                    className={[
                      'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium',
                      statusStyles[api.status] ?? 'bg-gray-100 text-gray-500',
                    ].join(' ')}
                  >
                    {statusLabels[api.status] ?? api.status}
                  </span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  {new Date(api.created_at).toLocaleDateString()}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-right">
                  <div className="flex items-center justify-end gap-1.5">
                    {api.status === 'active' ? (
                      <button
                        onClick={(e) => handleDeactivate(e, api.id)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium text-amber-700 bg-amber-50 hover:bg-amber-100 transition-colors"
                        title="停用：停止对外提供服务（可再次激活）"
                      >
                        <PauseCircle className="w-3.5 h-3.5" />
                        停用
                      </button>
                    ) : (
                      <button
                        onClick={(e) => handleActivate(e, api.id)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium text-green-700 bg-green-50 hover:bg-green-100 transition-colors"
                        title="激活发布：对外提供 API 服务"
                      >
                        <Rocket className="w-3.5 h-3.5" />
                        激活发布
                      </button>
                    )}
                    <button
                      onClick={(e) => handleDelete(e, api.id)}
                      className="p-1.5 rounded-lg text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                      title="删除"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
