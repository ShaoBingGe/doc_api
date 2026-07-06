// OCR 优化平台设置页（薄入口）。
// L2.2：1058 行单文件已按分组拆到 ./ocr-optimizer/（helpers 类型+纯函数 /
// primitives 基础 UI / detail-panels 详情面板）——本文件只保留主编排组件。
// 路由的 default import 路径不变。
import { useEffect, useMemo, useState } from 'react'
import { Loader2, Play, Sparkles, RefreshCw, CheckCircle2, ChevronRight, ChevronDown } from 'lucide-react'
import { toast } from '../../lib/toast'
import {
  activateOcrVersion,
  fetchApiDefinitions,
  fetchOcrRound,
  fetchOcrRun,
  fetchOcrRuns,
  fetchOcrVersion,
  fetchOcrVersions,
  initOcrOptimizer,
  triggerOptimization,
} from '../../lib/api-client'
import { Section, Field } from './ocr-optimizer/primitives'
import { ActiveVersionPanel, VersionDetailPanel, RunDetailPanel } from './ocr-optimizer/detail-panels'
import {
  fmtPct, fmtDate, statusBadgeCls,
  type ApiDef, type VersionSummary, type VersionDetail,
  type RunSummary, type RoundDetail, type RunDetail,
} from './ocr-optimizer/helpers'

