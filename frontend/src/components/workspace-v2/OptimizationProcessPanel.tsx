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
 */
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Sparkles,
  Loader2,
  ChevronDown,
  ChevronRight,
  Pencil,
  X,
  Check,
  Plus,
  AlertCircle,
  PlayCircle,
  StopCircle,
  FileEdit,
  CheckCircle2,
} from 'lucide-react'
import { cn } from '../../lib/utils'
import { toast } from '../../lib/toast'
import CountryReflectionAgents from './CountryReflectionAgents'
import {
  fetchOcrVersions,
  fetchOcrVersion,
  fetchOcrRuns,
  fetchOcrRound,
  fetchFieldAccuracy,
  advanceRound,
  finalizeRun,
  abortRun,
  manualPatchVersion,
  type ModuleEditPayload,
} from '../../lib/api-client'

interface OptimizationProcessPanelProps {
  apiDefinitionId: string
  reloadKey: number
  optimizing: boolean
}

// ── Types from backend ──────────────────────────────────────────────────────

interface VersionSummary {
  id: string
  version: string
  status: string
  origin: string
  overall_accuracy: number | null
  parent_version_id: string | null
  produced_by_run_id: string | null
  produced_in_round: number | null
  created_at: string
  activated_at: string | null
  module_count: number
  composed_prompt_preview: string
}

interface OcrModuleResponse {
  id: string
  module_key: string
  display_name: string
  description: string
  json_path: string
  schema_fragment: Record<string, unknown>
  ocr_suggestions: Record<string, unknown>
  ocr_prompt: string
  skill_ids: string[]
  order_index: number
  module_accuracy: number | null
}

interface VersionDetail extends VersionSummary {
  composed_prompt: string
  composed_schema: Record<string, unknown> | null
  modules: OcrModuleResponse[]
  notes: string | null
}

interface IterationResponse {
  id: string
  module_key: string
  aggregate_accuracy: number
  aggregate_diff: { differences_description?: string; differences_reason_analysis?: string } | null
  optimization_suggestion: string | null
  new_description: string | null
  new_ocr_suggestions: Record<string, unknown> | null
  new_ocr_prompt: string | null
  skill_feedback: string | null
  per_sample_results: Array<{
    sample_doc_id: string
    ocr_sliced: unknown
    ground_truth: unknown
    matched: boolean
    field_accuracy: number
    diff_detail: string
  }>
}

interface RoundDetail {
  id: string
  round_num: number
  phase: string
  overall_accuracy: number | null
  per_sample_accuracy: Record<string, number> | null
  ocr_raw_outputs: Record<string, unknown> | null
  meta_decision: Record<string, unknown> | null
  iterations: IterationResponse[]
  next_version_id: string | null
}

