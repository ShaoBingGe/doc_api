import { useState } from 'react'
import {
  ArrowLeft,
  List,
  Save,
  User,
  Sparkles,
  Loader2,
  GitBranch,
  Library,
  BarChart3,
} from 'lucide-react'
import { cn } from '../../lib/utils'
import { useWorkspaceStore } from '../../stores/workspace-store'
import { useNavigate } from 'react-router-dom'
import { triggerOptimization } from '../../lib/api-client'
import { toast } from '../../lib/toast'
import SkillInsightsModal from './SkillInsightsModal'

export type HeaderTab = 'fields' | 'optimize' | 'skills'

interface WorkspaceHeaderProps {
  activeTab: HeaderTab
  onTabChange: (tab: HeaderTab) => void
  onOpenModal: () => void
  isNewMode: boolean
  onOptimizationTriggered?: () => void
}

const MIN_SAMPLES = 3

export default function WorkspaceHeader({
  activeTab,
  onTabChange,
  onOpenModal,
  isNewMode,
  onOptimizationTriggered,
}: WorkspaceHeaderProps) {
  const {
    documentInfo,
    annotations,
    apiDefinition,
    documents,
    apiDefinitionId,
    fieldEditDrafts,
    addFieldDrafts,
    editingFieldId,
    customizeJob,
    samplesReview,
  } = useWorkspaceStore()
  const navigate = useNavigate()
  const [optimizing, setOptimizing] = useState(false)
  const [insightsOpen, setInsightsOpen] = useState(false)

  // ── Sample-readiness gate (design v3) ────────────────────────────────────
  // Iteration requires N samples whose OCR result the customer marked as GT
  // ("已审视"). Raw upload count is irrelevant.
  const sampleCount = documents.length
  const confirmedCount = samplesReview?.confirmedCount ?? 0
  const requiredCount = samplesReview?.requiredCount ?? MIN_SAMPLES
  const hasEnoughSamples = confirmedCount >= requiredCount

  // ── Dirty-draft detection ────────────────────────────────────────────────
  // While the customer is editing fields, the legacy "save as-is" path is
  // hidden behind the customize flow. We disable "保存并生成 API" to steer
  // them toward "开始优化" (or the in-panel "保存并生成客户专属模板" CTA).
  const dirtyEditCount = Object.values(fieldEditDrafts).filter((d) => {
    const nameChanged = (d.originalName || '') !== d.correctedName
    const valueChanged = String(d.originalValue ?? '') !== d.correctedValue
    const fmtChanged = (d.originalFormat || '') !== d.correctedFormat
    return nameChanged || valueChanged || fmtChanged
  }).length
  const dirtyAddCount = addFieldDrafts.filter((d) => d.correctedName.trim().length > 0).length
  const inFlightJob =
    !!customizeJob &&
    customizeJob.status !== 'completed' &&
    customizeJob.status !== 'failed'
  const hasDirtyEdits = dirtyEditCount + dirtyAddCount > 0 || !!editingFieldId || inFlightJob

  const handleOptimize = async () => {
    if (!apiDefinitionId) {
      toast.info('请先保存 API 后再开始优化')
      return
    }
    // Gate 1: confirmed-sample count (not raw)
    if (!hasEnoughSamples) {
      toast.error(
        requiredCount > MIN_SAMPLES
          ? `样本不足（${confirmedCount} / ${requiredCount}）—— 迭代需 3 锚点 + ${requiredCount - MIN_SAMPLES} 个多样化噪声样本。`
            + `请再上传并审视 ${requiredCount - confirmedCount} 个多样化样本（留出验证用）。`
          : `已审视样本不足（${confirmedCount} / ${requiredCount}）—— `
            + `打开每个样本检查 OCR 结果，点工具栏"待审视"切到"已审视"`,
      )
      return
    }
    // Jump to optimization tab immediately so user sees loading skeleton there
    onTabChange('optimize')
    onOptimizationTriggered?.()

    setOptimizing(true)
    try {
      const res = await triggerOptimization(apiDefinitionId)
      const data = res.data
      if (data.status === 'completed') {
        const acc =
          typeof data.overall_accuracy === 'number'
            ? `${Math.round(data.overall_accuracy * 100)}%`
            : 'N/A'
        toast.success(
          `优化完成！准确率: ${acc}（共 ${data.rounds_completed} 轮）`,
        )
      } else if (data.status === 'failed') {
        toast.error(data.error_message || '优化失败')
      } else {
        toast.info(`优化状态: ${data.status}`)
      }
      onOptimizationTriggered?.()
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
    { key: 'optimize', label: '优化过程', icon: GitBranch },
    { key: 'skills', label: '新增技能', icon: Library },
  ]

  // Tooltip text for the optimize button depending on state
  const optimizeDisabledReason = isNewMode
    ? '请先保存 API'
    : !hasEnoughSamples
      ? `至少需要 ${requiredCount} 个已审视的样本（当前 ${confirmedCount}/${sampleCount}）`
      : null

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
        <span className="text-sm font-medium text-gray-200 max-w-[280px] truncate">
          {isNewMode
            ? '新建定制 API'
            : apiDefinition?.name ||
              documentInfo?.filename ||
              'Loading...'}
        </span>
        {!isNewMode && apiDefinitionId && (
          <span
            className={cn(
              'text-xs px-1.5 py-0.5 rounded font-mono',
              hasEnoughSamples
                ? 'bg-emerald-500/20 text-emerald-400'
                : 'bg-amber-500/20 text-amber-400',
            )}
            title={hasEnoughSamples ? '满足优化条件' : '样本不足'}
          >
            {sampleCount}/{MIN_SAMPLES} 样本
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
        {!isNewMode && activeTab === 'fields' && (
          <span className="text-xs text-gray-500 ml-2">
            {annotations.length} 字段
          </span>
        )}
      </div>

      {/* Right: 洞察 + 开始优化 + 保存 + avatar （技能库已改为「新增技能」tab） */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => setInsightsOpen(true)}
          disabled={isNewMode}
          title="技能洞察：每字段轨迹 + 守护状态 + 已挂技能（只读）"
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md transition-colors',
            isNewMode
              ? 'bg-white/5 text-gray-500 cursor-not-allowed'
              : 'bg-white/5 text-gray-300 hover:bg-white/10',
          )}
        >
          <BarChart3 className="w-4 h-4" />
          洞察
        </button>
        <button
          onClick={handleOptimize}
          disabled={optimizing || isNewMode || !hasEnoughSamples}
          title={optimizeDisabledReason ?? '触发完整优化 Run'}
          className={cn(
            'flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors font-medium',
            optimizing || isNewMode || !hasEnoughSamples
              ? 'bg-white/5 text-gray-500 cursor-not-allowed'
              : 'bg-amber-500/20 text-amber-400 hover:bg-amber-500/30',
          )}
        >
          {optimizing ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Sparkles className="w-4 h-4" />
          )}
          {optimizing ? '优化中…' : '开始优化'}
        </button>
        <button
          onClick={onOpenModal}
          disabled={isNewMode || hasDirtyEdits}
          title={
            hasDirtyEdits
              ? '有待暂存的字段修改 — 请点"开始优化"走客户定制流程'
              : undefined
          }
          className={cn(
            'flex items-center gap-2 px-4 py-1.5 text-sm rounded-md transition-colors font-medium',
            isNewMode || hasDirtyEdits
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

      <SkillInsightsModal
        apiDefinitionId={apiDefinitionId}
        open={insightsOpen}
        onClose={() => setInsightsOpen(false)}
      />
    </header>
  )
}
