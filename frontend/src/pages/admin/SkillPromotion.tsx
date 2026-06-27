import { useEffect, useMemo, useState } from 'react'
import {
  Loader2,
  Sparkles,
  Star,
  ChevronDown,
  ChevronRight,
  X,
  ArrowUpCircle,
  RefreshCw,
} from 'lucide-react'
import {
  fetchPlatformCountryTemplates,
  fetchSkillPromotionCandidates,
  draftSkillPromotion,
  promoteSkill,
  type CountryKind,
  type SkillCandidate,
  type SkillDraft,
  type GoldenReference,
} from '../../lib/api-client'
import { toast } from '../../lib/toast'

/** P4 技能晋升 —— 把反思每轮蒸发的 skill_feedback 采收成候选，管理员 LLM 起草 + 编辑确认
 *  后晋升进全局技能库。`recommended`(跨租户>5)是自动推荐徽标，非硬门；管理员可越级晋升。 */
export default function SkillPromotion() {
  const [countries, setCountries] = useState<CountryKind[]>([])
  const [country, setCountry] = useState<string>('')
  const [candidates, setCandidates] = useState<SkillCandidate[]>([])
  const [recommended, setRecommended] = useState(0)
  const [golden, setGolden] = useState<GoldenReference | null>(null)
  const [loading, setLoading] = useState(true)
  const [promoteFor, setPromoteFor] = useState<SkillCandidate | null>(null)

  useEffect(() => {
    fetchPlatformCountryTemplates()
      .then(({ data }) => {
        setCountries(data)
        const first = data.find((t) => t.available)
        if (first) setCountry(first.country)
        else setLoading(false)
      })
      .catch(() => {
        toast.error('加载国家列表失败')
        setLoading(false)
      })
  }, [])

  function load(c: string) {
    setLoading(true)
    fetchSkillPromotionCandidates(c)
      .then(({ data }) => {
        setCandidates(data.candidates)
        setRecommended(data.recommended)
        setGolden(data.golden_reference)
      })
      .catch(() => toast.error('加载晋升候选失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (country) load(country)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [country])

  const availableCountries = useMemo(
    () => countries.filter((t) => t.available),
    [countries],
  )

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">技能晋升候选</h1>
          <p className="text-sm text-gray-500 mt-1 max-w-3xl">
            反思每轮会在 <code className="text-xs bg-gray-100 px-1 rounded">skill_feedback</code> 里指出「某字段缺什么技能」。
            这里把这些建议按 <b>国家 / 字段</b> 聚合成候选，
            <b>跨租户 &gt; 5</b> 自动标「推荐」（非硬门 —— 你可越级晋升任意候选）。
            点「起草并晋升」由模型起草技能正文，你<b>编辑确认</b>后写入全局技能库。
          </p>
        </div>
        <button
          onClick={() => country && load(country)}
          disabled={!country || loading}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-gray-600 border border-gray-200 hover:bg-gray-50 disabled:opacity-60"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {/* country chips */}
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
          </button>
        ))}
      </div>

      {!loading && candidates.length > 0 && (
        <div className="text-xs text-gray-500 bg-gray-50 border border-gray-100 rounded-lg px-4 py-2 flex items-center gap-4 flex-wrap">
          <span>
            {country}：{candidates.length} 个候选，其中 <b className="text-emerald-600">{recommended}</b> 个达「推荐」阈值（跨租户&gt;5）。
          </span>
          {golden && (
            <span className="text-gray-400" title={golden.generated_at ? `评测于 ${golden.generated_at.replace('T', ' ').slice(0, 19)}` : undefined}>
              · golden 参考准确率 <b className="text-indigo-600">{Math.round(golden.overall_accuracy * 100)}%</b>
              {typeof golden.seeds === 'number' ? `（${golden.seeds} 篇）` : ''}（晋升「不回归」参考，不卡）
            </span>
          )}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-gray-400 py-12 text-center">加载中…</p>
      ) : candidates.length === 0 ? (
        <div className="border border-dashed border-gray-200 rounded-xl py-16 text-center text-sm text-gray-400">
          该国家暂无晋升候选（反思尚未产出 skill_feedback，或已全部晋升）
        </div>
      ) : (
        <div className="space-y-3">
          {candidates.map((c) => (
            <CandidateRow
              key={`${c.country}/${c.field}`}
              candidate={c}
              onPromote={() => setPromoteFor(c)}
            />
          ))}
        </div>
      )}

      {promoteFor && (
        <PromoteModal
          candidate={promoteFor}
          onClose={() => setPromoteFor(null)}
          onPromoted={() => {
            setPromoteFor(null)
            toast.success('已晋升进全局技能库')
          }}
        />
      )}
    </div>
  )
}

