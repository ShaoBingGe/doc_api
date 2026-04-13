import { useEffect, useState } from 'react'
import { Key, Plus, Trash2, Copy, Check, Loader2, Info } from 'lucide-react'
import { useApiStore, type ApiKey } from '../../stores/api-store'
import { toast } from '../../lib/toast'

function NewKeyModal({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (rawKey: string) => void
}) {
  const { createApiKey } = useApiStore()
  const [name, setName] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setIsLoading(true)
    try {
      const result = await createApiKey(name.trim())
      toast.success('API Key 创建成功')
      onCreated(result.raw_key)
    } catch {
      toast.error('创建 API Key 失败，请重试')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <h2 className="text-base font-semibold text-gray-900 mb-4">创建新 Key</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Key 名称
            </label>
            <input
              autoFocus
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如 Production、Development..."
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={!name.trim() || isLoading}
              className="flex items-center gap-2 px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
            >
              {isLoading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              创建
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function CopyButton({ keyPrefix }: { keyPrefix: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(keyPrefix)
    setCopied(true)
    toast.success('已复制 Key 前缀')
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      onClick={handleCopy}
      className="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
      title="复制前缀"
    >
      {copied ? (
        <Check className="w-3.5 h-3.5 text-green-500" />
      ) : (
        <Copy className="w-3.5 h-3.5" />
      )}
    </button>
  )
}

function DeleteButton({ apiKey }: { apiKey: ApiKey }) {
  const { deleteApiKey } = useApiStore()

  const handleDelete = async () => {
    if (!confirm(`确认删除 Key "${apiKey.name}"？`)) return
    try {
      await deleteApiKey(apiKey.id)
      toast.success('API Key 已删除')
    } catch {
      toast.error('删除失败，请重试')
    }
  }

  return (
    <button
      onClick={handleDelete}
      className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
      title="删除 Key"
    >
      <Trash2 className="w-3.5 h-3.5" />
    </button>
  )
}

export default function ApiKeyManagement() {
  const { apiKeys, fetchApiKeys } = useApiStore()
  const [showModal, setShowModal] = useState(false)
  const [newRawKey, setNewRawKey] = useState<string | null>(null)
  const [rawKeyCopied, setRawKeyCopied] = useState(false)

  useEffect(() => {
    fetchApiKeys()
  }, [fetchApiKeys])

  const handleCopyRawKey = () => {
    if (!newRawKey) return
    navigator.clipboard.writeText(newRawKey)
    setRawKeyCopied(true)
    toast.success('已复制完整 Key')
    setTimeout(() => setRawKeyCopied(false), 2000)
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">API Key 管理</h1>
          <p className="text-sm text-gray-500 mt-1">管理您的 API 密钥，用于接口鉴权</p>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
        >
          <Plus className="w-4 h-4" />
          创建新 Key
        </button>
      </div>

      {/* Info banner */}
      <div className="flex items-start gap-3 p-4 bg-blue-50 border border-blue-100 rounded-xl">
        <Info className="w-4 h-4 text-blue-500 mt-0.5 flex-shrink-0" />
        <p className="text-sm text-blue-700">
          每个 API Key 支持 ~3 req/sec 并发，如需更高吞吐请创建多个 Key
        </p>
      </div>

      {/* New raw key alert */}
      {newRawKey && (
        <div className="p-4 bg-amber-50 border border-amber-200 rounded-xl">
          <p className="text-xs font-semibold text-amber-800 mb-2">
            请立即复制此 Key -- 关闭后将无法再次查看：
          </p>
          <code className="text-xs font-mono text-amber-900 break-all block mb-3 bg-amber-100 p-2 rounded-lg">
            {newRawKey}
          </code>
          <div className="flex items-center gap-3">
            <button
              onClick={handleCopyRawKey}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-amber-700 text-white rounded-lg hover:bg-amber-800 transition-colors"
            >
              {rawKeyCopied ? (
                <>
                  <Check className="w-3 h-3" /> 已复制
                </>
              ) : (
                <>
                  <Copy className="w-3 h-3" /> 复制
                </>
              )}
            </button>
            <button
              onClick={() => {
                setNewRawKey(null)
                setRawKeyCopied(false)
              }}
              className="text-xs text-amber-700 underline hover:text-amber-900"
            >
              确认已保存，关闭
            </button>
          </div>
        </div>
      )}

      {/* Key table */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        {apiKeys.length === 0 ? (
          <div className="py-16 text-center">
            <Key className="w-8 h-8 text-gray-300 mx-auto mb-3" />
            <p className="text-sm text-gray-400">暂无 API Key，点击上方按钮创建</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-200 bg-gray-50">
                  <th className="text-left text-xs font-medium text-gray-500 px-6 py-3">名称</th>
                  <th className="text-left text-xs font-medium text-gray-500 py-3">Key 前缀</th>
                  <th className="text-left text-xs font-medium text-gray-500 py-3">速率限制</th>
                  <th className="text-left text-xs font-medium text-gray-500 py-3">最后使用</th>
                  <th className="text-left text-xs font-medium text-gray-500 py-3">创建时间</th>
                  <th className="text-right text-xs font-medium text-gray-500 px-6 py-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {apiKeys.map((k) => (
                  <tr key={k.id} className="border-b border-gray-100 last:border-0">
                    <td className="py-3 px-6">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 bg-gray-100 rounded-lg flex items-center justify-center flex-shrink-0">
                          <Key className="w-4 h-4 text-gray-500" />
                        </div>
                        <span className="text-sm font-medium text-gray-900">{k.name}</span>
                      </div>
                    </td>
                    <td className="py-3">
                      <code className="text-xs text-gray-500 font-mono">{k.key_prefix}••••••••</code>
                    </td>
                    <td className="py-3">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700">
                        180/min
                      </span>
                    </td>
                    <td className="py-3 text-xs text-gray-400">—</td>
                    <td className="py-3 text-xs text-gray-400">
                      {new Date(k.created_at).toLocaleDateString('zh-CN')}
                    </td>
                    <td className="py-3 px-6 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <CopyButton keyPrefix={k.key_prefix} />
                        <DeleteButton apiKey={k} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showModal && (
        <NewKeyModal
          onClose={() => setShowModal(false)}
          onCreated={(rawKey) => {
            setNewRawKey(rawKey)
            setShowModal(false)
          }}
        />
      )}
    </div>
  )
}
