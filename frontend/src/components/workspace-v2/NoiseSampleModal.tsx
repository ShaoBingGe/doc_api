import { useRef, useState } from 'react'
import { X, Upload, Loader2, FileText } from 'lucide-react'
import { cn } from '../../lib/utils'
import { toast } from '../../lib/toast'
import { useWorkspaceStore } from '../../stores/workspace-store'
import { triggerOptimization } from '../../lib/api-client'

interface Props {
  open: boolean
  onClose: () => void
  /** Exact number of noise samples required (= required − 3 anchors). */
  noiseCount: number
}

/** 噪声样本批量上传（ADR-001）。要求一次性选满 noiseCount 张多样化样本，
 *  上传后自动 OCR + 以当前结果为基线 GT（不逐张复核），随即启动迭代。 */
export default function NoiseSampleModal({ open, onClose, noiseCount }: Props) {
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [done, setDone] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const addSampleDocument = useWorkspaceStore((s) => s.addSampleDocument)
  const confirmSampleGT = useWorkspaceStore((s) => s.confirmSampleGT)
  const apiDefinitionId = useWorkspaceStore((s) => s.apiDefinitionId)
  const customizeJob = useWorkspaceStore((s) => s.customizeJob)

  const exactly = files.length === noiseCount

  const handleUpload = async () => {
    if (!exactly || !apiDefinitionId || uploading) return
    setUploading(true)
    setDone(0)
    try {
      for (const f of files) {
        const doc = await addSampleDocument(f)
        if (doc) await confirmSampleGT(doc.id, true) // 自动以当前 OCR 为基线 GT
        setDone((d) => d + 1)
      }
      // 凑满 3 锚点 + N 噪声 → 启动迭代。若处于定制流程，后端确认后会自动续跑；
      // 否则显式触发一次。
      if (customizeJob && customizeJob.status === 'waiting_for_samples') {
        toast.success(`${noiseCount} 份噪声样本已上传，迭代优化即将自动启动`)
        await useWorkspaceStore.getState().pollCustomizeJob()
      } else {
        await triggerOptimization(apiDefinitionId)
        toast.success(`${noiseCount} 份噪声样本已上传，3 轮迭代优化已启动`)
      }
      setFiles([])
      onClose()
    } catch {
      toast.error('上传或启动失败，请重试')
    } finally {
      setUploading(false)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={uploading ? undefined : onClose}>
      <div className="w-[560px] bg-[#1e1e24] border border-white/10 rounded-xl shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/10">
          <div className="text-white font-medium">上传多样化噪声样本</div>
          {!uploading && (
            <button onClick={onClose} className="text-gray-500 hover:text-gray-300"><X className="w-4 h-4" /></button>
          )}
        </div>

        <div className="px-5 py-4 space-y-3">
          <p className="text-xs text-amber-100/70 leading-relaxed">
            即将启动自动迭代优化。请<b>额外上传 {noiseCount} 份多样化「噪声」样本</b>
            （不同开票方 / 版式 / 税率 / 扫描质量，越随机越好）作为<b>留出验证集</b>，
            让优化结果更稳健、不过拟合你那 3 张。系统会自动识别并以当前结果为基线，无需逐张复核。
          </p>

          {/* File picker */}
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,image/*"
            className="hidden"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
          <button
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
            className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg border border-dashed border-white/15 bg-white/5 text-gray-300 text-sm hover:bg-white/10 disabled:opacity-40"
          >
            <FileText className="w-4 h-4" />
            {files.length === 0 ? `选择 ${noiseCount} 份样本（可多选）` : '重新选择'}
          </button>

          {/* Counter */}
          <div className="flex items-center justify-between text-xs">
            <span className={cn('font-medium', exactly ? 'text-emerald-400' : 'text-amber-400')}>
              已选 {files.length} / {noiseCount}
              {files.length > 0 && !exactly && (files.length < noiseCount ? `（还差 ${noiseCount - files.length} 份）` : `（多了 ${files.length - noiseCount} 份）`)}
            </span>
            {uploading && <span className="text-gray-400">上传中 {done}/{noiseCount}…</span>}
          </div>
          {/* Progress bar */}
          <div className="flex gap-1">
            {Array.from({ length: noiseCount }).map((_, i) => (
              <div key={i} className={cn('flex-1 h-1.5 rounded-full transition-colors', i < (uploading ? done : files.length) ? 'bg-emerald-500' : 'bg-white/10')} />
            ))}
          </div>
        </div>

        <div className="border-t border-white/10 px-5 py-3 flex justify-end gap-2">
          {!uploading && (
            <button onClick={onClose} className="px-3 py-1.5 text-sm rounded-md bg-white/5 text-gray-300 hover:bg-white/10">取消</button>
          )}
          <button
            onClick={handleUpload}
            disabled={!exactly || uploading}
            title={!exactly ? `请正好选择 ${noiseCount} 份样本` : undefined}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-md bg-emerald-600 hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed text-white font-medium transition-colors"
          >
            {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            {uploading ? '上传中…' : `上传 ${noiseCount} 份并启动迭代`}
          </button>
        </div>
      </div>
    </div>
  )
}
