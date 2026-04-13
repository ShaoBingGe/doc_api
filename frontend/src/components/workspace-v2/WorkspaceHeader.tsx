import { useState } from 'react'
import { ArrowLeft, List, ShieldCheck, BarChart2, Save, User, Sparkles, Loader2 } from 'lucide-react'
import { cn } from '../../lib/utils'
import { useWorkspaceStore } from '../../stores/workspace-store'
import { useNavigate } from 'react-router-dom'
import { triggerOptimization } from '../../lib/api-client'
import { toast } from '../../lib/toast'

type HeaderTab = 'fields' | 'rules' | 'stats'

interface WorkspaceHeaderProps {
  activeTab: HeaderTab
  onTabChange: (tab: HeaderTab) => void
  onOpenModal: () => void
  isNewMode: boolean
}

export default function WorkspaceHeader({ activeTab, onTabChange, onOpenModal, isNewMode }: WorkspaceHeaderProps) {
  const { documentInfo, annotations, processingResults, apiDefinition } = useWorkspaceStore()
  const navigate = useNavigate()
  const [optimizing, setOptimizing] = useState(false)

  // Show optimize button when there are confirmed/edited fields
  const hasCorrections = processingResults.some((r) => r.confidence >= 100)

  const handleOptimize = async () => {
    if (!apiDefinition?.id) {
      toast.info('请先保存 API 后再进行 Prompt 优化')
      return
    }
    setOptimizing(true)
    try {
      const res = await triggerOptimization(apiDefinition.id)
      const data = res.data
      if (data.status === 'completed') {
        toast.success(`Prompt 优化完成！准确率: ${Math.round(data.accuracy_score * 100)}%（版本 v${data.version}）`)
      } else {
        toast.info(data.message || '优化未产生改进')
      }
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail
      toast.error(typeof msg === 'string' ? msg : '优化失败，请重试')
    } finally {
      setOptimizing(false)
    }
  }

  const tabs: { key: HeaderTab; label: string; icon: React.ElementType }[] = [
    { key: 'fields', label: '字段视图', icon: List },
    { key: 'rules', label: '校验规则', icon: ShieldCheck },
    { key: 'stats', label: '统计分析', icon: BarChart2 },
  ]

  return (
    <header className="flex items-center justify-between px-4 py-2.5 bg-[#1e1e24] border-b border-white/10 text-white">
      {/* Left: back button + title */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate('/')}
          className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-white transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          返回
        </button>
        <div className="h-4 w-px bg-white/10" />
        <span className="text-sm font-medium text-gray-200 max-w-[220px] truncate">
          {isNewMode
            ? '新建定制 API'
            : documentInfo?.filename ?? 'Loading...'}
        </span>
        {!isNewMode && documentInfo?.status === 'completed' && (
          <span className="text-xs bg-emerald-500/20 text-emerald-400 px-1.5 py-0.5 rounded">
            已完成
          </span>
        )}
      </div>

      {/* Center: tabs */}
      <div className="flex items-center gap-1">
        {tabs.map((tab) => {
          const Icon = tab.icon
          return (
            <button
              key={tab.key}
              onClick={() => onTabChange(tab.key)}
              disabled={isNewMode}
              className={cn(
                'flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors',
                isNewMode
                  ? 'text-gray-600 cursor-not-allowed'
                  : activeTab === tab.key
                    ? 'bg-purple-600/20 text-purple-400 ring-1 ring-purple-500/30'
                    : 'text-gray-400 hover:bg-white/5',
              )}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          )
        })}
        {!isNewMode && (
          <span className="text-xs text-gray-500 ml-2">{annotations.length} 字段</span>
        )}
      </div>

      {/* Right: optimize + save + avatar */}
      <div className="flex items-center gap-2">
        {hasCorrections && !isNewMode && (
          <button
            onClick={handleOptimize}
            disabled={optimizing}
            className="flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors font-medium bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 disabled:opacity-50"
          >
            {optimizing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
            {optimizing ? '优化中...' : '优化 Prompt'}
          </button>
        )}
        <button
          onClick={onOpenModal}
          disabled={isNewMode}
          className={cn(
            'flex items-center gap-2 px-4 py-1.5 text-sm rounded-md transition-colors font-medium',
            isNewMode
              ? 'bg-purple-600/50 text-white/50 cursor-not-allowed'
              : 'bg-purple-600 hover:bg-purple-700 text-white',
          )}
        >
          <Save className="w-4 h-4" />
          保存并生成 API
        </button>
        <div className="w-8 h-8 rounded-full bg-purple-500 flex items-center justify-center ml-2 cursor-pointer">
          <User className="w-4 h-4" />
        </div>
      </div>
    </header>
  )
}
