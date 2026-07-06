// L2.2：OcrOptimizer 拆分——只读详情面板（激活版本 / 版本 / 模块 / Run / 轮次）。
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { JsonBlock, Field } from './primitives'
import {
  fmtPct, fmtDate, statusBadgeCls,
  type ModuleResp, type VersionDetail, type RoundDetail, type RunDetail,
} from './helpers'

export function ActiveVersionPanel({ detail }: { detail: VersionDetail }) {
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

export function VersionDetailPanel({
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

export function ModuleDetailPanel({ m }: { m: ModuleResp }) {
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

export function RunDetailPanel({
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

export function RoundDetailPanel({
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
