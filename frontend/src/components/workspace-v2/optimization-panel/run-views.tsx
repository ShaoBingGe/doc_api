// L2.1：OptimizationProcessPanel 拆分——Run 状态视图（版本 chips / Run
// 状态栏 / 模块列表 / 模块五阶段详情）。
import { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle, CheckCircle2, Loader2, Pencil, PlayCircle, Plus, StopCircle, X,
} from 'lucide-react'
import { cn } from '../../../lib/utils'
import { toast } from '../../../lib/toast'
import { Collapsible, EmptyPhase } from './shared'
import {
  accPct, originLabel,
  type VersionSummary, type VersionDetail, type OcrModuleResponse,
  type IterationResponse, type RunSummary,
} from './helpers'

export function VersionChips({
  versions,
  selectedId,
  onSelect,
}: {
  versions: VersionSummary[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  return (
    <div className="flex items-center gap-1.5 px-3 py-2 overflow-x-auto">
      <span className="text-[10px] uppercase text-gray-500 tracking-wider mr-1">
        Versions:
      </span>
      {versions.map((v) => {
        const oi = originLabel(v.origin)
        const isActive = v.status === 'active'
        const isSelected = v.id === selectedId
        return (
          <button
            key={v.id}
            onClick={() => onSelect(v.id)}
            className={cn(
              'flex items-center gap-1 px-2 py-0.5 text-xs rounded-md border transition-colors whitespace-nowrap',
              isSelected
                ? 'ring-2 ring-purple-400 ring-offset-2 ring-offset-[#18181c]'
                : 'hover:bg-white/5',
              oi.color,
            )}
            title={`origin: ${v.origin} · accuracy: ${accPct(v.overall_accuracy)} · created: ${new Date(v.created_at).toLocaleString()}`}
          >
            <span className="font-mono">v{v.version}</span>
            <span className="text-[9px] opacity-70">{oi.label}</span>
            {isActive && <CheckCircle2 className="w-3 h-3 text-emerald-400" />}
            {v.overall_accuracy != null && (
              <span className="text-[9px] opacity-80">{accPct(v.overall_accuracy)}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Run status bar
// ──────────────────────────────────────────────────────────────────────────────

export function RunStatusBar({
  run,
  advancing,
  finalizing,
  hasPendingEdits,
  onAdvance,
  onFinalize,
  onAbort,
}: {
  run: RunSummary
  advancing: boolean
  finalizing: boolean
  hasPendingEdits: boolean
  onAdvance: () => void
  onFinalize: () => void
  onAbort: () => void
}) {
  const isPaused = run.status === 'paused_for_review'
  const isRunning = run.status === 'running'
  const isFailed = run.status === 'failed'
  const atMaxRounds = run.current_round_num >= run.max_rounds

  return (
    <div
      className={cn(
        'flex items-center justify-between px-3 py-1.5 text-xs',
        isFailed ? 'bg-red-500/10' : 'bg-white/5',
      )}
    >
      <div className="flex items-center gap-2">
        {isRunning && <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-400" />}
        {isPaused && <AlertCircle className="w-3.5 h-3.5 text-amber-400" />}
        {isFailed && <AlertCircle className="w-3.5 h-3.5 text-red-400" />}
        <span className="font-mono text-gray-400">Run #{run.id.slice(0, 8)}</span>
        <span
          className={cn(
            'px-1.5 py-0.5 rounded text-[10px]',
            isPaused && 'bg-amber-500/20 text-amber-300',
            isRunning && 'bg-blue-500/20 text-blue-300',
            isFailed && 'bg-red-500/20 text-red-300',
          )}
        >
          {run.status}
        </span>
        <span className="text-gray-500">
          round {run.current_round_num}/{run.max_rounds}
        </span>
        {isFailed && run.error_message && (
          <span className="text-red-300 truncate max-w-[400px]">{run.error_message}</span>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <button
          onClick={onAdvance}
          disabled={!isPaused || advancing || finalizing || hasPendingEdits || atMaxRounds}
          title={
            atMaxRounds
              ? '已达最大轮数，请 finalize'
              : hasPendingEdits
              ? '请先保存或舍弃未保存编辑'
              : !isPaused
              ? '仅在 paused_for_review 状态可推进'
              : ''
          }
          className={cn(
            'flex items-center gap-1 px-2 py-1 rounded-md font-medium',
            isPaused && !advancing && !finalizing && !hasPendingEdits && !atMaxRounds
              ? 'bg-blue-600/20 text-blue-300 hover:bg-blue-600/30'
              : 'bg-white/5 text-gray-600 cursor-not-allowed',
          )}
        >
          {advancing ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <PlayCircle className="w-3 h-3" />
          )}
          下一轮
        </button>
        <button
          onClick={onFinalize}
          disabled={(!isPaused && !isFailed) || finalizing}
          className={cn(
            'flex items-center gap-1 px-2 py-1 rounded-md font-medium',
            (isPaused || isFailed) && !finalizing
              ? 'bg-emerald-600/20 text-emerald-300 hover:bg-emerald-600/30'
              : 'bg-white/5 text-gray-600 cursor-not-allowed',
          )}
        >
          {finalizing ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <CheckCircle2 className="w-3 h-3" />
          )}
          完成此次 Run
        </button>
        <button
          onClick={onAbort}
          disabled={isRunning}
          className="flex items-center gap-1 px-2 py-1 rounded-md text-gray-400 hover:bg-white/5 hover:text-red-400"
        >
          <StopCircle className="w-3 h-3" />
          放弃
        </button>
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Module list
// ──────────────────────────────────────────────────────────────────────────────

export function ModuleList({
  modules,
  selectedKey,
  onSelect,
  iterations,
}: {
  modules: OcrModuleResponse[]
  selectedKey: string | null
  onSelect: (key: string) => void
  iterations: IterationResponse[]
}) {
  const iterByKey = useMemo(() => {
    const m = new Map<string, IterationResponse>()
    iterations.forEach((it) => m.set(it.module_key, it))
    return m
  }, [iterations])

  return (
    <div className="w-[210px] flex-shrink-0 border-r border-white/5 overflow-y-auto bg-[#16161a]">
      <div className="px-2 py-2 sticky top-0 bg-[#16161a] border-b border-white/5">
        <div className="text-[10px] uppercase tracking-wider text-gray-500">
          模块 ({modules.length})
        </div>
      </div>
      <div className="p-1.5 space-y-1">
        {modules.map((m) => {
          const it = iterByKey.get(m.module_key)
          const acc = it?.aggregate_accuracy ?? m.module_accuracy ?? null
          const ok = acc != null && acc >= 1.0
          const isSelected = m.module_key === selectedKey
          return (
            <button
              key={m.module_key}
              onClick={() => onSelect(m.module_key)}
              className={cn(
                'w-full text-left px-2 py-1.5 rounded-md border transition-colors',
                isSelected
                  ? 'border-purple-500/60 bg-purple-500/10'
                  : 'border-transparent hover:bg-white/5',
              )}
            >
              <div className="flex items-center justify-between gap-1">
                <span
                  className={cn(
                    'text-xs font-mono truncate',
                    isSelected ? 'text-white' : 'text-gray-300',
                  )}
                >
                  {m.module_key}
                </span>
                <span
                  className={cn(
                    'text-[10px]',
                    ok ? 'text-emerald-400' : 'text-amber-400',
                  )}
                >
                  {accPct(acc)}
                </span>
              </div>
              <div className="text-[10px] text-gray-500 truncate">{m.display_name}</div>
            </button>
          )
        })}
      </div>
      <div className="p-2 border-t border-white/5 mt-2">
        <button
          onClick={() => toast.info('Skill 功能即将上线 (Coming Soon)')}
          className="w-full text-[10px] text-gray-500 hover:text-purple-300 flex items-center justify-center gap-1 py-1.5 rounded-md border border-dashed border-white/10 hover:border-purple-500/40"
        >
          <Plus className="w-3 h-3" />
          Skill 模块
        </button>
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Module detail (5 phases)
// ──────────────────────────────────────────────────────────────────────────────

export function ModuleDetail({
  module,
  iteration,
  version,
  pendingEdit,
  onEditChange,
  onDiscardEdit,
}: {
  module: OcrModuleResponse
  iteration: IterationResponse | null
  version: VersionDetail | null
  pendingEdit: { description?: string; ocr_suggestions?: string } | undefined
  onEditChange: (patch: { description?: string; ocr_suggestions?: string }) => void
  onDiscardEdit: () => void
}) {
  const [editingDesc, setEditingDesc] = useState(false)
  const [editingSugg, setEditingSugg] = useState(false)

  useEffect(() => {
    setEditingDesc(false)
    setEditingSugg(false)
  }, [module.module_key])

  const description = pendingEdit?.description ?? module.description
  const suggestionsStr =
    pendingEdit?.ocr_suggestions ?? JSON.stringify(module.ocr_suggestions, null, 2)

  return (
    <div className="p-4 space-y-4 text-sm">
      <div className="flex items-baseline justify-between border-b border-white/10 pb-2">
        <div>
          <span className="text-base font-medium text-white">{module.display_name}</span>
          <span className="ml-2 text-xs font-mono text-gray-500">{module.module_key}</span>
        </div>
        <div className="text-xs text-gray-400">
          path: <span className="font-mono">{module.json_path}</span>
          {iteration && (
            <span className="ml-3">
              accuracy:{' '}
              <span
                className={cn(
                  iteration.aggregate_accuracy >= 1
                    ? 'text-emerald-400'
                    : 'text-amber-400',
                )}
              >
                {accPct(iteration.aggregate_accuracy)}
              </span>
            </span>
          )}
        </div>
      </div>

      {/* Phase 1 */}
      <Collapsible title="Phase 1 — OCR 输出">
        {iteration ? (
          <div className="space-y-2">
            {iteration.per_sample_results.map((s) => (
              <details key={s.sample_doc_id} className="rounded-md bg-black/30 px-2 py-1.5">
                <summary className="cursor-pointer text-xs text-gray-300">
                  <span className="font-mono">{s.sample_doc_id.slice(0, 8)}</span>
                  <span
                    className={cn(
                      'ml-2 text-[10px] px-1.5 py-0.5 rounded',
                      s.matched
                        ? 'bg-emerald-500/10 text-emerald-400'
                        : 'bg-amber-500/10 text-amber-400',
                    )}
                  >
                    {accPct(s.field_accuracy)}
                  </span>
                </summary>
                <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
                  <div>
                    <div className="text-gray-500 mb-1">OCR 切片</div>
                    <pre className="bg-black/40 p-2 rounded text-gray-300 overflow-x-auto">
                      {JSON.stringify(s.ocr_sliced, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <div className="text-gray-500 mb-1">Ground Truth</div>
                    <pre className="bg-black/40 p-2 rounded text-gray-300 overflow-x-auto">
                      {JSON.stringify(s.ground_truth, null, 2)}
                    </pre>
                  </div>
                </div>
                {s.diff_detail && (
                  <div className="mt-1.5 text-[11px] text-amber-300/80">
                    diff: {s.diff_detail}
                  </div>
                )}
              </details>
            ))}
          </div>
        ) : (
          <EmptyPhase label="该版本不是 round 产物，无 OCR 数据" />
        )}
      </Collapsible>

      {/* Phase 2 */}
      <Collapsible title="Phase 2 — 模块差异分析">
        {iteration?.aggregate_diff ? (
          <div className="space-y-2 text-[12px]">
            {iteration.aggregate_diff.differences_description && (
              <div>
                <div className="text-gray-500 text-[10px] mb-0.5">
                  differences_description
                </div>
                <div className="text-gray-300 leading-relaxed">
                  {iteration.aggregate_diff.differences_description}
                </div>
              </div>
            )}
            {iteration.aggregate_diff.differences_reason_analysis && (
              <div>
                <div className="text-gray-500 text-[10px] mb-0.5">reason_analysis</div>
                <div className="text-gray-300 leading-relaxed">
                  {iteration.aggregate_diff.differences_reason_analysis}
                </div>
              </div>
            )}
          </div>
        ) : (
          <EmptyPhase label="无 diff 数据" />
        )}
      </Collapsible>

      {/* Phase 3 */}
      <Collapsible title="Phase 3 — 分析原因 (LLM Reasoning)">
        {iteration?.optimization_suggestion ? (
          <div className="text-[12px] text-gray-300 leading-relaxed bg-black/30 rounded-md p-2.5">
            {iteration.optimization_suggestion}
          </div>
        ) : (
          <EmptyPhase label="无 reasoning 数据" />
        )}
      </Collapsible>

      {/* Phase 4 (editable) */}
      <Collapsible title="Phase 4 — 生成优化建议" defaultOpen>
        <div className="space-y-3">
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] uppercase text-gray-500">description</span>
              {!editingDesc ? (
                <button
                  onClick={() => setEditingDesc(true)}
                  className="text-[10px] text-gray-400 hover:text-purple-300 flex items-center gap-0.5"
                >
                  <Pencil className="w-3 h-3" /> 编辑
                </button>
              ) : (
                <button
                  onClick={() => setEditingDesc(false)}
                  className="text-[10px] text-gray-400 hover:text-white flex items-center gap-0.5"
                >
                  <X className="w-3 h-3" /> 收起
                </button>
              )}
            </div>
            {editingDesc ? (
              <textarea
                value={description}
                onChange={(e) => onEditChange({ description: e.target.value })}
                rows={3}
                className="w-full text-[12px] text-gray-200 bg-black/40 border border-purple-500/30 focus:border-purple-500/60 rounded-md p-2 outline-none"
              />
            ) : (
              <div className="text-[12px] text-gray-300 leading-relaxed bg-black/30 rounded-md p-2.5">
                {description}
              </div>
            )}
            {pendingEdit?.description !== undefined && (
              <div className="text-[10px] text-purple-300 mt-1">
                ✏ 已修改（点击底部「保存 patch」生成新版本）
              </div>
            )}
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] uppercase text-gray-500">ocr_suggestions</span>
              {!editingSugg ? (
                <button
                  onClick={() => setEditingSugg(true)}
                  className="text-[10px] text-gray-400 hover:text-purple-300 flex items-center gap-0.5"
                >
                  <Pencil className="w-3 h-3" /> 编辑
                </button>
              ) : (
                <button
                  onClick={() => setEditingSugg(false)}
                  className="text-[10px] text-gray-400 hover:text-white flex items-center gap-0.5"
                >
                  <X className="w-3 h-3" /> 收起
                </button>
              )}
            </div>
            {editingSugg ? (
              <textarea
                value={suggestionsStr}
                onChange={(e) => onEditChange({ ocr_suggestions: e.target.value })}
                rows={8}
                className="w-full text-[11px] font-mono text-gray-200 bg-black/40 border border-purple-500/30 focus:border-purple-500/60 rounded-md p-2 outline-none"
              />
            ) : (
              <pre className="text-[11px] font-mono text-gray-300 bg-black/30 rounded-md p-2.5 overflow-x-auto">
                {suggestionsStr}
              </pre>
            )}
            {pendingEdit?.ocr_suggestions !== undefined && (
              <div className="text-[10px] text-purple-300 mt-1">
                ✏ 已修改（点击底部「保存 patch」生成新版本）
              </div>
            )}
          </div>

          {pendingEdit && (
            <button
              onClick={onDiscardEdit}
              className="text-[10px] text-gray-500 hover:text-red-400"
            >
              舍弃本模块的编辑
            </button>
          )}
        </div>
      </Collapsible>

      {/* Phase 5 */}
      <Collapsible title="Phase 5 — 生成新 OCR Prompt">
        <pre className="text-[11px] font-mono text-gray-300 bg-black/30 rounded-md p-2.5 overflow-x-auto whitespace-pre-wrap">
          {module.ocr_prompt}
        </pre>
        {iteration?.new_ocr_prompt && iteration.new_ocr_prompt !== module.ocr_prompt && (
          <div className="mt-2">
            <div className="text-[10px] text-gray-500 mb-1">
              本轮 LLM 生成的新 prompt（已落入下一版本）
            </div>
            <pre className="text-[11px] font-mono text-blue-200 bg-blue-500/5 rounded-md p-2.5 overflow-x-auto whitespace-pre-wrap">
              {iteration.new_ocr_prompt}
            </pre>
          </div>
        )}
        {version?.composed_prompt && (
          <details className="mt-3 text-[11px]">
            <summary className="cursor-pointer text-gray-500 hover:text-gray-300">
              composed_prompt 完整预览
            </summary>
            <pre className="mt-2 text-[11px] font-mono text-gray-400 bg-black/40 rounded-md p-2.5 overflow-x-auto whitespace-pre-wrap">
              {version.composed_prompt}
            </pre>
          </details>
        )}
      </Collapsible>

      {/* Skills (TODO placeholder) */}
      <div className="border-t border-white/10 pt-3 mt-3">
        <div className="text-[10px] uppercase text-gray-500 mb-2">Skills</div>
        {(!module.skill_ids || module.skill_ids.length === 0) ? (
          <div className="text-[12px] text-gray-500">暂未挂载 skills</div>
        ) : (
          <div className="text-[12px] text-gray-300">
            {module.skill_ids.length} skill(s) attached
          </div>
        )}
        {iteration?.skill_feedback && (
          <div className="mt-2 text-[11px] text-gray-400 italic">
            <span className="text-gray-500 not-italic">Optimizer 反馈: </span>
            {iteration.skill_feedback}
          </div>
        )}
        <button
          onClick={() => toast.info('Skill 功能即将上线 (Coming Soon)')}
          className="mt-2 text-[10px] text-gray-400 hover:text-purple-300 flex items-center gap-1"
        >
          <Plus className="w-3 h-3" />
          添加 Skill
        </button>
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Small bits
// ──────────────────────────────────────────────────────────────────────────────

