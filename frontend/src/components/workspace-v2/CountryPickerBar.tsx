import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import {
  fetchCountryTemplates,
  initFromCountryTemplate,
  type CountryTemplateInfo,
} from '../../lib/api-client'
import { toast } from '../../lib/toast'

const CHIP_ORDER = ['MY', 'CN', 'US', 'EU', 'GLOBAL']

const COUNTRY_LABEL: Record<string, string> = {
  MY: '🇲🇾 MY',
  CN: '🇨🇳 CN',
  US: '🇺🇸 US',
  EU: '🇪🇺 EU',
  GLOBAL: '🌏 GLOBAL',
}

interface Props {
  onPicked: (apiDefinitionId: string) => void
}

export default function CountryPickerBar({ onPicked }: Props) {
  const [chips, setChips] = useState<CountryTemplateInfo[]>(
    CHIP_ORDER.map((c) => ({ country: c, available: false })),
  )
  const [loading, setLoading] = useState(true)
  const [activeCountry, setActiveCountry] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const { data } = await fetchCountryTemplates()
        if (cancelled) return
        // Preserve hardcoded order regardless of backend response order
        const byCountry = new Map(data.map((c) => [c.country.toUpperCase(), c.available]))
        setChips(
          CHIP_ORDER.map((c) => ({ country: c, available: !!byCountry.get(c) })),
        )
      } catch {
        if (!cancelled) toast.error('加载国家模板列表失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const handlePick = async (country: string) => {
    if (activeCountry) return // already in-flight
    setActiveCountry(country)
    try {
      const { data } = await initFromCountryTemplate(country)
      toast.success(`已基于 ${country} 模板创建占位 API；请上传第一张样本`)
      onPicked(data.api_definition_id)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof msg === 'string' ? msg : `${country} 模板创建失败`)
      setActiveCountry(null)
    }
  }

  return (
    <div className="border-b border-white/10 bg-[#1e1e24] px-6 py-4">
      <div className="flex items-center gap-3">
        <span className="text-xs text-gray-400 font-medium">选国家：</span>
        {chips.map((chip) => {
          const isLoading = activeCountry === chip.country
          const disabled = loading || !chip.available || !!activeCountry
          return (
            <button
              key={chip.country}
              onClick={() => chip.available && handlePick(chip.country)}
              disabled={disabled}
              title={chip.available ? `使用 ${chip.country} 预设模板` : '该国家模板尚未提供'}
              className={[
                'inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium transition-all',
                chip.available
                  ? 'bg-purple-600/20 text-purple-200 border border-purple-500/40 hover:bg-purple-600/30'
                  : 'bg-white/5 text-gray-500 border border-white/10 cursor-not-allowed',
                isLoading ? 'opacity-60' : '',
              ].join(' ')}
            >
              {isLoading && <Loader2 className="w-3 h-3 animate-spin" />}
              <span>{COUNTRY_LABEL[chip.country] ?? chip.country}</span>
              {!chip.available && (
                <span className="text-[10px] uppercase tracking-wide text-gray-500">
                  Coming Soon
                </span>
              )}
            </button>
          )
        })}
      </div>
      <p className="mt-2 text-xs text-gray-500">
        请选择一个国家以使用预设模板。下方上传 UI 将在选中后启用。
      </p>
    </div>
  )
}
