import { CreditCard, Check, Zap, Building2, FileText } from 'lucide-react'

function PlanCard({
  name,
  price,
  period,
  calls,
  icon,
  iconBg,
  features,
  current,
  badge,
  highlighted,
}: {
  name: string
  price: string
  period: string
  calls: string
  icon: React.ReactNode
  iconBg: string
  features: string[]
  current?: boolean
  badge?: string
  highlighted?: boolean
}) {
  return (
    <div
      className={`relative border rounded-xl p-5 flex flex-col ${
        highlighted
          ? 'border-indigo-200 bg-indigo-50/30 ring-1 ring-indigo-100'
          : 'border-gray-200 bg-white'
      }`}
    >
      {badge && (
        <div className="absolute -top-2.5 right-4">
          <span
            className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
              badge === 'Coming soon'
                ? 'bg-amber-100 text-amber-700'
                : 'bg-purple-100 text-purple-700'
            }`}
          >
            {badge}
          </span>
        </div>
      )}

      <div className="flex items-center gap-3 mb-4">
        <div className={`w-9 h-9 ${iconBg} rounded-lg flex items-center justify-center`}>
          {icon}
        </div>
        <div>
          <p className="text-sm font-semibold text-gray-900">{name}</p>
          <p className="text-xs text-gray-500">{calls}</p>
        </div>
      </div>

      <div className="mb-4">
        <span className="text-2xl font-bold text-gray-900">{price}</span>
        {period && <span className="text-sm text-gray-500">{period}</span>}
      </div>

      <ul className="space-y-2 flex-1 mb-4">
        {features.map((f) => (
          <li key={f} className="flex items-start gap-2 text-xs text-gray-600">
            <Check className="w-3.5 h-3.5 text-green-500 mt-0.5 flex-shrink-0" />
            <span>{f}</span>
          </li>
        ))}
      </ul>

      {current ? (
        <button
          disabled
          className="w-full py-2 text-sm font-medium text-gray-500 bg-gray-100 rounded-lg cursor-not-allowed"
        >
          当前套餐
        </button>
      ) : (
        <button
          disabled
          className={`w-full py-2 text-sm font-medium rounded-lg cursor-not-allowed ${
            highlighted
              ? 'bg-indigo-200 text-indigo-400'
              : 'bg-gray-100 text-gray-400'
          }`}
        >
          {badge === '联系我们' ? '联系我们' : '即将推出'}
        </button>
      )}
    </div>
  )
}

export default function Billing() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-gray-900">账单与续费</h1>
        <p className="text-sm text-gray-500 mt-1">管理您的订阅套餐与账单信息</p>
      </div>

      {/* Current plan card */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-900">当前套餐</h2>
        </div>
        <div className="p-6 space-y-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-50 rounded-xl flex items-center justify-center">
              <CreditCard className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-gray-900">Free</span>
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700">
                  当前
                </span>
              </div>
              <p className="text-xs text-gray-500 mt-0.5">免费套餐</p>
            </div>
          </div>

          {/* Usage progress */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs text-gray-500">本月已用</span>
              <span className="text-xs text-gray-700 font-medium">0 / 1,000 次</span>
            </div>
            <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-indigo-500 rounded-full transition-all"
                style={{ width: '0%' }}
              />
            </div>
          </div>

          {/* Next billing */}
          <div className="flex items-center justify-between pt-2 border-t border-gray-100">
            <span className="text-xs text-gray-500">下次计费</span>
            <span className="text-sm text-gray-400">--</span>
          </div>
        </div>
      </div>

      {/* Upgrade plans */}
      <div>
        <h2 className="text-sm font-semibold text-gray-900 mb-4">升级套餐</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <PlanCard
            name="Free"
            price="$0"
            period="/月"
            calls="1,000 次/月"
            icon={<Zap className="w-5 h-5 text-gray-600" />}
            iconBg="bg-gray-50"
            features={[
              '基础 API 调用',
              '1 个 API Key',
              '社区支持',
              '基础监控',
            ]}
            current
          />

          <PlanCard
            name="Pro"
            price="$49"
            period="/月"
            calls="50,000 次/月"
            icon={<Zap className="w-5 h-5 text-indigo-600" />}
            iconBg="bg-indigo-50"
            features={[
              '高级 API 调用',
              '无限 API Key',
              '优先支持',
              '高级监控与分析',
              '自定义速率限制',
            ]}
            badge="Coming soon"
            highlighted
          />

          <PlanCard
            name="Enterprise"
            price="Custom"
            period=""
            calls="无限制"
            icon={<Building2 className="w-5 h-5 text-purple-600" />}
            iconBg="bg-purple-50"
            features={[
              '全部 Pro 功能',
              'SLA 保障',
              '专属客户经理',
              '私有化部署支持',
              '定制开发',
            ]}
            badge="联系我们"
          />
        </div>
      </div>

      {/* Billing history */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-900">账单历史</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className="text-left text-xs font-medium text-gray-500 px-6 py-3">日期</th>
                <th className="text-left text-xs font-medium text-gray-500 py-3">金额</th>
                <th className="text-left text-xs font-medium text-gray-500 py-3">状态</th>
                <th className="text-right text-xs font-medium text-gray-500 px-6 py-3">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={4} className="py-12 text-center">
                  <FileText className="w-8 h-8 text-gray-300 mx-auto mb-3" />
                  <p className="text-sm text-gray-400">暂无账单记录</p>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