function CandidateRow({
  candidate: c,
  onPromote,
}: {
  candidate: SkillCandidate
  onPromote: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="bg-white border border-gray-200 rounded-xl">
      <div className="flex items-center justify-between px-4 py-3 gap-3">
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-2 min-w-0 text-left"
        >
          {open ? (
            <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
          ) : (
            <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
          )}
          <span className="font-medium text-gray-900 truncate">{c.field}</span>
          {c.recommended && (
            <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-2 py-0.5 flex-shrink-0">
              <Star className="w-3 h-3" /> 推荐
            </span>
          )}
        </button>
        <div className="flex items-center gap-3 flex-shrink-0">
          <span className="text-xs text-gray-500">
            出现 {c.occurrence_count} · 租户 {c.tenant_count} · API {c.api_count}
          </span>
          <button
            onClick={onPromote}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
          >
            <Sparkles className="w-4 h-4" /> 起草并晋升
          </button>
        </div>
      </div>
      {open && (
        <div className="border-t border-gray-100 px-4 py-3 space-y-1.5">
          <p className="text-xs text-gray-400">反思原文（最多 3 条）：</p>
          {c.sample_feedback.map((s, i) => (
            <p key={i} className="text-xs text-gray-600 bg-gray-50 rounded px-2.5 py-1.5 break-words">
              {s}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

function PromoteModal({
  candidate: c,
  onClose,
  onPromoted,
}: {
  candidate: SkillCandidate
  onClose: () => void
  onPromoted: () => void
}) {
  const [draft, setDraft] = useState<SkillDraft | null>(null)
  const [drafting, setDrafting] = useState(true)
  const [promoting, setPromoting] = useState(false)

  async function runDraft() {
    setDrafting(true)
    try {
      const { data } = await draftSkillPromotion({
        country: c.country,
        field: c.field,
        sample_feedback: c.sample_feedback,
      })
      setDraft(data)
    } catch {
      toast.error('起草失败，请重试')
    } finally {
      setDrafting(false)
    }
  }

  useEffect(() => {
    runDraft()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function confirm() {
    if (!draft || !draft.name.trim() || !draft.content.trim() || promoting) return
    setPromoting(true)
    try {
      await promoteSkill({
        country: c.country,
        field: c.field,
        name: draft.name,
        content: draft.content,
        description: draft.description,
      })
      onPromoted()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof msg === 'string' ? msg : '晋升失败，请重试')
    } finally {
      setPromoting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={promoting ? undefined : onClose}>
      <div className="w-[640px] max-w-full bg-white rounded-xl shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
          <div className="font-medium text-gray-900">
            晋升技能 · <span className="text-indigo-600">{c.country}/{c.field}</span>
          </div>
          {!promoting && (
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        <div className="px-5 py-4 space-y-3">
          {drafting ? (
            <div className="py-10 text-center text-sm text-gray-500">
              <Loader2 className="w-5 h-5 animate-spin inline mr-2" />
              模型起草中…
            </div>
          ) : draft ? (
            <>
              <label className="block">
                <span className="text-xs text-gray-500">技能名</span>
                <input
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  className="mt-1 w-full px-3 py-2 rounded-lg border border-gray-200 text-sm focus:border-indigo-400 focus:outline-none"
                />
              </label>
              <label className="block">
                <span className="text-xs text-gray-500">规则正文（可编辑 —— 这就是写入全局库的内容）</span>
                <textarea
                  value={draft.content}
                  onChange={(e) => setDraft({ ...draft, content: e.target.value })}
                  rows={7}
                  className="mt-1 w-full px-3 py-2 rounded-lg border border-gray-200 text-sm leading-relaxed focus:border-indigo-400 focus:outline-none resize-y"
                />
              </label>
              <label className="block">
                <span className="text-xs text-gray-500">一句话说明</span>
                <input
                  value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  className="mt-1 w-full px-3 py-2 rounded-lg border border-gray-200 text-sm focus:border-indigo-400 focus:outline-none"
                />
              </label>
              <p className="text-xs text-gray-400">
                晋升后写入<b>全局</b>技能库（所有 API 可复用）；需在工作区把它 attach 到对应字段才会生效。
              </p>
            </>
          ) : (
            <div className="py-8 text-center text-sm text-gray-400">起草失败</div>
          )}
        </div>

        <div className="border-t border-gray-100 px-5 py-3 flex justify-between">
          <button
            onClick={runDraft}
            disabled={drafting || promoting}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg text-gray-600 border border-gray-200 hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${drafting ? 'animate-spin' : ''}`} /> 重新起草
          </button>
          <div className="flex gap-2">
            {!promoting && (
              <button onClick={onClose} className="px-3 py-1.5 text-sm rounded-lg text-gray-600 hover:bg-gray-100">
                取消
              </button>
            )}
            <button
              onClick={confirm}
              disabled={drafting || promoting || !draft?.name.trim() || !draft?.content.trim()}
              className="inline-flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg text-white bg-indigo-600 hover:bg-indigo-700 font-medium disabled:opacity-50"
            >
              {promoting ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowUpCircle className="w-4 h-4" />}
              确认晋升入库
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
