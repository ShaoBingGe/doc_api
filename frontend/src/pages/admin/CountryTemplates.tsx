import { useEffect, useMemo, useState } from 'react'
import { Loader2, RefreshCw, FileText, AlertTriangle, CheckCircle2 } from 'lucide-react'
import apiClient, {
  fetchPlatformCountryTemplates,
  fetchGoldenSeeds,
  fetchGoldenEvaluation,
  evaluateGolden,
  goldenFileUrl,
  type CountryKind,
  type GoldenSeed,
  type GoldenEvaluation,
} from '../../lib/api-client'
import { toast } from '../../lib/toast'

// Render any GT / OCR value compactly: unwrap single-element arrays, stringify objects.
function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return '∅'
  if (Array.isArray(v)) {
    if (v.length === 1) return fmtVal(v[0])
    return v.map(fmtVal).join(' | ')
  }
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export default function CountryTemplates() {
  const [templates, setTemplates] = useState<CountryKind[]>([])
  const [country, setCountry] = useState<string>('')
  const [seeds, setSeeds] = useState<GoldenSeed[]>([])
  const [evaluation, setEvaluation] = useState<GoldenEvaluation | null>(null)
  const [loading, setLoading] = useState(true)
  const [evaluating, setEvaluating] = useState(false)

  // load country list once
  useEffect(() => {
    fetchPlatformCountryTemplates()
      .then(({ data }) => {
        setTemplates(data)
        const firstAvail = data.find((t) => t.available)
        if (firstAvail) setCountry(firstAvail.country)
      })
      .catch(() => toast.error('加载国家模板失败'))
      .finally(() => setLoading(false))
  }, [])

  // load seeds + cached evaluation when country changes
  useEffect(() => {
    if (!country) return
    setLoading(true)
    Promise.all([fetchGoldenSeeds(country), fetchGoldenEvaluation(country)])
      .then(([s, e]) => {
        setSeeds(s.data.seeds)
        setEvaluation(e.data?.per_seed ? e.data : null)
      })
      .catch(() => toast.error('加载黄金种子失败'))
      .finally(() => setLoading(false))
  }, [country])

  async function runEvaluate() {
    if (!country || evaluating) return
    setEvaluating(true)
    toast.info('正在用当前国家模板对黄金集跑 OCR，请稍候…')
    try {
      const { data } = await evaluateGolden(country)
      if (data.error) {
        toast.error(`评测失败：${data.error}${data.detail ? ' · ' + data.detail : ''}`)
      } else {
        toast.success(
          `评测完成：${data.summary?.seeds ?? 0} 篇，冲突 ${data.summary?.conflicts ?? 0}，OCR 失败 ${data.summary?.ocr_errors ?? 0}`,
        )
      }
      setEvaluation(data?.per_seed ? data : null)
    } catch {
      toast.error('评测请求失败（Gemini 可能不可用）')
    } finally {
      setEvaluating(false)
    }
  }

  const availableCountries = useMemo(() => templates.filter((t) => t.available), [templates])

  return (
    <div className="max-w-6xl mx-auto space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">国家模板 / 黄金种子</h1>
          <p className="text-sm text-gray-500 mt-1">
            审阅各国模板的黄金种子样本与人工复核数据；可用当前模板重新评测，标识与标注的冲突。
          </p>
        </div>
        <button
          onClick={runEvaluate}
          disabled={!country || evaluating}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 transition-colors disabled:opacity-60"
        >
          {evaluating ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          {evaluating ? '评测中…' : '重新评测'}
        </button>
      </div>

      {/* country + kind selector */}
      <div className="flex items-center gap-2 flex-wrap">
        {availableCountries.map((t) => (
          <button
            key={t.country}
            onClick={() => setCountry(t.country)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
              country === t.country
                ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                : 'border-gray-200 text-gray-600 hover:bg-gray-50'
            }`}
          >
            {t.country}
            <span className="ml-1.5 text-xs text-gray-400">{t.kinds.join('/')}</span>
          </button>
        ))}
        {availableCountries.length === 0 && !loading && (
          <span className="text-sm text-gray-400">暂无可用国家模板</span>
        )}
      </div>

      {evaluation?.summary && (
        <div className="text-xs text-gray-500 flex items-center gap-4 bg-gray-50 border border-gray-100 rounded-lg px-4 py-2">
          <span>最近评测：{evaluation.generated_at?.replace('T', ' ').slice(0, 19) ?? '—'}</span>
          <span>种子 {evaluation.summary.seeds}</span>
          <span className="text-amber-600">冲突字段 {evaluation.summary.conflicts}</span>
          <span>OCR 失败 {evaluation.summary.ocr_errors}</span>
          <span>整体准确率 {(evaluation.summary.overall_accuracy * 100).toFixed(1)}%</span>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-gray-400 py-12 text-center">加载中…</p>
      ) : seeds.length === 0 ? (
        <div className="border border-dashed border-gray-200 rounded-xl py-16 text-center text-sm text-gray-400">
          该国家暂无黄金种子
        </div>
      ) : (
        <div className="space-y-5">
          {seeds.map((seed) => (
            <SeedRow
              key={seed.seed_id}
              country={country}
              seed={seed}
              evalData={evaluation?.per_seed?.[seed.seed_id] ?? null}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SeedRow({
  country,
  seed,
  evalData,
}: {
  country: string
  seed: GoldenSeed
  evalData: GoldenEvaluation['per_seed'][string] | null
}) {
  const conflicts = evalData?.fields.filter((f) => f.conflict) ?? []
  const hasEval = !!evalData
  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className="px-4 py-2.5 border-b border-gray-100 flex items-center justify-between">
        <div className="flex items-center gap-2 min-w-0">
          <FileText className="w-4 h-4 text-gray-400 flex-shrink-0" />
          <span className="text-sm text-gray-700 truncate" title={seed.filename}>
            {seed.filename}
          </span>
        </div>
        {hasEval ? (
          conflicts.length > 0 ? (
            <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-600">
              <AlertTriangle className="w-3.5 h-3.5" />
              {conflicts.length} 处冲突
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-xs font-medium text-green-600">
              <CheckCircle2 className="w-3.5 h-3.5" />
              与标注一致
            </span>
          )
        ) : (
          <span className="text-xs text-gray-400">尚未评测</span>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-0">
        {/* 样本 左 */}
        <div className="border-r border-gray-100 bg-gray-50">
          <PdfPane country={country} seedId={seed.seed_id} />
        </div>
        {/* 数据集 右 */}
        <div className="p-3">
          {hasEval ? (
            <DatasetFromEval fields={evalData!.fields} ocrError={evalData!.ocr_error} />
          ) : (
            <DatasetFromGt gt={seed.gt} />
          )}
        </div>
      </div>
    </div>
  )
}

// PDF rendered via authed blob → object URL (the file endpoint needs the JWT).
function PdfPane({ country, seedId }: { country: string; seedId: string }) {
  const [url, setUrl] = useState<string | null>(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    let revoked: string | null = null
    let alive = true
    apiClient
      .get(goldenFileUrl(country, seedId), { responseType: 'blob' })
      .then((res) => {
        if (!alive) return
        const obj = URL.createObjectURL(res.data as Blob)
        revoked = obj
        setUrl(obj)
      })
      .catch(() => alive && setErr(true))
    return () => {
      alive = false
      if (revoked) URL.revokeObjectURL(revoked)
    }
  }, [country, seedId])

  if (err) return <div className="h-[420px] flex items-center justify-center text-sm text-gray-400">PDF 加载失败</div>
  if (!url) return <div className="h-[420px] flex items-center justify-center text-sm text-gray-400">加载样本…</div>
  return <iframe src={url} title="sample" className="w-full h-[420px]" />
}

function DatasetFromEval({
  fields,
  ocrError,
}: {
  fields: GoldenEvaluation['per_seed'][string]['fields']
  ocrError: boolean
}) {
  return (
    <div className="space-y-1">
      {ocrError && (
        <div className="text-xs text-red-500 mb-2 flex items-center gap-1">
          <AlertTriangle className="w-3.5 h-3.5" /> 本样本 OCR 失败，下方为标注值
        </div>
      )}
      {fields.map((f, i) => (
        <div
          key={`${f.module_key}-${i}`}
          className={`rounded-md px-2.5 py-1.5 ${
            f.conflict ? 'bg-amber-50 border border-amber-200' : ''
          }`}
        >
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-xs text-gray-500 flex-shrink-0">{f.field}</span>
            {!f.conflict && <span className="text-sm text-gray-900 text-right break-all">{fmtVal(f.gt)}</span>}
          </div>
          {f.conflict && (
            <div className="mt-1 grid grid-cols-2 gap-2 text-sm">
              <div className="min-w-0">
                <div className="text-[10px] text-green-600 uppercase">标注 GT</div>
                <div className="text-green-700 break-all">{fmtVal(f.gt)}</div>
              </div>
              <div className="min-w-0">
                <div className="text-[10px] text-red-500 uppercase">最新模板识别</div>
                <div className="text-red-600 break-all">{fmtVal(f.latest)}</div>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function DatasetFromGt({ gt }: { gt: Record<string, unknown> }) {
  const entries = Object.entries(gt)
  return (
    <div className="space-y-0.5">
      <p className="text-xs text-gray-400 mb-2">人工复核标注数据（尚未评测，无冲突标识）</p>
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-3 px-2.5 py-1">
          <span className="text-xs text-gray-500 flex-shrink-0">{k}</span>
          <span className="text-sm text-gray-900 text-right break-all">{fmtVal(v)}</span>
        </div>
      ))}
    </div>
  )
}
