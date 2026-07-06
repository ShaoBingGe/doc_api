// L2.1：OptimizationProcessPanel 拆分——共享类型 + 纯 helper（无组件，
// 满足 react-refresh only-export-components）。
// EmptyPhase / FinalizeModal）。从单文件 1574 行按分组拆出（B2 打法）。

export interface OptimizationProcessPanelProps {
  apiDefinitionId: string
  reloadKey: number
  optimizing: boolean
}

// ── Types from backend ──────────────────────────────────────────────────────

export interface VersionSummary {
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

export interface OcrModuleResponse {
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

export interface VersionDetail extends VersionSummary {
  composed_prompt: string
  composed_schema: Record<string, unknown> | null
  modules: OcrModuleResponse[]
  notes: string | null
}

export interface IterationResponse {
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

export interface RoundDetail {
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

export interface RunSummary {
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

export function accPct(a: number | null | undefined): string {
  if (a == null) return '—'
  return `${Math.round(a * 100)}%`
}

export function originLabel(origin: string): { label: string; color: string } {
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

export function _val(v: unknown): string {
  if (v === null || v === undefined) return '∅'
  if (Array.isArray(v)) {
    if (v.length === 1) return _val(v[0])
    return JSON.stringify(v)
  }
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export type DiffRow = {
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

export function _matchStats(it: IterationResponse | null): { m: number; n: number } {
  const ps = it?.per_sample_results ?? []
  return { m: ps.filter((p) => p.matched).length, n: ps.length }
}

// ── 字段级准确率收敛看板（每轮 × 每字段热力表） ─────────────────────────────
//
// Lets the customer SEE convergence: rows = fields, columns = rounds, cells =
// per-field accuracy (red→amber→green). The top "总体" row tracks overall
// accuracy per round; a trend column shows last−first delta. Worst fields sort
// to the top so problem fields surface. Data: GET .../runs/{id}/field-accuracy.

export interface FieldAccuracyData {
  run_id: string
  fields: { module_key: string; display_name: string }[]
  rounds: {
    round_num: number
    overall_accuracy: number | null
    phase: string
    fields: Record<string, number>
  }[]
}

export function accCellCls(a: number | null | undefined): string {
  if (a == null) return 'text-gray-600'
  if (a >= 0.999) return 'bg-emerald-500/20 text-emerald-300'
  if (a >= 0.8) return 'bg-amber-500/15 text-amber-300'
  if (a >= 0.5) return 'bg-orange-500/15 text-orange-300'
  return 'bg-red-500/15 text-red-300'
}

