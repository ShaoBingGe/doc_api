import { useEffect, useState } from 'react'
import { CheckCircle, XCircle, Info, AlertTriangle, X } from 'lucide-react'
import { cn } from '../lib/utils'
import { type Toast, onToast, onDismiss } from '../lib/toast'

const icons = {
  success: CheckCircle,
  error: XCircle,
  info: Info,
  warning: AlertTriangle,
}

const styles = {
  success: 'bg-emerald-900/90 border-emerald-500/40 text-emerald-100',
  error:   'bg-red-900/90 border-red-500/40 text-red-100',
  info:    'bg-[#2a2a32] border-white/10 text-gray-100',
  warning: 'bg-amber-900/90 border-amber-500/40 text-amber-100',
}

function ToastItem({ toast, onRemove }: { toast: Toast; onRemove: (id: string) => void }) {
  const Icon = icons[toast.type]

  useEffect(() => {
    const timer = setTimeout(() => onRemove(toast.id), toast.duration ?? 3500)
    return () => clearTimeout(timer)
  }, [toast.id, toast.duration, onRemove])

  return (
    <div
      className={cn(
        'flex items-start gap-3 px-4 py-3 rounded-lg border shadow-xl text-sm max-w-sm w-full',
        'animate-in slide-in-from-bottom-2 fade-in duration-200',
        styles[toast.type],
      )}
    >
      <Icon className="w-4 h-4 flex-shrink-0 mt-0.5" />
      <span className="flex-1 leading-snug">{toast.message}</span>
      <button
        onClick={() => onRemove(toast.id)}
        className="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}

export default function ToastContainer() {
  const [toasts, setToasts] = useState<Toast[]>([])

  useEffect(() => {
    const unsub1 = onToast((t) => setToasts((prev) => [...prev, t]))
    const unsub2 = onDismiss((id) => setToasts((prev) => prev.filter((t) => t.id !== id)))
    return () => { unsub1(); unsub2() }
  }, [])

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-16 right-4 z-[200] flex flex-col gap-2 items-end">
      {toasts.map((t) => (
        <ToastItem
          key={t.id}
          toast={t}
          onRemove={(id) => setToasts((prev) => prev.filter((x) => x.id !== id))}
        />
      ))}
    </div>
  )
}
