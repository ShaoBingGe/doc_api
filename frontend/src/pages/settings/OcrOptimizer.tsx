import { useEffect, useMemo, useState } from 'react'
import { Loader2, Play, Sparkles, RefreshCw, CheckCircle2, ChevronRight, ChevronDown } from 'lucide-react'
import { toast } from '../../lib/toast'
import {
  activateOcrVersion,
  fetchApiDefinitions,
  fetchOcrIterations,
  fetchOcrRound,
  fetchOcrRun,
  fetchOcrRuns,
  fetchOcrVersion,
  fetchOcrVersions,
  initOcrOptimizer,
  triggerOptimization,
} from '../../lib/api-client'

// ── Types mirror backend pydantic schemas ────────────────────────────────────

interface ApiDef {
  id: string
  api_code: string
  name: string
  status: string
}

interface ModuleResp {
  id: string
  module_key: string
  display_name: string
  description: string
  json_path: string
  schema_fragment: Record<string, unknown>
  ocr_suggestions: Record<string, unknown>
  ocr_prompt: string
  order_index: number
  status: string
  module_accuracy: number | null
  created_at: string
}

interface VersionSummary {
  id: string
  version: number
  status: string
  overall_accuracy: number | null
  parent_version_id: string | null
  produced_by_run_id: string | null
  produced_in_round: number | null
  created_at: string
  activated_at: string | null
  module_count: number
  composed_prompt_preview: string
}

interface VersionDetail extends VersionSummary {
  api_definition_id: string
  composed_prompt: string
  composed_schema: Record<string, unknown> | null
  notes: string | null
  modules: ModuleResp[]
}

interface RunSummary {
  id: string
  api_definition_id: string
  starting_version_id: string
  resulting_version_id: string | null
  status: string
  max_rounds: number
  target_accuracy: number
  rounds_completed: number
  sample_document_ids: string[]
  llm_provider: string
  started_at: string
  completed_at: string | null
  error_message: string | null
  metrics: Record<string, unknown> | null
}

interface RoundSummary {
  id: string
  round_num: number
  phase: string
  overall_accuracy: number | null
  prompt_version_id: string
  next_version_id: string | null
  meta_decision: Record<string, unknown> | null
  duration_ms: number | null
  created_at: string
  completed_at: string | null
}

interface IterationResp {
  id: string
  module_key: string
  aggregate_accuracy: number
  aggregate_diff: Record<string, unknown> | null
  optimization_suggestion: string | null
  new_description: string | null
  new_ocr_suggestions: Record<string, unknown> | null
  new_ocr_prompt: string | null
  per_sample_results: unknown[]
  created_at: string
}

interface RoundDetail extends RoundSummary {
  per_sample_accuracy: Record<string, unknown> | null
  ocr_raw_outputs: Record<string, unknown> | null
  iterations: IterationResp[]
}

interface RunDetail extends RunSummary {
  rounds: RoundSummary[]
}

// ── Small helpers ────────────────────────────────────────────────────────────

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

