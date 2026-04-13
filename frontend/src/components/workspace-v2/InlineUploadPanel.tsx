import { useRef, useState } from 'react'
import { Upload, Loader2 } from 'lucide-react'
import apiClient from '../../lib/api-client'
import { toast } from '../../lib/toast'
import { cn } from '../../lib/utils'

interface InlineUploadPanelProps {
  onUploadComplete: (documentId: string) => void
}

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.xlsx'
const ALLOWED_TYPES = [
  'application/pdf',
  'image/png',
  'image/jpeg',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
]
const MAX_SIZE = 50 * 1024 * 1024 // 50 MB

export default function InlineUploadPanel({ onUploadComplete }: InlineUploadPanelProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadingFilename, setUploadingFilename] = useState('')
  const [error, setError] = useState('')

  const validateFile = (file: File): boolean => {
    // Also allow by extension for xlsx which may have generic mime
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

    try {
      const res = await apiClient.post('/api/v1/documents/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      const documentId: string = res.data.id
      toast.success('文档上传成功')
      onUploadComplete(documentId)
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? '上传失败，请检查网络后重试'
      const errorText = typeof msg === 'string' ? msg : JSON.stringify(msg)
      setError(errorText)
      toast.error(errorText)
    } finally {
      setUploading(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) handleUpload(file)
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handleUpload(file)
    // Reset so the same file can be re-selected
    e.target.value = ''
  }

  return (
    <div className="flex items-center justify-center h-full bg-[#1e1e24]">
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={handleFileChange}
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
          onDrop={handleDrop}
        >
          <Upload className="w-10 h-10 text-gray-500" />
          <p className="text-sm text-gray-300 font-medium">
            拖拽或点击上传文档
          </p>
          <p className="text-xs text-gray-500">
            支持 PDF, PNG, JPG, XLSX (最大 50MB)
          </p>
          {error && (
            <p className="text-xs text-red-400 mt-1 max-w-[300px] text-center">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
