import { useEffect, useState } from 'react'
import { X, Loader2 } from 'lucide-react'
import { fetchTemplates, subscribeTemplate } from '../../lib/api-client'
import { toast } from '../../lib/toast'

interface Template {
  id: string
  name: string
  description: string
  country: string
  language: string
}

const COUNTRY_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'CN', label: 'CN' },
  { value: 'US', label: 'US' },
  { value: 'EU', label: 'EU' },
  { value: 'GLOBAL', label: 'GLOBAL' },
]

const LANGUAGE_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'zh', label: 'zh' },
  { value: 'en', label: 'en' },
  { value: 'multi', label: 'multi' },
]

const countryEmoji: Record<string, string> = {
  CN: '\uD83C\uDDE8\uD83C\uDDF3',
  US: '\uD83C\uDDFA\uD83C\uDDF8',
  EU: '\uD83C\uDDEA\uD83C\uDDFA',
  GLOBAL: '\uD83C\uDF0D',
}

interface Props {
  isOpen: boolean
  onClose: () => void
  onSubscribed?: () => void
}

export default function TemplateBrowserModal({ isOpen, onClose, onSubscribed }: Props) {
  const [templates, setTemplates] = useState<Template[]>([])
  const [loading, setLoading] = useState(false)
  const [subscribing, setSubscribing] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [country, setCountry] = useState('')
  const [language, setLanguage] = useState('')

  useEffect(() => {
    if (!isOpen) return
    setSelected(new Set())
    loadTemplates()
  }, [isOpen, country, language])

  async function loadTemplates() {
    setLoading(true)
    try {
      const params: Record<string, string> = {}
      if (country) params.country = country
      if (language) params.language = language
      const { data } = await fetchTemplates(params)
      setTemplates(Array.isArray(data) ? data : [])
    } catch {
      toast.error('加载模板失败')
      setTemplates([])
    } finally {
      setLoading(false)
    }
  }

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  async function handleSubscribe() {
    if (selected.size === 0) return
    setSubscribing(true)
    try {
      const promises = Array.from(selected).map((id) => subscribeTemplate(id))
      await Promise.all(promises)
      toast.success(`成功订阅 ${selected.size} 个模板`)
      onSubscribed?.()
      onClose()
    } catch {
      toast.error('订阅失败，请重试')
    } finally {
      setSubscribing(false)
    }
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">订阅公开 API 模板</h2>
          <button
            onClick={onClose}
            className="p-2 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Filter bar */}
        <div className="flex items-center gap-4 px-6 py-3 border-b border-gray-100">
          <label className="flex items-center gap-2 text-sm text-gray-600">
            Country
            <select
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
            >
              {COUNTRY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-600">
            Language
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
        </div>

        {/* Template grid */}
        <div className="flex-1 overflow-auto px-6 py-4">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
            </div>
          ) : templates.length === 0 ? (
            <div className="text-center py-20 text-gray-400">
              暂无可用模板
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-4">
              {templates.map((tpl) => (
                <div
                  key={tpl.id}
                  onClick={() => toggleSelect(tpl.id)}
                  className={[
                    'relative p-4 rounded-xl border cursor-pointer transition-all',
                    selected.has(tpl.id)
                      ? 'border-indigo-400 bg-indigo-50 shadow-sm'
                      : 'border-gray-200 hover:border-gray-300 hover:shadow-sm',
                  ].join(' ')}
                >
                  {/* Checkbox */}
                  <div className="absolute top-3 right-3">
                    <div
                      className={[
                        'w-5 h-5 rounded border-2 flex items-center justify-center transition-colors',
                        selected.has(tpl.id)
                          ? 'bg-indigo-600 border-indigo-600'
                          : 'border-gray-300',
                      ].join(' ')}
                    >
                      {selected.has(tpl.id) && (
                        <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                    </div>
                  </div>

                  <h3 className="text-sm font-semibold text-gray-900 pr-8">{tpl.name}</h3>
                  <div className="flex items-center gap-2 mt-2">
                    <span className="text-base">{countryEmoji[tpl.country] ?? tpl.country}</span>
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
                      {tpl.language}
                    </span>
                  </div>
                  {tpl.description && (
                    <p className="text-xs text-gray-500 mt-2 line-clamp-2">{tpl.description}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Bottom bar */}
        <div className="flex items-center justify-end px-6 py-4 border-t border-gray-200">
          <button
            onClick={handleSubscribe}
            disabled={selected.size === 0 || subscribing}
            className={[
              'inline-flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium text-white transition-colors',
              selected.size === 0
                ? 'bg-gray-300 cursor-not-allowed'
                : 'bg-indigo-600 hover:bg-indigo-700',
            ].join(' ')}
          >
            {subscribing && <Loader2 className="w-4 h-4 animate-spin" />}
            订阅 {selected.size} 个模板
          </button>
        </div>
      </div>
    </div>
  )
}
