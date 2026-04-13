import { useEffect, useState, useMemo } from 'react'
import { Activity, TrendingUp, CheckCircle, Clock, Loader2 } from 'lucide-react'
import { fetchUsageStats } from '../../lib/api-client'
import { toast } from '../../lib/toast'

type RangeOption = 'today' | '7d' | '30d'

interface UsageStats {
  calls_today: number
  calls_this_month: number
  success_rate: number
  avg_latency_ms: number
  calls_by_day: { date: string; count: number }[]
  top_apis: { api_code: string; total_calls: number; success_rate: number }[]
}

const RANGE_LABELS: Record<RangeOption, string> = {
  today: '今日',
  '7d': '7天',
  '30d': '30天',
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

function KpiCard({
  icon,
  label,
  value,
  bg,
}: {
  icon: React.ReactNode
  label: string
  value: string
  bg: string
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-2">
        <div className={`w-7 h-7 ${bg} rounded-lg flex items-center justify-center`}>
          {icon}
        </div>
        <span className="text-xs text-gray-500">{label}</span>
      </div>
      <p className="text-xl font-semibold text-gray-900">{value}</p>
    </div>
  )
}

function CallsChart({ data }: { data: { date: string; count: number }[] }) {
  const chartHeight = 200
  const chartWidth = 600
  const paddingTop = 10
  const paddingBottom = 30
  const paddingLeft = 45
  const paddingRight = 10

  const { points, yTicks } = useMemo(() => {
    if (!data || data.length === 0) {
      return { points: [], yTicks: [] }
    }

    const counts = data.map((d) => d.count)
    const rawMax = Math.max(...counts, 1)
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawMax)))
    const niceCeil = Math.ceil(rawMax / magnitude) * magnitude
    const computedMax = Math.max(niceCeil, 1)

    const innerW = chartWidth - paddingLeft - paddingRight
    const innerH = chartHeight - paddingTop - paddingBottom

    const pts = data.map((d, i) => ({
      x: paddingLeft + (data.length > 1 ? (i / (data.length - 1)) * innerW : innerW / 2),
      y: paddingTop + innerH - (d.count / computedMax) * innerH,
      date: d.date,
      count: d.count,
    }))

    const tickCount = 4
    const ticks = Array.from({ length: tickCount + 1 }, (_, i) => {
      const val = (computedMax / tickCount) * i
      return {
        value: Math.round(val),
        y: paddingTop + innerH - (val / computedMax) * innerH,
      }
    })

    return { points: pts, yTicks: ticks }
  }, [data])

  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-[200px] text-sm text-gray-400">
        暂无数据
      </div>
    )
  }

  const polylinePoints = points.map((p) => `${p.x},${p.y}`).join(' ')
  const areaPoints = [
    `${points[0].x},${chartHeight - paddingBottom}`,
    ...points.map((p) => `${p.x},${p.y}`),
    `${points[points.length - 1].x},${chartHeight - paddingBottom}`,
  ].join(' ')

  return (
    <svg
      viewBox={`0 0 ${chartWidth} ${chartHeight}`}
      className="w-full"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#6366f1" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#6366f1" stopOpacity="0.02" />
        </linearGradient>
      </defs>

      {/* Y axis grid lines + labels */}
      {yTicks.map((tick) => (
        <g key={tick.value}>
          <line
            x1={paddingLeft}
            y1={tick.y}
            x2={chartWidth - paddingRight}
            y2={tick.y}
            stroke="#e5e7eb"
            strokeWidth="1"
            strokeDasharray={tick.value === 0 ? '0' : '4 4'}
          />
          <text
            x={paddingLeft - 8}
            y={tick.y + 4}
            textAnchor="end"
            className="text-[10px] fill-gray-400"
          >
            {tick.value}
          </text>
        </g>
      ))}

      {/* Area fill */}
      <polygon points={areaPoints} fill="url(#areaGradient)" />

      {/* Line */}
      <polyline
        points={polylinePoints}
        fill="none"
        stroke="#6366f1"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {/* Data points */}
      {points.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r="3" fill="#6366f1" stroke="white" strokeWidth="2" />
      ))}

      {/* X axis labels */}
      {points.map((p, i) => {
        if (data.length > 7 && i % 2 !== 0 && i !== data.length - 1) return null
        const label = p.date.length >= 10 ? p.date.slice(5) : p.date
        return (
          <text
            key={i}
            x={p.x}
            y={chartHeight - 8}
            textAnchor="middle"
            className="text-[10px] fill-gray-400"
          >
            {label}
          </text>
        )
      })}
    </svg>
  )
}