export default function OcrOptimizer() {
  // top-level: API selection
  const [apis, setApis] = useState<ApiDef[]>([])
  const [selectedApiId, setSelectedApiId] = useState<string | null>(null)
  const [loadingApis, setLoadingApis] = useState(false)

  // versions
  const [versions, setVersions] = useState<VersionSummary[]>([])
  const [loadingVersions, setLoadingVersions] = useState(false)
  const [activeVersionDetail, setActiveVersionDetail] = useState<VersionDetail | null>(null)
  const [expandedVersionId, setExpandedVersionId] = useState<string | null>(null)
  const [expandedVersionDetail, setExpandedVersionDetail] = useState<VersionDetail | null>(null)
  const [expandedModuleId, setExpandedModuleId] = useState<string | null>(null)

  // runs
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null)
  const [expandedRunDetail, setExpandedRunDetail] = useState<RunDetail | null>(null)
  const [expandedRoundNum, setExpandedRoundNum] = useState<number | null>(null)
  const [expandedRoundDetail, setExpandedRoundDetail] = useState<RoundDetail | null>(null)
  const [expandedIterationId, setExpandedIterationId] = useState<string | null>(null)

  // actions
  const [initing, setIniting] = useState(false)
  const [optimizing, setOptimizing] = useState(false)
  const [sampleIdsRaw, setSampleIdsRaw] = useState('')
  const [maxRounds, setMaxRounds] = useState<string>('3')
  const [targetAcc, setTargetAcc] = useState<string>('0.95')
  const [useLlmForInit, setUseLlmForInit] = useState(false)

  // ── Load APIs once ────────────────────────────────────────────────────────
  useEffect(() => {
    setLoadingApis(true)
    fetchApiDefinitions()
      .then((res) => {
        const data = res.data
        const list: ApiDef[] = Array.isArray(data) ? data : (data?.items ?? [])
        setApis(list)
        if (list.length > 0) setSelectedApiId((cur) => cur ?? list[0].id)
      })
      .catch(() => toast.error('加载 API 列表失败'))
      .finally(() => setLoadingApis(false))
  }, [])

  // ── Reload versions + runs when API changes ───────────────────────────────
  useEffect(() => {
    if (!selectedApiId) return
    reloadAll(selectedApiId)
    // reset expansions
    setExpandedVersionId(null)
    setExpandedVersionDetail(null)
    setExpandedRunId(null)
    setExpandedRunDetail(null)
    setExpandedRoundNum(null)
    setExpandedRoundDetail(null)
    setExpandedIterationId(null)
    setActiveVersionDetail(null)
  }, [selectedApiId])

  async function reloadAll(apiId: string) {
    await Promise.all([loadVersions(apiId), loadRuns(apiId)])
  }

  async function loadVersions(apiId: string) {
    setLoadingVersions(true)
    try {
      const res = await fetchOcrVersions(apiId)
      const list: VersionSummary[] = res.data ?? []
      list.sort((a, b) => b.version - a.version)
      setVersions(list)
      // auto-load detail for active version
      const active = list.find((v) => v.status === 'active')
      if (active) {
        const det = await fetchOcrVersion(apiId, active.id)
        setActiveVersionDetail(det.data)
      } else {
        setActiveVersionDetail(null)
      }
    } catch {
      setVersions([])
      setActiveVersionDetail(null)
    } finally {
      setLoadingVersions(false)
    }
  }

  async function loadRuns(apiId: string) {
    setLoadingRuns(true)
    try {
      const res = await fetchOcrRuns(apiId)
      const list: RunSummary[] = res.data ?? []
      list.sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())
      setRuns(list)
    } catch {
      setRuns([])
    } finally {
      setLoadingRuns(false)
    }
  }

  // ── Action handlers ──────────────────────────────────────────────────────
  const parsedSampleIds = useMemo(
    () =>
      sampleIdsRaw
        .split(/[\s,]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    [sampleIdsRaw],
  )

  async function handleInit() {
    if (!selectedApiId) return
    setIniting(true)
    try {
      await initOcrOptimizer(selectedApiId, {
        sample_document_ids: parsedSampleIds.length > 0 ? parsedSampleIds : undefined,
        activate: true,
        use_llm_for_modules: useLlmForInit,
      })
      toast.success('已生成初始 Prompt 版本')
      await reloadAll(selectedApiId)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof msg === 'string' ? msg : '初始化失败')
    } finally {
      setIniting(false)
    }
  }

  async function handleOptimize() {
    if (!selectedApiId) return
    setOptimizing(true)
    try {
      const res = await triggerOptimization(selectedApiId, {
        max_rounds: maxRounds ? Number(maxRounds) : null,
        target_accuracy: targetAcc ? Number(targetAcc) : null,
        sample_document_ids: parsedSampleIds.length > 0 ? parsedSampleIds : null,
      })
      const data = res.data
      if (data.status === 'completed') {
        toast.success(`优化完成（${data.rounds_completed} 轮，准确率 ${fmtPct(data.overall_accuracy)}）`)
      } else if (data.status === 'failed') {
        toast.error(data.error_message || '优化失败')
      } else {
        toast.info(`Run 状态: ${data.status}`)
      }
      await reloadAll(selectedApiId)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof msg === 'string' ? msg : '触发优化失败')
    } finally {
      setOptimizing(false)
    }
  }

  async function handleActivate(versionId: string) {
    if (!selectedApiId) return
    try {
      await activateOcrVersion(selectedApiId, versionId)
      toast.success('已激活')
      await loadVersions(selectedApiId)
    } catch {
      toast.error('激活失败')
    }
  }

  async function toggleVersion(v: VersionSummary) {
    if (!selectedApiId) return
    if (expandedVersionId === v.id) {
      setExpandedVersionId(null)
      setExpandedVersionDetail(null)
      setExpandedModuleId(null)
      return
    }
    setExpandedVersionId(v.id)
    setExpandedVersionDetail(null)
    setExpandedModuleId(null)
    try {
      const res = await fetchOcrVersion(selectedApiId, v.id)
      setExpandedVersionDetail(res.data)
    } catch {
      toast.error('加载版本详情失败')
    }
  }

  async function toggleRun(r: RunSummary) {
    if (!selectedApiId) return
    if (expandedRunId === r.id) {
      setExpandedRunId(null)
      setExpandedRunDetail(null)
      setExpandedRoundNum(null)
      setExpandedRoundDetail(null)
      setExpandedIterationId(null)
      return
    }
    setExpandedRunId(r.id)
    setExpandedRunDetail(null)
    setExpandedRoundNum(null)
    setExpandedRoundDetail(null)
    try {
      const res = await fetchOcrRun(selectedApiId, r.id)
      setExpandedRunDetail(res.data)
    } catch {
      toast.error('加载 Run 详情失败')
    }
  }

  async function toggleRound(runId: string, roundNum: number) {
    if (!selectedApiId) return
    if (expandedRoundNum === roundNum) {
      setExpandedRoundNum(null)
      setExpandedRoundDetail(null)
      setExpandedIterationId(null)
      return
    }
    setExpandedRoundNum(roundNum)
    setExpandedRoundDetail(null)
    setExpandedIterationId(null)
    try {
      const res = await fetchOcrRound(selectedApiId, runId, roundNum)
      setExpandedRoundDetail(res.data)
    } catch {
      toast.error('加载轮次详情失败')
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────
  if (loadingApis) {
    return (
      <div className="flex items-center justify-center py-32">
        <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    )
  }

  if (apis.length === 0) {
    return (
      <div className="p-10 text-center text-gray-500">
        暂无 API，定制一个新的 API 后再使用优化器。
      </div>
    )
  }

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* API selector + actions */}
      <Section
        title="OCR Prompt 优化器"
        right={
          <button
            onClick={() => selectedApiId && reloadAll(selectedApiId)}
            className="inline-flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-800"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            刷新
          </button>
        }
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Field label="选择 API">
            <select
              value={selectedApiId ?? ''}
              onChange={(e) => setSelectedApiId(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              {apis.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.api_code}) · {a.status}
                </option>
              ))}
            </select>
          </Field>
          <Field label="样本文档 ID（逗号或空格分隔，可留空，使用 API 默认绑定）">
            <input
              type="text"
              value={sampleIdsRaw}
              onChange={(e) => setSampleIdsRaw(e.target.value)}
              placeholder="uuid1, uuid2, uuid3"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </Field>
          <Field label="最大轮数 (max_rounds)">
            <input
              type="number"
              min={1}
              max={10}
              value={maxRounds}
              onChange={(e) => setMaxRounds(e.target.value)}
              className="w-32 border border-gray-300 rounded-lg px-3 py-2 text-sm"
            />
          </Field>
          <Field label="目标准确率 (target_accuracy)">
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={targetAcc}
              onChange={(e) => setTargetAcc(e.target.value)}
              className="w-32 border border-gray-300 rounded-lg px-3 py-2 text-sm"
            />
          </Field>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={useLlmForInit}
              onChange={(e) => setUseLlmForInit(e.target.checked)}
              className="rounded border-gray-300"
            />
            初始化时用 LLM 生成模块描述
          </label>
          <button
            onClick={handleInit}
            disabled={initing || !selectedApiId}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-gray-700 hover:bg-gray-800 disabled:opacity-50"
          >
            {initing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            初始化版本
          </button>
          <button
            onClick={handleOptimize}
            disabled={optimizing || !selectedApiId}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50"
          >
            {optimizing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
            触发优化（同步阻塞）
          </button>
        </div>
      </Section>

      {/* Active version snapshot */}
      <Section title="当前激活版本">
        {!activeVersionDetail ? (
          <div className="text-sm text-gray-400">该 API 尚无激活版本，先点击 “初始化版本”。</div>
        ) : (
          <ActiveVersionPanel detail={activeVersionDetail} />
        )}
      </Section>

      {/* Versions list */}
      <Section
        title="所有 Prompt 版本"
        right={loadingVersions ? <Loader2 className="w-4 h-4 animate-spin text-gray-400" /> : null}
      >
        {versions.length === 0 ? (
          <div className="text-sm text-gray-400">尚无版本。</div>
        ) : (
          <div className="divide-y divide-gray-100">
            {versions.map((v) => {
              const open = expandedVersionId === v.id
              return (
                <div key={v.id} className="py-3">
                  <button
                    onClick={() => toggleVersion(v)}
                    className="w-full flex items-center gap-3 text-left hover:bg-gray-50 rounded-lg px-2 py-2"
                  >
                    {open ? (
                      <ChevronDown className="w-4 h-4 text-gray-400" />
                    ) : (
                      <ChevronRight className="w-4 h-4 text-gray-400" />
                    )}
                    <span className="text-sm font-mono font-semibold text-gray-900">v{v.version}</span>
                    <span
                      className={`inline-flex items-center px-2 py-0.5 text-[11px] font-medium rounded ring-1 ${statusBadgeCls(v.status)}`}
                    >
                      {v.status}
                    </span>
                    <span className="text-xs text-gray-500">
                      {v.module_count} 模块 · 准确率 {fmtPct(v.overall_accuracy)} ·{' '}
                      {v.produced_in_round != null ? `Round ${v.produced_in_round}` : '初始'}
                    </span>
                    <span className="ml-auto text-xs text-gray-400">{fmtDate(v.created_at)}</span>
                    {v.status !== 'active' && (
                      <span
                        role="button"
                        tabIndex={0}
                        onClick={(e) => {
                          e.stopPropagation()
                          handleActivate(v.id)
                        }}
                        className="ml-2 inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-indigo-200 text-indigo-700 hover:bg-indigo-50"
                      >
                        <CheckCircle2 className="w-3 h-3" />
                        激活
                      </span>
                    )}
                  </button>
                  {open && (
                    <div className="ml-7 mt-3">
                      {!expandedVersionDetail ? (
                        <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                      ) : (
                        <VersionDetailPanel
                          detail={expandedVersionDetail}
                          expandedModuleId={expandedModuleId}
                          onToggleModule={(id) =>
                            setExpandedModuleId(expandedModuleId === id ? null : id)
                          }
                        />
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </Section>

      {/* Runs list */}
      <Section
        title="优化 Run 历史"
        right={loadingRuns ? <Loader2 className="w-4 h-4 animate-spin text-gray-400" /> : null}
      >
        {runs.length === 0 ? (
          <div className="text-sm text-gray-400">尚无运行记录。</div>
        ) : (
          <div className="divide-y divide-gray-100">
            {runs.map((r) => {
              const open = expandedRunId === r.id
              return (
                <div key={r.id} className="py-3">
                  <button
                    onClick={() => toggleRun(r)}
                    className="w-full flex items-center gap-3 text-left hover:bg-gray-50 rounded-lg px-2 py-2"
                  >
                    {open ? (
                      <ChevronDown className="w-4 h-4 text-gray-400" />
                    ) : (
                      <ChevronRight className="w-4 h-4 text-gray-400" />
                    )}
                    <code className="text-xs font-mono text-gray-700">{r.id.slice(0, 8)}</code>
                    <span
                      className={`inline-flex items-center px-2 py-0.5 text-[11px] font-medium rounded ring-1 ${statusBadgeCls(r.status)}`}
                    >
                      {r.status}
                    </span>
                    <span className="text-xs text-gray-500">
                      {r.rounds_completed}/{r.max_rounds} 轮 · 目标 {fmtPct(r.target_accuracy)} ·{' '}
                      {r.sample_document_ids.length} 样本
                    </span>
                    <span className="ml-auto text-xs text-gray-400">{fmtDate(r.started_at)}</span>
                  </button>
                  {open && (
                    <div className="ml-7 mt-3">
                      {!expandedRunDetail ? (
                        <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                      ) : (
                        <RunDetailPanel
                          detail={expandedRunDetail}
                          expandedRoundNum={expandedRoundNum}
                          expandedRoundDetail={expandedRoundDetail}
                          expandedIterationId={expandedIterationId}
                          onToggleRound={(rn) => toggleRound(r.id, rn)}
                          onToggleIteration={(id) =>
                            setExpandedIterationId(expandedIterationId === id ? null : id)
                          }
                        />
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </Section>
    </div>
  )
}

// ── Sub-panels ──────────────────────────────────────────────────────────────

