import { useRef, useState } from 'react'
import { Plus, Trash2, FileText, Image as ImageIcon, Loader2 } from 'lucide-react'
import { cn } from '../../lib/utils'
import { useWorkspaceStore } from '../../stores/workspace-store'
import { toast } from '../../lib/toast'

/**
 * Left rail (80px wide) for the batch sample workspace.
 * Lists all sample documents bound to the current ApiDefinition.
 *
 * Behavior:
 *  - Click thumbnail → select that doc (drives main panels)
 *  - Hover thumbnail → reveals delete button
 *  - Click "+" at the bottom → file picker → upload and append to sample set
 */

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.xlsx'
const MAX_SIZE = 50 * 1024 * 1024

export default function DocumentThumbnailColumn() {
  const {
    documents,
    selectedDocId,
    samplesLoading,
    uploadingSample,
    apiDefinitionId,
    selectDocument,
    addSampleDocument,
    removeSampleDocument,
  } = useWorkspaceStore()

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (file.size > MAX_SIZE) {
      toast.error('文件大小不能超过 50MB')
      return
    }
    addSampleDocument(file)
  }

  function isImage(ft: string): boolean {
    return ['png', 'jpg', 'jpeg', 'image'].includes(ft.toLowerCase())
  }

  if (!apiDefinitionId) {
    return null
  }

  return (
    <div className="w-[88px] flex-shrink-0 bg-[#16161a] border-r border-white/5 flex flex-col">
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={handleFileChange}
      />

      {/* Header */}
      <div className="px-2 py-2 border-b border-white/5">
        <div className="text-[10px] uppercase tracking-wider text-gray-500 text-center">
          样本 {documents.length > 0 && `(${documents.length})`}
        </div>
      </div>

      {/* Thumbnails list */}
      <div className="flex-1 overflow-y-auto py-2 px-2 space-y-2">
        {samplesLoading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="w-4 h-4 animate-spin text-gray-500" />
          </div>
        ) : documents.length === 0 ? (
          <div className="text-[10px] text-gray-600 text-center px-1 py-4 leading-tight">
            尚无样本，
            <br />
            点 + 上传
          </div>
        ) : (
          documents.map((doc, idx) => {
            const selected = doc.id === selectedDocId
            const Icon = isImage(doc.fileType) ? ImageIcon : FileText
            return (
              <div key={doc.id} className="relative group">
                <button
                  onClick={() => selectDocument(doc.id)}
                  className={cn(
                    'w-full aspect-[3/4] rounded-md flex flex-col items-center justify-center gap-1 transition-all border-2',
                    selected
                      ? 'border-purple-500 bg-purple-500/10 ring-2 ring-purple-500/30'
                      : 'border-white/5 bg-[#1e1e24] hover:border-purple-500/40 hover:bg-purple-500/5',
                  )}
                  title={doc.filename}
                >
                  <Icon
                    className={cn(
                      'w-6 h-6',
                      selected ? 'text-purple-400' : 'text-gray-500',
                    )}
                  />
                  <span className="text-[10px] text-gray-400 font-mono">
                    #{idx + 1}
                  </span>
                  <span className="text-[9px] text-gray-500 truncate max-w-full px-1">
                    {doc.filename}
                  </span>
                </button>
                {/* Delete button — visible on hover */}
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    if (confirmDelete === doc.id) {
                      removeSampleDocument(doc.id)
                      setConfirmDelete(null)
                    } else {
                      setConfirmDelete(doc.id)
                      setTimeout(() => setConfirmDelete(null), 2500)
                    }
                  }}
                  className={cn(
                    'absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center text-white transition-all',
                    confirmDelete === doc.id
                      ? 'bg-red-600 opacity-100'
                      : 'bg-gray-700 opacity-0 group-hover:opacity-100 hover:bg-red-600',
                  )}
                  title={confirmDelete === doc.id ? '再次点击确认删除' : '从样本集移除'}
                >
                  <Trash2 className="w-2.5 h-2.5" />
                </button>
              </div>
            )
          })
        )}
      </div>

      {/* Upload button */}
      <div className="p-2 border-t border-white/5">
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploadingSample}
          className={cn(
            'w-full aspect-square rounded-md flex flex-col items-center justify-center gap-0.5 border-2 border-dashed transition-all',
            uploadingSample
              ? 'border-purple-500/40 bg-purple-500/5'
              : 'border-white/10 hover:border-purple-500/50 hover:bg-purple-500/5',
          )}
          title="添加样本文档"
        >
          {uploadingSample ? (
            <Loader2 className="w-4 h-4 animate-spin text-purple-400" />
          ) : (
            <>
              <Plus className="w-5 h-5 text-gray-500" />
              <span className="text-[9px] text-gray-500">添加</span>
            </>
          )}
        </button>
      </div>
    </div>
  )
}