function statusBadgeCls(status: string): string {
  const map: Record<string, string> = {
    active: 'bg-green-50 text-green-700 ring-green-200',
    draft: 'bg-gray-100 text-gray-600 ring-gray-200',
    archived: 'bg-amber-50 text-amber-700 ring-amber-200',
    running: 'bg-blue-50 text-blue-700 ring-blue-200',
    completed: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
    failed: 'bg-red-50 text-red-700 ring-red-200',
    cancelled: 'bg-gray-100 text-gray-500 ring-gray-200',
    deprecated: 'bg-orange-50 text-orange-700 ring-orange-200',
  }
  return map[status] ?? 'bg-gray-100 text-gray-600 ring-gray-200'
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="text-[11px] leading-relaxed bg-gray-900 text-gray-100 rounded-md p-3 overflow-auto max-h-72">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function Section({
  title,
  right,
  children,
}: {
  title: string
  right?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="bg-white rounded-xl border border-gray-200 mb-6">
      <header className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
        <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
        {right}
      </header>
      <div className="p-5">{children}</div>
    </section>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">{label}</div>
      <div className="text-sm text-gray-800 break-all">{children}</div>
    </div>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────

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

function ActiveVersionPanel({ detail }: { detail: VersionDetail }) {
  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-5">
        <Field label="version">v{detail.version}</Field>
        <Field label="overall_accuracy">{fmtPct(detail.overall_accuracy)}</Field>
        <Field label="modules">{detail.modules.length}</Field>
        <Field label="activated_at">{fmtDate(detail.activated_at)}</Field>
        <Field label="produced_by_run_id">
          <code className="text-xs">{detail.produced_by_run_id ?? '—'}</code>
        </Field>
        <Field label="produced_in_round">{detail.produced_in_round ?? '—'}</Field>
        <Field label="parent_version_id">
          <code className="text-xs">{detail.parent_version_id ?? '—'}</code>
        </Field>
        <Field label="notes">{detail.notes ?? '—'}</Field>
      </div>
      <details className="mb-4">
        <summary className="cursor-pointer text-xs font-semibold text-gray-700 hover:text-gray-900">
          composed_prompt
        </summary>
        <pre className="mt-2 text-[11px] leading-relaxed whitespace-pre-wrap bg-gray-50 border border-gray-200 rounded-md p-3 max-h-96 overflow-auto">
          {detail.composed_prompt}
        </pre>
      </details>
      <details>
        <summary className="cursor-pointer text-xs font-semibold text-gray-700 hover:text-gray-900">
          composed_schema
        </summary>
        <div className="mt-2">
          <JsonBlock value={detail.composed_schema} />
        </div>
      </details>
    </div>
  )
}

function VersionDetailPanel({
  detail,
  expandedModuleId,
  onToggleModule,
}: {
  detail: VersionDetail
  expandedModuleId: string | null
  onToggleModule: (id: string) => void
}) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Field label="id">
          <code className="text-xs">{detail.id}</code>
        </Field>
        <Field label="api_definition_id">
          <code className="text-xs">{detail.api_definition_id}</code>
        </Field>
        <Field label="overall_accuracy">{fmtPct(detail.overall_accuracy)}</Field>
        <Field label="produced_in_round">{detail.produced_in_round ?? '—'}</Field>
      </div>

      <details>
        <summary className="cursor-pointer text-xs font-semibold text-gray-700">
          composed_prompt
        </summary>
        <pre className="mt-2 text-[11px] leading-relaxed whitespace-pre-wrap bg-gray-50 border border-gray-200 rounded-md p-3 max-h-80 overflow-auto">
          {detail.composed_prompt}
        </pre>
      </details>

      <details>
        <summary className="cursor-pointer text-xs font-semibold text-gray-700">
          composed_schema
        </summary>
        <div className="mt-2">
          <JsonBlock value={detail.composed_schema} />
        </div>
      </details>

      <div>
        <div className="text-xs font-semibold text-gray-700 mb-2">
          模块 ({detail.modules.length})
        </div>
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          {detail.modules.map((m) => {
            const open = expandedModuleId === m.id
            return (
              <div key={m.id}>
                <button
                  onClick={() => onToggleModule(m.id)}
                  className="w-full flex items-center gap-3 text-left hover:bg-gray-50 px-3 py-2"
                >
                  {open ? (
                    <ChevronDown className="w-4 h-4 text-gray-400" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-gray-400" />
                  )}
                  <span className="text-xs font-mono font-semibold text-indigo-700">
                    #{m.order_index} {m.module_key}
                  </span>
                  <span className="text-xs text-gray-600">{m.display_name}</span>
                  <span
                    className={`inline-flex items-center px-2 py-0.5 text-[10px] font-medium rounded ring-1 ${statusBadgeCls(m.status)}`}
                  >
                    {m.status}
                  </span>
                  <span className="ml-auto text-xs text-gray-400">
                    json_path: <code>{m.json_path}</code>
                  </span>
                </button>
                {open && <ModuleDetailPanel m={m} />}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function ModuleDetailPanel({ m }: { m: ModuleResp }) {
  return (
    <div className="px-5 py-4 bg-gray-50 space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Field label="id">
          <code className="text-xs">{m.id}</code>
        </Field>
        <Field label="module_key">
          <code className="text-xs">{m.module_key}</code>
        </Field>
        <Field label="display_name">{m.display_name}</Field>
        <Field label="json_path">
          <code className="text-xs">{m.json_path}</code>
        </Field>
        <Field label="order_index">{m.order_index}</Field>
        <Field label="status">{m.status}</Field>
        <Field label="module_accuracy">{fmtPct(m.module_accuracy)}</Field>
        <Field label="created_at">{fmtDate(m.created_at)}</Field>
      </div>
      <Field label="description">
        <div className="text-sm text-gray-700 whitespace-pre-wrap">{m.description}</div>
      </Field>
      <Field label="ocr_prompt">
        <pre className="text-[11px] leading-relaxed whitespace-pre-wrap bg-white border border-gray-200 rounded-md p-3 max-h-60 overflow-auto">
          {m.ocr_prompt}
        </pre>
      </Field>
      <Field label="ocr_suggestions">
        <JsonBlock value={m.ocr_suggestions} />
      </Field>
      <Field label="schema_fragment">
        <JsonBlock value={m.schema_fragment} />
      </Field>
    </div>
  )
}

function RunDetailPanel({
  detail,
  expandedRoundNum,
  expandedRoundDetail,
  expandedIterationId,
  onToggleRound,
  onToggleIteration,
}: {
  detail: RunDetail
  expandedRoundNum: number | null
  expandedRoundDetail: RoundDetail | null
  expandedIterationId: string | null
  onToggleRound: (n: number) => void
  onToggleIteration: (id: string) => void
}) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Field label="id">
          <code className="text-xs">{detail.id}</code>
        </Field>
        <Field label="status">{detail.status}</Field>
        <Field label="rounds_completed">
          {detail.rounds_completed}/{detail.max_rounds}
        </Field>
        <Field label="target_accuracy">{fmtPct(detail.target_accuracy)}</Field>
        <Field label="starting_version_id">
          <code className="text-xs">{detail.starting_version_id}</code>
        </Field>
        <Field label="resulting_version_id">
          <code className="text-xs">{detail.resulting_version_id ?? '—'}</code>
        </Field>
        <Field label="llm_provider">
          <code className="text-xs">{detail.llm_provider}</code>
        </Field>
        <Field label="started_at">{fmtDate(detail.started_at)}</Field>
        <Field label="completed_at">{fmtDate(detail.completed_at)}</Field>
        <Field label="sample_document_ids">
          <div className="text-xs font-mono">{detail.sample_document_ids.length} 个</div>
        </Field>
        <Field label="error_message">
          <div className="text-xs text-red-600">{detail.error_message ?? '—'}</div>
        </Field>
        <Field label="metrics">
          <code className="text-xs">{detail.metrics ? '见下方' : '—'}</code>
        </Field>
      </div>

      {detail.metrics && (
        <details>
          <summary className="cursor-pointer text-xs font-semibold text-gray-700">metrics</summary>
          <div className="mt-2">
            <JsonBlock value={detail.metrics} />
          </div>
        </details>
      )}

      <div>
        <div className="text-xs font-semibold text-gray-700 mb-2">
          轮次 ({detail.rounds.length})
        </div>
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          {detail.rounds.map((rd) => {
            const open = expandedRoundNum === rd.round_num
            return (
              <div key={rd.id}>
                <button
                  onClick={() => onToggleRound(rd.round_num)}
                  className="w-full flex items-center gap-3 text-left hover:bg-gray-50 px-3 py-2"
                >
                  {open ? (
                    <ChevronDown className="w-4 h-4 text-gray-400" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-gray-400" />
                  )}
                  <span className="text-xs font-mono font-semibold text-gray-900">
                    Round {rd.round_num}
                  </span>
                  <span
                    className={`inline-flex items-center px-2 py-0.5 text-[10px] font-medium rounded ring-1 ${statusBadgeCls(rd.phase)}`}
                  >
                    {rd.phase}
                  </span>
                  <span className="text-xs text-gray-500">
                    准确率 {fmtPct(rd.overall_accuracy)} · {rd.duration_ms ?? '—'} ms
                  </span>
                  <span className="ml-auto text-xs text-gray-400">{fmtDate(rd.created_at)}</span>
                </button>
                {open && (
                  <div className="px-5 py-4 bg-gray-50">
                    {!expandedRoundDetail || expandedRoundDetail.round_num !== rd.round_num ? (
                      <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                    ) : (
                      <RoundDetailPanel
                        detail={expandedRoundDetail}
                        expandedIterationId={expandedIterationId}
                        onToggleIteration={onToggleIteration}
                      />
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function RoundDetailPanel({
  detail,
  expandedIterationId,
  onToggleIteration,
}: {
  detail: RoundDetail
  expandedIterationId: string | null
  onToggleIteration: (id: string) => void
}) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Field label="id">
          <code className="text-xs">{detail.id}</code>
        </Field>
        <Field label="phase">{detail.phase}</Field>
        <Field label="overall_accuracy">{fmtPct(detail.overall_accuracy)}</Field>
        <Field label="duration_ms">{detail.duration_ms ?? '—'}</Field>
        <Field label="prompt_version_id">
          <code className="text-xs">{detail.prompt_version_id}</code>
        </Field>
        <Field label="next_version_id">
          <code className="text-xs">{detail.next_version_id ?? '—'}</code>
        </Field>
        <Field label="completed_at">{fmtDate(detail.completed_at)}</Field>
      </div>

      {detail.per_sample_accuracy && (
        <details>
          <summary className="cursor-pointer text-xs font-semibold text-gray-700">
            per_sample_accuracy
          </summary>
          <div className="mt-2">
            <JsonBlock value={detail.per_sample_accuracy} />
          </div>
        </details>
      )}
      {detail.ocr_raw_outputs && (
        <details>
          <summary className="cursor-pointer text-xs font-semibold text-gray-700">
            ocr_raw_outputs
          </summary>
          <div className="mt-2">
            <JsonBlock value={detail.ocr_raw_outputs} />
          </div>
        </details>
      )}
      {detail.meta_decision && (
        <details>
          <summary className="cursor-pointer text-xs font-semibold text-gray-700">
            meta_decision
          </summary>
          <div className="mt-2">
            <JsonBlock value={detail.meta_decision} />
          </div>
        </details>
      )}

      <div>
        <div className="text-xs font-semibold text-gray-700 mb-2">
          模块迭代 ({detail.iterations.length})
        </div>
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          {detail.iterations.map((it) => {
            const open = expandedIterationId === it.id
            return (
              <div key={it.id}>
                <button
                  onClick={() => onToggleIteration(it.id)}
                  className="w-full flex items-center gap-3 text-left hover:bg-gray-50 px-3 py-2"
                >
                  {open ? (
                    <ChevronDown className="w-4 h-4 text-gray-400" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-gray-400" />
                  )}
                  <span className="text-xs font-mono font-semibold text-indigo-700">
                    {it.module_key}
                  </span>
                  <span className="text-xs text-gray-500">
                    准确率 {fmtPct(it.aggregate_accuracy)}
                  </span>
                  <span className="ml-auto text-xs text-gray-400">{fmtDate(it.created_at)}</span>
                </button>
                {open && (
                  <div className="px-5 py-4 bg-gray-50 space-y-3">
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                      <Field label="id">
                        <code className="text-xs">{it.id}</code>
                      </Field>
                      <Field label="module_key">
                        <code className="text-xs">{it.module_key}</code>
                      </Field>
                      <Field label="aggregate_accuracy">{fmtPct(it.aggregate_accuracy)}</Field>
                    </div>
                    {it.optimization_suggestion && (
                      <Field label="optimization_suggestion">
                        <div className="text-sm whitespace-pre-wrap">{it.optimization_suggestion}</div>
                      </Field>
                    )}
                    {it.new_description && (
                      <Field label="new_description">
                        <div className="text-sm whitespace-pre-wrap">{it.new_description}</div>
                      </Field>
                    )}
                    {it.new_ocr_prompt && (
                      <Field label="new_ocr_prompt">
                        <pre className="text-[11px] leading-relaxed whitespace-pre-wrap bg-white border border-gray-200 rounded-md p-3 max-h-60 overflow-auto">
                          {it.new_ocr_prompt}
                        </pre>
                      </Field>
                    )}
                    {it.new_ocr_suggestions && (
                      <Field label="new_ocr_suggestions">
                        <JsonBlock value={it.new_ocr_suggestions} />
                      </Field>
                    )}
                    {it.aggregate_diff && (
                      <Field label="aggregate_diff">
                        <JsonBlock value={it.aggregate_diff} />
                      </Field>
                    )}
                    {it.per_sample_results.length > 0 && (
                      <Field label="per_sample_results">
                        <JsonBlock value={it.per_sample_results} />
                      </Field>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
