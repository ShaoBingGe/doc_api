// L2.2：OcrOptimizer 拆分——共享类型（镜像后端 pydantic schema）+ 纯 helper。

export interface ApiDef {
  id: string
  api_code: string
  name: string
  status: string
}

export interface ModuleResp {
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

export interface VersionSummary {
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

export interface VersionDetail extends VersionSummary {
  api_definition_id: string
  composed_prompt: string
  composed_schema: Record<string, unknown> | null
  notes: string | null
  modules: ModuleResp[]
}

export interface RunSummary {
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

export interface RoundSummary {
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

export interface IterationResp {
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

export interface RoundDetail extends RoundSummary {
  per_sample_accuracy: Record<string, unknown> | null
  ocr_raw_outputs: Record<string, unknown> | null
  iterations: IterationResp[]
}

export interface RunDetail extends RunSummary {
  rounds: RoundSummary[]
}

// ── Small helpers ────────────────────────────────────────────────────────────

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function statusBadgeCls(status: string): string {
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