export default function TrafficMonitoring() {
  const [range, setRange] = useState<RangeOption>('7d')
  const [stats, setStats] = useState<UsageStats | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setIsLoading(true)
    fetchUsageStats(range)
      .then((res) => {
        if (!cancelled) {
          setStats(res.data)
          setIsLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          toast.error('加载流量数据失败')
          setIsLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [range])

  const isEmpty = !stats || (
    stats.calls_today === 0 &&
    stats.calls_this_month === 0 &&
    stats.calls_by_day.every((d) => d.count === 0)
  )

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">流量监控</h1>
          <p className="text-sm text-gray-500 mt-1">实时查看 API 调用情况与性能指标</p>
        </div>
        {/* Range selector */}
        <div className="flex items-center bg-gray-100 rounded-lg p-0.5">
          {(Object.keys(RANGE_LABELS) as RangeOption[]).map((opt) => (
            <button
              key={opt}
              onClick={() => setRange(opt)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                range === opt
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {RANGE_LABELS[opt]}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-6 h-6 text-gray-400 animate-spin" />
        </div>
      ) : isEmpty ? (
        <div className="bg-white border border-gray-200 rounded-xl py-20 text-center">
          <Activity className="w-10 h-10 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-400">暂无调用数据</p>
        </div>
      ) : (
        <>
          {/* KPI Cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <KpiCard
              icon={<Activity className="w-4 h-4 text-indigo-500" />}
              label="今日调用"
              value={formatNumber(stats!.calls_today)}
              bg="bg-indigo-50"
            />
            <KpiCard
              icon={<TrendingUp className="w-4 h-4 text-blue-500" />}
              label="本月调用"
              value={formatNumber(stats!.calls_this_month)}
              bg="bg-blue-50"
            />
            <KpiCard
              icon={<CheckCircle className="w-4 h-4 text-green-500" />}
              label="成功率"
              value={`${stats!.success_rate.toFixed(1)}%`}
              bg="bg-green-50"
            />
            <KpiCard
              icon={<Clock className="w-4 h-4 text-amber-500" />}
              label="平均延迟"
              value={`${Math.round(stats!.avg_latency_ms)}ms`}
              bg="bg-amber-50"
            />
          </div>

          {/* Line Chart */}
          <div className="bg-white border border-gray-200 rounded-xl p-6">
            <h2 className="text-sm font-semibold text-gray-900 mb-4">调用趋势</h2>
            <CallsChart data={stats!.calls_by_day} />
          </div>

          {/* Top APIs table */}
          {stats!.top_apis && stats!.top_apis.length > 0 && (
            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-100">
                <h2 className="text-sm font-semibold text-gray-900">Top API</h2>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-gray-200 bg-gray-50">
                      <th className="text-left text-xs font-medium text-gray-500 px-6 py-3">API Code</th>
                      <th className="text-left text-xs font-medium text-gray-500 py-3">总调用</th>
                      <th className="text-left text-xs font-medium text-gray-500 py-3">成功率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats!.top_apis.map((api) => (
                      <tr key={api.api_code} className="border-b border-gray-100 last:border-0">
                        <td className="px-6 py-3">
                          <code className="text-sm font-mono text-gray-900">{api.api_code}</code>
                        </td>
                        <td className="py-3 text-sm text-gray-700">
                          {formatNumber(api.total_calls)}
                        </td>
                        <td className="py-3">
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                              api.success_rate >= 99
                                ? 'bg-green-50 text-green-700'
                                : api.success_rate >= 95
                                  ? 'bg-yellow-50 text-yellow-700'
                                  : 'bg-red-50 text-red-700'
                            }`}
                          >
                            {api.success_rate.toFixed(1)}%
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
