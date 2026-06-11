import { useRef, useState } from 'react'
import { Upload, Loader2, FileJson, Image as ImageIcon, X, Check } from 'lucide-react'
import apiClient, { OCR_TIMEOUT, uploadDocumentWithAnnotations } from '../../lib/api-client'
import { toast } from '../../lib/toast'
import { cn } from '../../lib/utils'

interface InlineUploadPanelProps {
  onUploadComplete: (documentId: string) => void
  /** When provided, the upload is bound to this ApiDefinition and the backend
   *  auto-runs OCR with that API's active OcrPromptVersion.composed_prompt
   *  (§6.4 country-template flow). */
  apiDefinitionId?: string
}

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.xlsx'
const JSON_ACCEPT = '.json,application/json'
const ALLOWED_TYPES = [
  'application/pdf',
  'image/png',
  'image/jpeg',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
]
const MAX_SIZE = 50 * 1024 * 1024 // 50 MB
const MAX_JSON_SIZE = 5 * 1024 * 1024 // 5 MB for the GT json

type UploadMode = 'new' | 'labeled'

export default function InlineUploadPanel({ onUploadComplete, apiDefinitionId }: InlineUploadPanelProps) {
  const [mode, setMode] = useState<UploadMode>('new')

  return (
    <div className="flex flex-col h-full bg-[#1e1e24]">
      {/* Top tabs */}
      <div className="flex border-b border-white/10">
        <TabButton
          active={mode === 'new'}
          onClick={() => setMode('new')}
          icon={<Upload className="w-3.5 h-3.5" />}
          label="上传新文档"
        />
        <TabButton
          active={mode === 'labeled'}
          onClick={() => setMode('labeled')}
          icon={<FileJson className="w-3.5 h-3.5" />}
          label="上传已标注数据"
        />
      </div>

      {/* Tab content */}
      <div className="flex-1 flex items-center justify-center overflow-y-auto">
        {mode === 'new' ? (
          <UploadNewDoc onUploadComplete={onUploadComplete} apiDefinitionId={apiDefinitionId} />
        ) : (
          <UploadLabeledPair onUploadComplete={onUploadComplete} apiDefinitionId={apiDefinitionId} />
        )}
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 px-4 py-2 text-xs transition-colors',
        active
          ? 'text-purple-300 border-b-2 border-purple-500 bg-purple-500/5'
          : 'text-gray-400 border-b-2 border-transparent hover:text-gray-200 hover:bg-white/5',
      )}
    >
      {icon}
      {label}
    </button>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Tab A — upload single doc (existing behavior)
// ──────────────────────────────────────────────────────────────────────────────

function UploadNewDoc({ onUploadComplete, apiDefinitionId }: InlineUploadPanelProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadingFilename, setUploadingFilename] = useState('')
  const [error, setError] = useState('')

  const validateFile = (file: File): boolean => {
    const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
    const validExt = ['pdf', 'png', 'jpg', 'jpeg', 'xlsx'].includes(ext)
    if (!ALLOWED_TYPES.includes(file.type) && !validExt) {
      toast.error('仅支持 PDF, PNG, JPG, XLSX 格式')
      return false
    }
    if (file.size > MAX_SIZE) {
      toast.error('文件大小不能超过 50MB')
      return false
    }
    return true
  }

  const handleUpload = async (file: File) => {
    if (!validateFile(file)) return
    setError('')
    setUploading(true)
    setUploadingFilename(file.name)

    const formData = new FormData()
    formData.append('file', file)
    if (apiDefinitionId) formData.append('api_definition_id', apiDefinitionId)

    try {
      const res = await apiClient.post('/api/v1/documents/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: OCR_TIMEOUT,  // §6.4 auto-OCR 同步执行；qwen3-vl-plus 多页可达数分钟
      })
      const documentId: string = res.data.id
      toast.success(apiDefinitionId ? '文档上传成功，已完成首次识别' : '文档上传成功')
      onUploadComplete(documentId)
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        '上传失败，请检查网络后重试'
      const errorText = typeof msg === 'string' ? msg : JSON.stringify(msg)
      setError(errorText)
      toast.error(errorText)
    } finally {
      setUploading(false)
    }
  }

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) handleUpload(f)
          e.target.value = ''
        }}
      />
      {uploading ? (
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="w-10 h-10 animate-spin text-purple-500" />
          <p className="text-sm text-gray-300">{uploadingFilename}</p>
          <p className="text-xs text-gray-500">正在上传...</p>
        </div>
      ) : (
        <div
          className={cn(
            'flex flex-col items-center justify-center gap-4 w-[360px] h-[260px] rounded-xl border-2 border-dashed cursor-pointer transition-all',
            dragOver
              ? 'border-purple-500 bg-purple-500/10'
              : 'border-[#3a3a42] hover:border-purple-500/50 hover:bg-purple-500/5',
          )}
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragOver(false)
            const f = e.dataTransfer.files[0]
            if (f) handleUpload(f)
          }}
        >
          <Upload className="w-10 h-10 text-gray-500" />
          <p className="text-sm text-gray-300 font-medium">拖拽或点击上传文档</p>
          <p className="text-xs text-gray-500">支持 PDF, PNG, JPG, XLSX (最大 50MB)</p>
          {error && (
            <p className="text-xs text-red-400 mt-1 max-w-[300px] text-center">{error}</p>
          )}
        </div>
      )}
    </>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Tab B — upload image + JSON GT pair