interface RunSummary {
  id: string
  status: string
  starting_version_id: string
  resulting_version_id: string | null
  rounds_completed: number
  current_round_num: number
  max_rounds: number
  error_message: string | null
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function accPct(a: number | null | undefined): string {
  if (a == null) return '—'
  return `${Math.round(a * 100)}%`
}

function originLabel(origin: string): { label: string; color: string } {
  if (origin === 'manual_edit')
    return { label: '✏ manual', color: 'bg-purple-500/20 text-purple-300 border-purple-500/40' }
  if (origin === 'round')
    return { label: 'round', color: 'bg-blue-500/20 text-blue-300 border-blue-500/40' }
  return { label: 'init', color: 'bg-gray-500/20 text-gray-400 border-gray-500/40' }
}

// ── Field optimization diff (优化前 → 优化后) ─────────────────────────────────
//
// One row per field: accuracy 优化前→优化后 + Δ + status. Click to expand the
// per-sample recognition results (正确值 / 优化前 / 优化后). "优化前" = the
// producing round's per-sample (prior version's OCR); "优化后" = the next round's
// per-sample (this version's OCR). When there's no next round (last version),
// the after column shows "—（待激活后体现）" and we still show the new prompt.

function _val(v: unknown): string {
  if (v === null || v === undefined) return '∅'
  if (Array.isArray(v)) {
    if (v.length === 1) return _val(v[0])
    return JSON.stringify(v)
  }
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

type DiffRow = {
  key: string
  before: IterationResponse | null
  after: IterationResponse | null
  beforeAcc: number | null
  afterAcc: number | null
  delta: number | null
  beforeMatch: number; beforeN: number
  afterMatch: number; afterN: number
  status: { label: string; cls: string }
  regressed: boolean
  changed: boolean
}

function _matchStats(it: IterationResponse | null): { m: number; n: number } {
  const ps = it?.per_sample_results ?? []
  return { m: ps.filter((p) => p.matched).length, n: ps.length }
}

// ── 字段级准确率收敛看板（每轮 × 每字段热力表） ─────────────────────────────
//
// Lets the customer SEE convergence: rows = fields, columns = rounds, cells =
// per-field accuracy (red→amber→green). The top "总体" row tracks overall
// accuracy per round; a trend column shows last−first delta. Worst fields sort
// to the top so problem fields surface. Data: GET .../runs/{id}/field-accuracy.

interface FieldAccuracyData {
  run_id: string
  fields: { module_key: string; display_name: string }[]
  rounds: {
    round_num: number
    overall_accuracy: number | null
    phase: string
    fields: Record<string, number>
  }[]
}

function accCellCls(a: number | null | undefined): string {
  if (a == null) return 'text-gray-600'
  if (a >= 0.999) return 'bg-emerald-500/20 text-emerald-300'
  if (a >= 0.8) return 'bg-amber-500/15 text-amber-300'
  if (a >= 0.5) return 'bg-orange-500/15 text-orange-300'
  return 'bg-red-500/15 text-red-300'
}

function FieldAccuracyHeatmap({
  apiDefinitionId,
  runId,
}: {
  apiDefinitionId: string
  runId: string | null
}) {
  const [open, setOpen] = useState(true)
  const [data, setData] = useState<FieldAccuracyData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!runId) {
      setData(null)
      return
    }
    let alive = true
    setLoading(true)
    fetchFieldAccuracy(apiDefinitionId, runId)
      .then((res) => {
        if (alive) setData(res.data as FieldAccuracyData)
      })
      .catch((e) => {
        console.warn('field-accuracy failed', e)
        if (alive) setData(null)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [apiDefinitionId, runId])

  const rows = useMemo(() => {
    if (!data) return []
    return data.fields
      .map((f) => {
        const series = data.rounds.map((r) => {
          const v = r.fields[f.module_key]
          return v === undefined ? null : v
        })
        const present = series.filter((v): v is number => v != null)
        const first = present[0] ?? null
        const last = present[present.length - 1] ?? null
        const delta = first != null && last != null ? last - first : null
        return { ...f, series, last, delta }
      })
      .sort((a, b) => (a.last ?? 1) - (b.last ?? 1)) // worst first
  }, [data])

  if (!runId) return null

  return (
    <div className="border-b border-white/5 bg-[#1b1b20]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-200 hover:text-white"
      >
        {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        <span className="font-medium">字段级准确率收敛</span>
        <span className="text-xs text-gray-500">
          {data ? `${data.rounds.length} 轮 · ${data.fields.length} 字段` : loading ? '加载中…' : '无数据'}
        </span>
      </button>

      {open && (
        <div className="max-h-[40vh] overflow-auto px-2 pb-2">
          {loading && !data ? (
            <div className="px-3 py-3 text-xs text-gray-500">加载中…</div>
          ) : !data || data.rounds.length === 0 ? (
            <div className="px-3 py-3 text-xs text-gray-500">
              本 Run 尚无逐轮字段评分（迭代开始后逐轮填充）。
            </div>
          ) : (
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 bg-[#1b1b20]">
                <tr className="text-gray-500 text-left">
                  <th className="py-1.5 px-2 font-medium">字段</th>
                  {data.rounds.map((r) => (
                    <th key={r.round_num} className="py-1.5 px-2 font-medium text-center">
                      R{r.round_num}
                    </th>
                  ))}
                  <th className="py-1.5 px-2 font-medium text-center">趋势</th>
                </tr>
              </thead>
              <tbody>
                {/* Overall row */}
                <tr className="border-t border-white/10 bg-white/5">
                  <td className="py-1.5 px-2 font-medium text-gray-200">总体</td>
                  {data.rounds.map((r) => (
                    <td key={r.round_num} className="py-1 px-1 text-center">
                      <span className={cn('px-1.5 py-0.5 rounded font-medium', accCellCls(r.overall_accuracy))}>
                        {accPct(r.overall_accuracy)}
                      </span>
                    </td>
                  ))}
                  <td className="py-1 px-2 text-center text-gray-500">—</td>
                </tr>
                {rows.map((row) => (
                  <tr key={row.module_key} className="border-t border-white/5 hover:bg-white/5">
                    <td className="py-1.5 px-2">
                      <div className="font-mono text-gray-300 truncate max-w-[160px]" title={row.display_name}>
                        {row.module_key}
                      </div>
                    </td>
                    {row.series.map((v, i) => (
                      <td key={i} className="py-1 px-1 text-center">
                        {v == null ? (
                          <span className="text-gray-700">·</span>
                        ) : (
                          <span className={cn('px-1.5 py-0.5 rounded font-medium', accCellCls(v))}>
                            {accPct(v)}
                          </span>
                        )}
                      </td>
                    ))}
                    <td
                      className={cn(
                        'py-1 px-2 text-center font-medium',
                        row.delta == null
                          ? 'text-gray-600'
                          : row.delta > 1e-4
                          ? 'text-emerald-400'
                          : row.delta < -1e-4
                          ? 'text-red-400'
                          : 'text-gray-500',
                      )}
                    >
                      {row.delta == null
                        ? '—'
                        : `${row.delta > 0 ? '+' : ''}${Math.round(row.delta * 100)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}

function FieldDiffComparison({
  round,
  nextRound,
}: {
  round: RoundDetail | null
  nextRound: RoundDetail | null
}) {
  const [open, setOpen] = useState(true)            // default-open so it's discoverable
  const [onlyChanged, setOnlyChanged] = useState(true)  // default: only changed fields
  const [expandedKey, setExpandedKey] = useState<string | null>(null)

  const rows = useMemo<DiffRow[]>(() => {
    if (!round) return []
    const beforeByKey = new Map((round.iterations ?? []).map((it) => [it.module_key, it]))
    const afterByKey = new Map((nextRound?.iterations ?? []).map((it) => [it.module_key, it]))
    const keys = Array.from(new Set([...beforeByKey.keys(), ...afterByKey.keys()]))

    return keys.map((key) => {
      const before = beforeByKey.get(key) ?? null
      const after = afterByKey.get(key) ?? null
      const beforeAcc = before?.aggregate_accuracy ?? null
      const afterAcc = after?.aggregate_accuracy ?? null
      const delta = (beforeAcc != null && afterAcc != null) ? afterAcc - beforeAcc : null
      const { m: beforeMatch, n: beforeN } = _matchStats(before)
      const { m: afterMatch, n: afterN } = _matchStats(after)
      const regressed = (before?.optimization_suggestion ?? '').includes('[REGRESSION]')
      const isNew = !before && !!after
      const promptChanged = !!before?.new_ocr_prompt || !!before?.new_description
      let status: { label: string; cls: string }
      if (isNew) status = { label: '新增字段', cls: 'bg-cyan-500/20 text-cyan-300' }
      else if (regressed) status = { label: '回退/下降', cls: 'bg-amber-500/20 text-amber-300' }
      else if (delta != null && delta > 1e-4) status = { label: '改进', cls: 'bg-emerald-500/20 text-emerald-300' }
      else if (delta != null && delta < -1e-4) status = { label: '下降', cls: 'bg-red-500/20 text-red-300' }
      else if (promptChanged && afterAcc == null) status = { label: '已优化·待确认', cls: 'bg-blue-500/20 text-blue-300' }
      else if ((beforeAcc ?? 0) >= 0.999) status = { label: '通过', cls: 'bg-emerald-500/15 text-emerald-300' }
      else status = { label: '无变化', cls: 'bg-white/10 text-gray-400' }
      const changed = isNew || regressed || promptChanged
        || (delta != null && Math.abs(delta) > 1e-4)
      return { key, before, after, beforeAcc, afterAcc, delta, beforeMatch, beforeN,
        afterMatch, afterN, status, regressed, changed }
    }).sort((a, b) => {
      if (a.regressed !== b.regressed) return a.regressed ? -1 : 1
      if (a.changed !== b.changed) return a.changed ? -1 : 1
      return (a.beforeAcc ?? 0) - (b.beforeAcc ?? 0)
    })
  }, [round, nextRound])

  if (!round || rows.length === 0) {
    return (
      <div className="border-b border-white/5 bg-[#1b1b20] px-4 py-2 text-xs text-gray-500">
        字段优化对比：请在上方版本中选择一个「round」迭代版本查看（当前版本由初始化/手工编辑产生，无迭代对比）。
      </div>
    )
  }
  const changedCount = rows.filter((r) => r.changed).length
  const visible = onlyChanged ? rows.filter((r) => r.changed) : rows

  return (
    <div className="border-b border-white/5 bg-[#1b1b20]">
      <div className="w-full flex items-center justify-between px-4 py-2">
        <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-2 text-sm text-gray-200 hover:text-white">
          {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          <span className="font-medium">字段优化对比</span>
          <span className="text-xs text-gray-500">
            第 {round.round_num} 轮 · {rows.length} 字段，{changedCount} 个有变化
          </span>
        </button>
        <label className="flex items-center gap-1.5 text-[11px] text-gray-400 cursor-pointer select-none">
          <input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)}
            className="w-3.5 h-3.5 accent-purple-500 cursor-pointer" />
          只看有变化{!nextRound && '（优化后待下一轮确认）'}
        </label>
      </div>

      {open && (
        <div className="max-h-[42vh] overflow-y-auto px-2 pb-2">
          {visible.length === 0 ? (
            <div className="px-3 py-3 text-xs text-gray-500">本轮没有字段发生变化。</div>
          ) : (
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-[#1b1b20]">
              <tr className="text-gray-500 text-left">
                <th className="py-1.5 px-2 font-medium">字段</th>
                <th className="py-1.5 px-2 font-medium">准确率（前 → 后）</th>
                <th className="py-1.5 px-2 font-medium">Δ</th>
                <th className="py-1.5 px-2 font-medium">匹配 GT（前 → 后）</th>
                <th className="py-1.5 px-2 font-medium">状态</th>
                <th className="py-1.5 px-2 w-6"></th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => {
                const isOpen = expandedKey === r.key
                // pair per-sample before/after by sample_doc_id (union of both)
                const beforeBySid = new Map((r.before?.per_sample_results ?? []).map((p) => [p.sample_doc_id, p]))
                const afterBySid = new Map((r.after?.per_sample_results ?? []).map((p) => [p.sample_doc_id, p]))
                const sids = Array.from(new Set([...beforeBySid.keys(), ...afterBySid.keys()]))
                const matchUp = r.afterMatch > r.beforeMatch
                const matchDown = r.afterMatch < r.beforeMatch
                return (
                  <Fragment key={r.key}>
                    <tr
                      onClick={() => setExpandedKey(isOpen ? null : r.key)}
                      className={cn('cursor-pointer border-t border-white/5 hover:bg-white/5',
                        r.regressed && 'bg-amber-500/5')}
                    >
                      <td className="py-1.5 px-2 font-mono text-gray-300">{r.key}</td>
                      <td className="py-1.5 px-2 text-gray-300">
                        {accPct(r.beforeAcc)} <span className="text-gray-600">→</span>{' '}
                        {r.afterAcc != null ? accPct(r.afterAcc) : <span className="text-gray-600">—</span>}
                      </td>
                      <td className={cn('py-1.5 px-2 font-medium',
                        r.delta == null ? 'text-gray-600'
                          : r.delta > 1e-4 ? 'text-emerald-400'
                          : r.delta < -1e-4 ? 'text-red-400' : 'text-gray-500')}>
                        {r.delta == null ? '—' : `${r.delta > 0 ? '+' : ''}${Math.round(r.delta * 100)}%`}
                      </td>
                      <td className={cn('py-1.5 px-2 font-medium',
                        matchUp ? 'text-emerald-400' : matchDown ? 'text-red-400' : 'text-gray-400')}>
                        {r.before ? `${r.beforeMatch}/${r.beforeN}` : '—'}
                        <span className="text-gray-600"> → </span>
                        {r.after ? `${r.afterMatch}/${r.afterN}` : '—'}
                      </td>
                      <td className="py-1.5 px-2">
                        <span className={cn('px-1.5 py-0.5 rounded-full text-[10px] font-medium', r.status.cls)}>
                          {r.status.label}
                        </span>
                      </td>
                      <td className="py-1.5 px-2 text-gray-500">
                        {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-[#141418]">
                        <td colSpan={6} className="px-3 py-2">
                          {r.regressed && r.before?.optimization_suggestion && (
                            <div className="mb-2 text-[11px] text-amber-300/90">⚠ {r.before.optimization_suggestion}</div>
                          )}
                          <table className="text-[11px] border-collapse">
                            <thead>
                              <tr className="text-gray-500 text-left">
                                <th className="py-1 pr-4 font-medium">样本</th>
                                <th className="py-1 pr-4 font-medium">正确值 (GT)</th>
                                <th className="py-1 pr-4 font-medium">优化前</th>
                                <th className="py-1 pr-4 font-medium">优化后</th>
                              </tr>
                            </thead>
                            <tbody>
                              {sids.map((sid) => {
                                const bp = beforeBySid.get(sid)
                                const ap = afterBySid.get(sid)
                                const gt = bp?.ground_truth ?? ap?.ground_truth
                                return (
                                  <tr key={sid} className="border-t border-white/5 align-top">
                                    <td className="py-1 pr-4 text-gray-500 font-mono">{sid.slice(0, 6)}</td>
                                    <td className="py-1 pr-4 text-emerald-300 font-mono break-all max-w-[220px]">{_val(gt)}</td>
                                    <td className="py-1 pr-4 font-mono break-all max-w-[220px]">
                                      {bp ? (
                                        <span className={bp.matched ? 'text-emerald-300' : 'text-red-300'}>
                                          {bp.matched ? '✓ ' : '✗ '}{_val(bp.ocr_sliced)}
                                        </span>
                                      ) : <span className="text-gray-600">—</span>}
                                    </td>
                                    <td className="py-1 pr-4 font-mono break-all max-w-[220px]">
                                      {ap ? (
                                        <span className={ap.matched ? 'text-emerald-300' : 'text-red-300'}>
                                          {ap.matched ? '✓ ' : '✗ '}{_val(ap.ocr_sliced)}
                                        </span>
                                      ) : <span className="text-gray-600">—（待确认）</span>}
                                    </td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                          {r.before?.new_ocr_prompt && (
                            <details className="mt-2">
                              <summary className="text-[11px] text-purple-300 cursor-pointer">查看本轮生成的新 prompt</summary>
                              <pre className="mt-1 text-[10px] text-gray-400 whitespace-pre-wrap bg-black/30 rounded p-2 max-h-40 overflow-y-auto">{r.before.new_ocr_prompt}</pre>
                            </details>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main ────────────────────────────────────────────────────────────────────

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

function VersionChips({
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

function RunStatusBar({
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

function ModuleList({
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

function ModuleDetail({
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

function Collapsible({
  title,
  children,
  defaultOpen = false,
}: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-white/5 rounded-md bg-[#1a1a1f] overflow-hidden">
      <button
        onClick={() => setOpen((p) => !p)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-gray-200 hover:bg-white/5"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        {title}
      </button>
      {open && <div className="px-3 py-2 border-t border-white/5">{children}</div>}
    </div>
  )
}

function EmptyPhase({ label }: { label: string }) {
  return <div className="text-[11px] text-gray-500 italic">{label}</div>
}

// ──────────────────────────────────────────────────────────────────────────────
// Finalize modal
// ──────────────────────────────────────────────────────────────────────────────

function FinalizeModal({
  versions,
  defaultId,
  finalizing,
  onCancel,
  onConfirm,
}: {
  versions: VersionSummary[]
  defaultId: string | null
  finalizing: boolean
  onCancel: () => void
  onConfirm: (versionId: string) => void
}) {
  const [picked, setPicked] = useState<string | null>(defaultId)

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="w-[520px] bg-[#1e1e24] border border-white/10 rounded-xl shadow-2xl overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
          <span className="text-sm font-medium">选择要激活的版本</span>
          <button onClick={onCancel} className="text-gray-400 hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="max-h-[400px] overflow-y-auto px-2 py-3 space-y-1">
          {versions.map((v) => {
            const oi = originLabel(v.origin)
            return (
              <label
                key={v.id}
                className={cn(
                  'flex items-center gap-2 px-3 py-2 rounded-md cursor-pointer text-xs',
                  v.id === picked
                    ? 'bg-purple-500/15 ring-1 ring-purple-500/40'
                    : 'hover:bg-white/5',
                )}
              >
                <input
                  type="radio"
                  className="accent-purple-500"
                  checked={picked === v.id}
                  onChange={() => setPicked(v.id)}
                />
                <span className={cn('px-1.5 py-0.5 rounded border text-[10px]', oi.color)}>
                  v{v.version} · {oi.label}
                </span>
                <span className="text-gray-400">{accPct(v.overall_accuracy)}</span>
                {v.status === 'active' && (
                  <span className="text-[10px] text-emerald-400">★ 当前 active</span>
                )}
                <span className="ml-auto text-[10px] text-gray-500">
                  {new Date(v.created_at).toLocaleString()}
                </span>
              </label>
            )
          })}
        </div>
        <div className="px-4 py-3 border-t border-white/10 flex items-center justify-between text-[11px] text-gray-400">
          <span>选中的版本将设为 active, 老 active 版本归档</span>
          <div className="flex gap-2">
            <button
              onClick={onCancel}
              className="px-3 py-1.5 text-xs text-gray-300 hover:text-white"
            >
              取消
            </button>
            <button
              onClick={() => picked && onConfirm(picked)}
              disabled={!picked || finalizing}
              className="px-3 py-1.5 text-xs rounded-md bg-emerald-600/80 hover:bg-emerald-600 text-white disabled:opacity-50 flex items-center gap-1.5"
            >
              {finalizing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Check className="w-3.5 h-3.5" />
              )}
              确定激活
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
