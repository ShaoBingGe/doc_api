import { useState } from 'react'
import { Wand2, CheckCircle2 } from 'lucide-react'
import { useWorkspaceStore } from '../../stores/workspace-store'

export default function NlCorrectionBar() {
  const { correctionHistory, addCorrectionHistory } = useWorkspaceStore()
  const [input, setInput] = useState('')

  const handleCorrect = () => {
    const text = input.trim()
    if (!text) return
    // P4 will implement real NL correction; for now just record to history
    addCorrectionHistory(text)
    setInput('')
  }

  return (
    <div className="border-b border-gray-100 bg-gray-50/60">
      {/* Header */}
      <div className="flex items-center gap-1.5 px-3 pt-3 pb-2">
        <Wand2 className="w-3.5 h-3.5 text-indigo-500" />
        <span className="text-[11px] font-semibold text-gray-700 tracking-wide uppercase">
          自然语言矫正
        </span>
      </div>

      {/* Input row */}
      <div className="flex gap-1.5 px-3 pb-3">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleCorrect()}
          placeholder="描述需要矫正的内容…"
          className="flex-1 text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-400 placeholder:text-gray-300"
        />
        <button
          onClick={handleCorrect}
          className="px-2.5 py-1.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors whitespace-nowrap"
        >
          矫正
        </button>
      </div>

      {/* History */}
      {correctionHistory.length > 0 && (
        <div className="px-3 pb-3 space-y-1">
          {correctionHistory.map((item, i) => (
            <div key={i} className="flex items-start gap-1.5 text-[10px] text-gray-500">
              <CheckCircle2 className="w-3 h-3 text-emerald-500 mt-0.5 flex-shrink-0" />
              <span className="leading-relaxed">{item}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