// ──────────────────────────────────────────────────────────────────────────────

interface JsonLintResult {
  ok: boolean
  error?: string
  annotationCount?: number
}

function UploadLabeledPair({ onUploadComplete, apiDefinitionId: _apiDefinitionId }: InlineUploadPanelProps) {
  // The labeled-pair upload uses /documents/upload-with-annotations which doesn't
  // accept api_definition_id (annotations are the GT, not the placeholder API flow).
  // Prop received to satisfy the typed parent; intentionally unused.
  const fileRef = useRef<HTMLInputElement>(null)
  const jsonRef = useRef<HTMLInputElement>(null)
  const [docFile, setDocFile] = useState<File | null>(null)
  const [jsonFile, setJsonFile] = useState<File | null>(null)
  const [jsonLint, setJsonLint] = useState<JsonLintResult | null>(null)
  const [uploading, setUploading] = useState(false)

  const handleDocChange = (f: File | null) => {
    if (!f) return
    const ext = f.name.split('.').pop()?.toLowerCase() ?? ''
    if (!['pdf', 'png', 'jpg', 'jpeg', 'xlsx'].includes(ext)) {
      toast.error('数据文件仅支持 PDF, PNG, JPG, XLSX')
      return
    }
    if (f.size > MAX_SIZE) {
      toast.error('数据文件不能超过 50MB')
      return
    }
    setDocFile(f)
  }

  const handleJsonChange = async (f: File | null) => {
    if (!f) return
    if (!f.name.toLowerCase().endsWith('.json')) {
      toast.error('JSON 标注文件必须以 .json 结尾')
      return
    }
    if (f.size > MAX_JSON_SIZE) {
      toast.error('JSON 文件不能超过 5MB')
      return
    }
    setJsonFile(f)

    // Lint immediately
    try {
      const text = await f.text()
      const parsed = JSON.parse(text)
      if (typeof parsed !== 'object' || parsed === null) {
        setJsonLint({ ok: false, error: 'JSON 顶层必须是对象' })
        return
      }
      const arr = (parsed as { annotations?: unknown }).annotations
      if (!Array.isArray(arr) || arr.length === 0) {
        setJsonLint({ ok: false, error: '缺少非空的 annotations 数组' })
        return
      }
      for (let i = 0; i < arr.length; i++) {
        const item = arr[i] as Record<string, unknown>
        if (!item || typeof item !== 'object') {
          setJsonLint({ ok: false, error: `annotations[${i}] 不是对象` })
          return
        }
        if (typeof item.field_path !== 'string') {
          setJsonLint({ ok: false, error: `annotations[${i}] 缺少字符串字段 field_path` })
          return
        }
        if (!('value' in item)) {
          setJsonLint({ ok: false, error: `annotations[${i}] 缺少 value 字段` })
          return
        }
      }
      setJsonLint({ ok: true, annotationCount: arr.length })
    } catch (e) {
      setJsonLint({
        ok: false,
        error: `JSON 解析失败: ${e instanceof Error ? e.message : 'unknown'}`,
      })
    }
  }

  const handleSubmit = async () => {
    if (!docFile || !jsonFile || !jsonLint?.ok) return
    setUploading(true)
    try {
      const res = await uploadDocumentWithAnnotations(docFile, jsonFile)
      const documentId: string = res.data.id
      toast.success(`已上传并写入 ${jsonLint.annotationCount} 条标注`)
      onUploadComplete(documentId)
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        '上传失败，请检查网络后重试'
      toast.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setUploading(false)
    }
  }

  const canSubmit = docFile && jsonFile && jsonLint?.ok && !uploading

  return (
    <div className="w-[420px] py-6 space-y-3">
      <div className="text-center mb-2">
        <h3 className="text-sm font-medium text-gray-200">上传已标注样本</h3>
        <p className="text-xs text-gray-500 mt-1">
          图片/PDF + 配套 JSON 标注文件（详见 docs/UI_DESIGN.md §12.4）
        </p>
      </div>

      <input
        ref={fileRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          handleDocChange(e.target.files?.[0] ?? null)
          e.target.value = ''
        }}
      />
      <input
        ref={jsonRef}
        type="file"
        accept={JSON_ACCEPT}
        className="hidden"
        onChange={(e) => {
          handleJsonChange(e.target.files?.[0] ?? null)
          e.target.value = ''
        }}
      />

      <FileSlot
        label="① 数据文件（图片/PDF/XLSX）"
        file={docFile}
        icon={<ImageIcon className="w-4 h-4" />}
        accept="PDF, PNG, JPG, XLSX"
        onPick={() => fileRef.current?.click()}
        onClear={() => setDocFile(null)}
      />

      <FileSlot
        label="② 标注 JSON 文件"
        file={jsonFile}
        icon={<FileJson className="w-4 h-4" />}
        accept=".json"
        onPick={() => jsonRef.current?.click()}
        onClear={() => {
          setJsonFile(null)
          setJsonLint(null)
        }}
        statusLine={
          jsonLint
            ? jsonLint.ok
              ? `✓ JSON 合法 (${jsonLint.annotationCount} 条 annotation)`
              : `✗ ${jsonLint.error}`
            : null
        }
        statusOk={jsonLint?.ok ?? null}
      />

      <button
        onClick={handleSubmit}
        disabled={!canSubmit}
        className={cn(
          'w-full mt-3 py-2 text-sm rounded-md font-medium transition-colors flex items-center justify-center gap-2',
          canSubmit
            ? 'bg-purple-600 hover:bg-purple-700 text-white'
            : 'bg-white/5 text-gray-500 cursor-not-allowed',
        )}
      >
        {uploading ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" /> 上传中...
          </>
        ) : (
          <>
            <Upload className="w-4 h-4" /> 上传并创建
          </>
        )}
      </button>
    </div>
  )
}

