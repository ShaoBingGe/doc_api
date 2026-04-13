import { Sparkles, ChevronDown, ChevronUp, Send } from 'lucide-react'
import { useState } from 'react'
import { cn } from '../../lib/utils'

export default function AiChat() {
  const [isOpen, setIsOpen] = useState(false)
  const [input, setInput] = useState('')

  const suggestions = [
    '修正发票号码为...',
    '添加新字段...',
    '调整API返回格式...',
    '把日期格式改为 YYYY/MM/DD',
  ]

  return (
    <div
      className={cn(
        'fixed bottom-0 left-0 right-0 bg-[#1e1e24] border-t border-white/10 shadow-2xl transition-all duration-300 ease-in-out z-50',
        isOpen ? 'h-[300px]' : 'h-12',
      )}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-6 py-3 cursor-pointer hover:bg-white/5 transition-colors"
        onClick={() => setIsOpen(!isOpen)}
      >
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-full bg-purple-600 flex items-center justify-center">
            <Sparkles className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="font-medium text-white text-sm">AI 矫正对话</span>
          <span className="text-xs bg-white/10 text-gray-400 px-2 py-0.5 rounded-full ml-2">
            0 条消息
          </span>
        </div>
        {isOpen ? (
          <ChevronDown className="w-4 h-4 text-gray-400" />
        ) : (
          <ChevronUp className="w-4 h-4 text-gray-400" />
        )}
      </div>

      {/* Chat Content */}
      {isOpen && (
        <div className="flex flex-col h-[calc(100%-48px)]">
          {/* Messages Area */}
          <div className="flex-1 overflow-auto p-6 space-y-6">
            {/* AI Welcome Message */}
            <div className="flex gap-4">
              <div className="w-8 h-8 rounded-full bg-purple-600 flex-shrink-0 flex items-center justify-center mt-1">
                <Sparkles className="w-4 h-4 text-white" />
              </div>
              <div className="space-y-2">
                <div className="text-gray-200 text-sm">
                  文档数据已提取完成。如果有任何提取错误或格式需要调整，请告诉我。
                </div>
                <div className="text-xs text-gray-500">刚刚</div>
              </div>
            </div>
          </div>

          {/* Input Area */}
          <div className="p-4 border-t border-white/10 bg-[#18181c]">
            {/* Suggestions */}
            <div className="flex gap-2 mb-3 overflow-x-auto pb-1 scrollbar-hide">
              {suggestions.map((text, i) => (
                <button
                  key={i}
                  onClick={() => setInput(text)}
                  className="whitespace-nowrap px-3 py-1.5 rounded-full border border-white/10 text-xs text-gray-400 hover:text-gray-200 hover:bg-white/5 transition-colors"
                >
                  {text}
                </button>
              ))}
            </div>

            {/* Input Box */}
            <div className="relative flex items-center">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="输入指令，例如：把日期格式改为 YYYY/MM/DD..."
                className="w-full bg-[#2a2a32] border border-white/10 rounded-lg pl-4 pr-12 py-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 focus:ring-1 focus:ring-purple-500 transition-all"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && input.trim()) {
                    setInput('')
                  }
                }}
              />
              <button
                className={cn(
                  'absolute right-2 p-2 rounded-md transition-colors',
                  input.trim()
                    ? 'bg-purple-600 text-white hover:bg-purple-700'
                    : 'text-gray-500 hover:bg-white/5',
                )}
                onClick={() => {
                  if (input.trim()) setInput('')
                }}
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
