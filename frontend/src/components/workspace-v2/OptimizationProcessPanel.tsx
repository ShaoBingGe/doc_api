/**
 * OptimizationProcessPanel v2 — module-sliced layout with Run state machine.
 *
 * Layout (see docs/UI_DESIGN.md §10):
 *   ┌───────── Version chips + Run status bar (top) ─────────┐
 *   │  module list (left, 200px)  │  selected module 5-phase │
 *   └─────────────────────────────┴───────────────────────────┘
 *
 * Run lifecycle: backend pauses after every round (paused_for_review).
 * User picks: advance next round (with optional manual_edit version),
 * or finalize (activate a chosen version).
 *
 * L2.1：1574 行单文件已按分组拆到 ./optimization-panel/（shared 类型+基础 UI
 * / charts 图表 / run-views Run 视图）——本文件只保留主编排组件。
 * import 方（Workspace 等）default import 路径不变。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, FileEdit, Loader2, Sparkles } from 'lucide-react'
import { toast } from '../../lib/toast'
import CountryReflectionAgents from './CountryReflectionAgents'
import {
  fetchOcrVersions,
  fetchOcrVersion,
  fetchOcrRuns,
  fetchOcrRound,
  advanceRound,
  finalizeRun,
  abortRun,
  manualPatchVersion,
  type ModuleEditPayload,
} from '../../lib/api-client'
import { FinalizeModal } from './optimization-panel/shared'
import {
  type OptimizationProcessPanelProps, type VersionSummary, type VersionDetail,
  type RoundDetail, type RunSummary,
} from './optimization-panel/helpers'
import { FieldAccuracyHeatmap, FieldDiffComparison } from './optimization-panel/charts'
import { VersionChips, RunStatusBar, ModuleList, ModuleDetail } from './optimization-panel/run-views'

export default function OptimizationProcessPanel({
  apiDefinitionId,
  reloadKey,
  optimizing,
}: OptimizationProcessPanelProps) {
  const [versions, setVersions] = useState<VersionSummary[]>([])
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null)
  const [versionDetail, setVersionDetail] = useState<VersionDetail | null>(null)
  const [round, setRound] = useState<RoundDetail | null>(null)
  // The round AFTER the producing round — its per-sample results are the
  // "优化后" (this version's) recognition, vs `round`'s "优化前" (prior version's).
  const [nextRound, setNextRound] = useState<RoundDetail | null>(null)
  const [activeRun, setActiveRun] = useState<RunSummary | null>(null)
  const [selectedModuleKey, setSelectedModuleKey] = useState<string | null>(null)
  const [loadingList, setLoadingList] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)

  const [pendingEdits, setPendingEdits] = useState<Record<string, {
    description?: string
    ocr_suggestions?: string
  }>>({})

  const [advancing, setAdvancing] = useState(false)
  const [finalizing, setFinalizing] = useState(false)
  const [savingPatch, setSavingPatch] = useState(false)
  const [showFinalizeModal, setShowFinalizeModal] = useState(false)

  // ── Initial / reload ─────────────────────────────────────────────────────
  const reload = useCallback(async () => {
    setLoadingList(true)
    try {
      const [vRes, rRes] = await Promise.all([
        fetchOcrVersions(apiDefinitionId),
        fetchOcrRuns(apiDefinitionId),
      ])
      const vList: VersionSummary[] = (vRes.data || []) as VersionSummary[]
      const sorted = [...vList].sort((a, b) =>
        a.created_at.localeCompare(b.created_at),
      )
      setVersions(sorted)

      const initialSelect = sorted[sorted.length - 1]?.id ?? null
      setSelectedVersionId((prev) => prev ?? initialSelect)

      const runs: RunSummary[] = (rRes.data || []) as RunSummary[]
      const open = runs.find((r) =>
        ['paused_for_review', 'running', 'failed'].includes(r.status),
      )
      setActiveRun(open ?? null)
    } catch (err) {
      console.error('OptimizationPanel reload failed', err)
    } finally {
      setLoadingList(false)
    }
  }, [apiDefinitionId])

  useEffect(() => {
    reload()
  }, [reload, reloadKey])

  // ── Load detail when selectedVersionId changes ───────────────────────────
  useEffect(() => {
    if (!selectedVersionId) {
      setVersionDetail(null)
      setRound(null)
      return
    }
    let alive = true
    setLoadingDetail(true)
    setPendingEdits({})
    ;(async () => {
      try {
        const vd = await fetchOcrVersion(apiDefinitionId, selectedVersionId)
        if (!alive) return
        const detail = vd.data as VersionDetail
        setVersionDetail(detail)
        const firstModule = [...(detail.modules || [])].sort(
          (a, b) => (a.module_accuracy ?? 1) - (b.module_accuracy ?? 1),
        )[0]
        setSelectedModuleKey((prev) =>
          prev && detail.modules.some((m) => m.module_key === prev)
            ? prev
            : firstModule?.module_key ?? null,
        )
        if (detail.produced_by_run_id && detail.produced_in_round) {
          try {
            const rd = await fetchOcrRound(
              apiDefinitionId,
              detail.produced_by_run_id,
              detail.produced_in_round,
            )
            if (alive) setRound(rd.data as RoundDetail)
          } catch (e) {
            console.warn('round detail failed', e)
            if (alive) setRound(null)
          }
          // "优化后" = the next round's eval of THIS version (may not exist for
          // the last round). Best-effort; absence just hides the after column.
          try {
            const nrd = await fetchOcrRound(
              apiDefinitionId,
              detail.produced_by_run_id,
              detail.produced_in_round + 1,
            )
            if (alive) setNextRound(nrd.data as RoundDetail)
          } catch {
            if (alive) setNextRound(null)
          }
        } else {
          setRound(null)
          setNextRound(null)
        }
      } catch (e) {
        console.error('version detail failed', e)
      } finally {
        if (alive) setLoadingDetail(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [apiDefinitionId, selectedVersionId])

  const selectedModule = useMemo(
    () =>
      versionDetail?.modules.find((m) => m.module_key === selectedModuleKey) ??
      null,
    [versionDetail, selectedModuleKey],
  )
  const selectedIteration = useMemo(
    () => round?.iterations.find((it) => it.module_key === selectedModuleKey) ?? null,
    [round, selectedModuleKey],
  )

  const hasPendingEdits = Object.keys(pendingEdits).length > 0

  // ── Actions ──────────────────────────────────────────────────────────────
  const handleSavePatch = useCallback(async () => {
    if (!selectedVersionId || !hasPendingEdits) return
    setSavingPatch(true)
    try {
      const edits: ModuleEditPayload[] = Object.entries(pendingEdits).map(
        ([module_key, p]) => {
          const out: ModuleEditPayload = { module_key }
          if (p.description !== undefined) out.description = p.description
          if (p.ocr_suggestions !== undefined) {
            try {
              out.ocr_suggestions = JSON.parse(p.ocr_suggestions || '{}')
            } catch {
              throw new Error(`模块 ${module_key} 的 suggestions JSON 格式错误`)
            }
          }
          return out
        },
      )
      const res = await manualPatchVersion(apiDefinitionId, selectedVersionId, edits)
      const newVersion = res.data as VersionDetail
      toast.success(`已保存 patch → v${newVersion.version}`)
      await reload()
      setSelectedVersionId(newVersion.id)
      setPendingEdits({})
    } catch (e) {
      const msg = e instanceof Error
        ? e.message
        : (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof msg === 'string' ? msg : '保存失败')
    } finally {
      setSavingPatch(false)
    }
  }, [apiDefinitionId, selectedVersionId, pendingEdits, hasPendingEdits, reload])

  const handleAdvance = useCallback(async () => {
    if (!activeRun) return
    if (hasPendingEdits) {
      toast.error('有未保存的编辑，请先点「保存 patch」或舍弃后再继续')
      return
    }
    if (activeRun.current_round_num >= activeRun.max_rounds) {
      toast.error('已达最大轮数，请点「完成此次 Run」')
      return
    }
    setAdvancing(true)
    try {
      await advanceRound(apiDefinitionId, activeRun.id, {
        use_version_id: selectedVersionId,
      })
      toast.success(`第 ${activeRun.current_round_num + 1} 轮已完成`)
      await reload()
    } catch (e) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof msg === 'string' ? msg : '推进下一轮失败')
    } finally {
      setAdvancing(false)
    }
  }, [activeRun, hasPendingEdits, apiDefinitionId, selectedVersionId, reload])

  const handleFinalize = useCallback(
    async (versionId: string) => {
      if (!activeRun) return
      setFinalizing(true)
      try {
        await finalizeRun(apiDefinitionId, activeRun.id, versionId)
        toast.success('优化完成！选定版本已激活')
        setShowFinalizeModal(false)
        await reload()
        setActiveRun(null)
      } catch (e) {
        const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail
        toast.error(typeof msg === 'string' ? msg : 'finalize 失败')
      } finally {
        setFinalizing(false)
      }
    },
    [activeRun, apiDefinitionId, reload],
  )

  const handleAbort = useCallback(async () => {
    if (!activeRun) return
    if (!window.confirm('确定放弃此次优化吗？已完成的轮次会保留作为历史。')) return
    try {
      await abortRun(apiDefinitionId, activeRun.id)
      toast.info('已放弃此次 Run')
      await reload()
      setActiveRun(null)
    } catch (e) {
      toast.error('放弃失败')
      console.error(e)
    }
  }, [activeRun, apiDefinitionId, reload])

  // ── Render ───────────────────────────────────────────────────────────────

  if (loadingList && !versionDetail) {
    return (
      <div className="flex items-center justify-center h-full bg-[#18181c]">
        <Loader2 className="w-6 h-6 animate-spin text-purple-500" />
      </div>
    )
  }

  if (versions.length === 0) {
    return (
      <div className="flex items-center justify-center h-full bg-[#18181c] text-gray-500">
        <div className="text-center">
          <Sparkles className="w-8 h-8 mx-auto mb-2 text-gray-700" />
          <p className="text-sm">尚无优化历史。请先点「开始优化」触发一次 Run。</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-[#18181c] text-white">
      {/* Country-scoped reflection agents — product-tech maintained,
          shared across all customer iterations for this country. */}
      <CountryReflectionAgents apiDefinitionId={apiDefinitionId} />
      <div className="border-b border-white/5">
        <VersionChips
          versions={versions}
          selectedId={selectedVersionId}
          onSelect={setSelectedVersionId}
        />
        {activeRun && (
          <RunStatusBar
            run={activeRun}
            advancing={advancing || optimizing}
            finalizing={finalizing}
            hasPendingEdits={hasPendingEdits}
            onAdvance={handleAdvance}
            onFinalize={() => setShowFinalizeModal(true)}
            onAbort={handleAbort}
          />
        )}
      </div>

      {/* 字段级准确率收敛看板：每轮 × 每字段热力表，让客户看到收敛过程 */}
      <FieldAccuracyHeatmap
        apiDefinitionId={apiDefinitionId}
        runId={versionDetail?.produced_by_run_id ?? activeRun?.id ?? null}
      />

      {/* 字段优化对比：每个字段 优化前→优化后 + Δ + 状态，可展开看逐样本识别结果 */}
      <FieldDiffComparison round={round} nextRound={nextRound} />

      <div className="flex-1 flex overflow-hidden">
        <ModuleList
          modules={versionDetail?.modules ?? []}
          selectedKey={selectedModuleKey}
          onSelect={setSelectedModuleKey}
          iterations={round?.iterations ?? []}
        />
        <div className="flex-1 overflow-y-auto">
          {loadingDetail ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="w-5 h-5 animate-spin text-purple-500" />
            </div>
          ) : selectedModule ? (
            <ModuleDetail
              module={selectedModule}
              iteration={selectedIteration}
              version={versionDetail}
              pendingEdit={pendingEdits[selectedModule.module_key]}
              onEditChange={(patch) =>
                setPendingEdits((prev) => ({
                  ...prev,
                  [selectedModule.module_key]: {
                    ...(prev[selectedModule.module_key] ?? {}),
                    ...patch,
                  },
                }))
              }
              onDiscardEdit={() =>
                setPendingEdits((prev) => {
                  const next = { ...prev }
                  delete next[selectedModule.module_key]
                  return next
                })
              }
            />
          ) : (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">
              选择左侧模块查看详情
            </div>
          )}
        </div>
      </div>

      {hasPendingEdits && (
        <div className="flex items-center justify-between px-4 py-2 border-t border-white/10 bg-purple-500/5">
          <div className="text-xs text-purple-300 flex items-center gap-2">
            <FileEdit className="w-3.5 h-3.5" />
            {Object.keys(pendingEdits).length} 个模块有未保存编辑
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setPendingEdits({})}
              className="px-3 py-1 text-xs text-gray-400 hover:text-white"
            >
              舍弃
            </button>
            <button
              onClick={handleSavePatch}
              disabled={savingPatch}
              className="flex items-center gap-1.5 px-3 py-1 text-xs rounded-md bg-purple-600 hover:bg-purple-700 text-white disabled:opacity-50"
            >
              {savingPatch ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Check className="w-3.5 h-3.5" />
              )}
              保存 patch (生成新派生版本)
            </button>
          </div>
        </div>
      )}

      {showFinalizeModal && activeRun && (
        <FinalizeModal
          versions={versions.filter(
            (v) =>
              v.id === activeRun.starting_version_id ||
              v.produced_by_run_id === activeRun.id,
          )}
          defaultId={selectedVersionId}
          finalizing={finalizing}
          onCancel={() => setShowFinalizeModal(false)}
          onConfirm={handleFinalize}
        />
      )}
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Version chips
// ──────────────────────────────────────────────────────────────────────────────