function FileSlot({
  label,
  file,
  icon,
  accept,
  onPick,
  onClear,
  statusLine,
  statusOk,
}: {
  label: string
  file: File | null
  icon: React.ReactNode
  accept: string
  onPick: () => void
  onClear: () => void
  statusLine?: string | null
  statusOk?: boolean | null
}) {
  return (
    <div className="rounded-lg border border-[#3a3a42] bg-[#15151a] p-3">
      <div className="text-[11px] text-gray-400 mb-2">{label}</div>
      {file ? (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-purple-400">{icon}</span>
          <span className="flex-1 truncate text-gray-200" title={file.name}>
            {file.name}
          </span>
          <span className="text-gray-500">{(file.size / 1024).toFixed(1)} KB</span>
          <button
            onClick={onClear}
            className="text-gray-500 hover:text-red-400"
            title="移除"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ) : (
        <button
          onClick={onPick}
          className="w-full flex items-center gap-2 px-3 py-2 text-xs text-gray-400 hover:text-purple-300 border border-dashed border-[#3a3a42] hover:border-purple-500/50 rounded-md transition-colors"
        >
          {icon}
          <span>点击选择 {accept}</span>
        </button>
      )}
      {statusLine && (
        <div
          className={cn(
            'mt-2 text-[11px] flex items-center gap-1',
            statusOk ? 'text-emerald-400' : 'text-red-400',
          )}
        >
          {statusOk && <Check className="w-3 h-3" />}
          {statusLine}
        </div>
      )}
    </div>
  )
}
