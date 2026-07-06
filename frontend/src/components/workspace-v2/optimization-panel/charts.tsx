// L2.1：OptimizationProcessPanel 拆分——图表组件（字段准确率热力图 +
// 字段 diff 对照）。纯展示 + 自取数（fetchFieldAccuracy）。
import { Fragment, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '../../../lib/utils'
import { fetchFieldAccuracy } from '../../../lib/api-client'
import {
  accPct, accCellCls, _matchStats, _val,
  type RoundDetail, type DiffRow, type FieldAccuracyData,
} from './helpers'

export function FieldAccuracyHeatmap({
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

export function FieldDiffComparison({
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

