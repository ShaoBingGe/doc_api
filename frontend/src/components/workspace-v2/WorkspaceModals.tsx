import { useState } from 'react'
import { X, Loader2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useWorkspaceStore } from '../../stores/workspace-store'
import { createApiDefinition, updateApiDefinition } from '../../lib/api-client'
import { toast } from '../../lib/toast'

interface ModalsProps {
  activeModal: 'save' | null
  onClose: () => void
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Generate a URL-safe lowercase api_code from a name + short uuid suffix */
function generateApiCode(name: string, docId: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 24) || 'api'
  const suffix = docId.slice(0, 6).toLowerCase()
  return `${slug}-${suffix}`
}

/** Build a JSON schema preview from structured_data keys */
function buildSchemaPreview(results: { annotationId: string; value: unknown }[], annotations: { id: string; label: string; fieldType: string }[]): Record<string, string> {
  const schema: Record<string, string> = {}
  for (const ann of annotations) {
    const result = results.find((r) => r.annotationId === ann.id)
    const valType = result?.value != null ? typeof result.value : ann.fieldType
    schema[ann.label] = valType === 'number' ? 'number' : 'string'
  }
  return schema
}

// ─── Save modal ───────────────────────────────────────────────────────────────

function SaveModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const { documentInfo, apiDefinition, annotations, processingResults, setApiDefinition } = useWorkspaceStore()

  const isEditing = !!(apiDefinition?.isExisting && apiDefinition?.id)

  const [apiName, setApiName] = useState(
    apiDefinition?.name ||
    (documentInfo?.filename
      ? documentInfo.filename.replace(/\.[^.]+$/, '')
      : '文档提取'),
  )
  const [description, setDescription] = useState(
    apiDefinition?.description || `从 ${documentInfo?.filename ?? '文档'} 生成的提取 API`,
  )
  const [isSaving, setIsSaving] = useState(false)

  const schemaPreview = buildSchemaPreview(processingResults, annotations)

  const handleSave = async () => {
    if (!documentInfo?.id) return
    setIsSaving(true)

    const apiCode = apiDefinition?.apiCode || generateApiCode(apiName, documentInfo.id)

    try {
      if (isEditing && apiDefinition?.id) {
        // Update existing API definition
        const res = await updateApiDefinition(apiDefinition.id, {
          name: apiName.trim() || '文档提取',
          description: description.trim(),
          sample_document_id: documentInfo.id,
        })
        const data = res.data
        const code: string = data.api_code ?? apiCode
        const endpoint = data.endpoint_url || apiDefinition.endpoint
        setApiDefinition({ ...apiDefinition, name: apiName, description, apiCode: code, endpoint })
        toast.success(`API "${apiName}" 已更新`)
      } else {
        // Create new API definition
        const res = await createApiDefinition({
          name: apiName.trim() || '文档提取',
          description: description.trim(),
          api_code: apiCode,
          document_id: documentInfo.id,
          sample_document_id: documentInfo.id,
          processor_type: 'mock',
        })
        const data = res.data
        const code: string = data.api_code ?? apiCode
        const endpoint = data.endpoint_url || `https://api.apianything.io/v1/extract/${code}`
        setApiDefinition({ apiCode: code, endpoint, id: data.id, name: apiName, description, isExisting: true })
        toast.success(`API "${apiName}" 已生成，Code: ${code}`)
      }

      onClose()
      navigate('/')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail ?? (isEditing ? '更新 API 失败，请重试' : '生成 API 失败，请重试')
      toast.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="p-6 text-gray-300 space-y-4">
      <p className="text-sm text-gray-400">
        {isEditing
          ? '更新当前 API 接口配置。'
          : '保存当前配置并生成可供调用的 API 接口。'}
      </p>
      <div className="space-y-3">
        <div>
          <label className="block text-xs text-gray-400 mb-1">API 名称</label>
          <input
            type="text"
            value={apiName}
            onChange={(e) => setApiName(e.target.value)}
            className="w-full bg-[#2a2a32] border border-white/10 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-purple-500 transition-colors"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">描述</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="w-full bg-[#2a2a32] border border-white/10 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-purple-500 transition-colors resize-none"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">生成的 API Code（预览）</label>
          <input
            type="text"
            value={apiDefinition?.apiCode || (documentInfo?.id ? generateApiCode(apiName, documentInfo.id) : '—')}
            readOnly
            className="w-full bg-[#18181c] border border-white/10 rounded px-3 py-2 text-sm text-gray-400 font-mono"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">关联文档</label>
          <input
            type="text"
            value={documentInfo?.filename ?? '—'}
            readOnly
            className="w-full bg-[#18181c] border border-white/10 rounded px-3 py-2 text-sm text-gray-400"
          />
        </div>

        {/* Output schema preview */}
        {Object.keys(schemaPreview).length > 0 && (
          <div>
            <label className="block text-xs text-gray-400 mb-1">出参预览</label>
            <div className="bg-[#18181c] border border-white/10 rounded px-3 py-2 text-xs text-gray-400 font-mono max-h-[140px] overflow-auto">
              <pre className="whitespace-pre-wrap">
                {JSON.stringify(schemaPreview, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </div>
      <div className="pt-2 flex justify-end gap-3">
        <button
          onClick={onClose}
          disabled={isSaving}
          className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors disabled:opacity-50"
        >
          取消
        </button>
        <button
          onClick={handleSave}
          disabled={isSaving || !apiName.trim()}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-purple-600 hover:bg-purple-700 text-white rounded transition-colors disabled:opacity-50"
        >
          {isSaving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {isEditing ? '更新 API' : '确认生成'}
        </button>
      </div>
    </div>
  )
}

// ─── Root ─────────────────────────────────────────────────────────────────────

export default function WorkspaceModals({ activeModal, onClose }: ModalsProps) {
  if (!activeModal) return null

  const { apiDefinition } = useWorkspaceStore.getState()
  const isEditing = !!(apiDefinition?.isExisting && apiDefinition?.id)
  const title = isEditing ? '更新 API' : '保存并生成 API'

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-[#1e1e24] border border-white/10 rounded-xl shadow-2xl w-full max-w-md overflow-hidden">
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10">
          <h2 className="text-lg font-medium text-white">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {activeModal === 'save' && <SaveModal onClose={onClose} />}
      </div>
    </div>
  )
}
