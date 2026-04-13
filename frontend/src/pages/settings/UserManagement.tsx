import { User, Mail, Calendar, CreditCard, Lock, Users } from 'lucide-react'

function InfoRow({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex-shrink-0">{icon}</div>
      <div>
        <p className="text-xs text-gray-400">{label}</p>
        <div className="text-sm text-gray-900 mt-0.5">{value}</div>
      </div>
    </div>
  )
}

function ActionCard({
  icon,
  title,
  description,
  disabled,
}: {
  icon: React.ReactNode
  title: string
  description: string
  disabled?: boolean
}) {
  return (
    <div className="relative group">
      <button
        disabled={disabled}
        className={`w-full flex items-center gap-4 p-4 border border-gray-200 rounded-xl text-left transition-colors ${
          disabled
            ? 'cursor-not-allowed opacity-60'
            : 'hover:bg-gray-50 hover:border-gray-300'
        }`}
      >
        <div className="flex-shrink-0">{icon}</div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900">{title}</p>
          <p className="text-xs text-gray-500 mt-0.5">{description}</p>
        </div>
        {disabled && (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500 flex-shrink-0">
            Coming soon
          </span>
        )}
      </button>
      {disabled && (
        <div className="absolute -top-8 left-1/2 -translate-x-1/2 px-2 py-1 bg-gray-800 text-white text-xs rounded-md opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap">
          Coming soon
        </div>
      )}
    </div>
  )
}

export default function UserManagement() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-gray-900">用户管理</h1>
        <p className="text-sm text-gray-500 mt-1">查看账户信息与团队管理</p>
      </div>

      {/* User info card */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-900">个人信息</h2>
        </div>
        <div className="p-6">
          <div className="flex items-start gap-5">
            {/* Avatar */}
            <div className="w-16 h-16 bg-indigo-100 rounded-xl flex items-center justify-center flex-shrink-0">
              <User className="w-8 h-8 text-indigo-600" />
            </div>

            {/* Info grid */}
            <div className="flex-1 grid grid-cols-1 sm:grid-cols-2 gap-4">
              <InfoRow
                icon={<User className="w-4 h-4 text-gray-400" />}
                label="用户名"
                value="admin"
              />
              <InfoRow
                icon={<Mail className="w-4 h-4 text-gray-400" />}
                label="邮箱"
                value="admin@apianything.io"
              />
              <InfoRow
                icon={<Calendar className="w-4 h-4 text-gray-400" />}
                label="注册时间"
                value="2024-01-01"
              />
              <InfoRow
                icon={<CreditCard className="w-4 h-4 text-gray-400" />}
                label="订阅套餐"
                value={
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700">
                    Free Plan
                  </span>
                }
              />
            </div>
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-900">账户操作</h2>
        </div>
        <div className="p-6 space-y-3">
          <ActionCard
            icon={<Lock className="w-5 h-5 text-gray-400" />}
            title="修改密码"
            description="更新您的登录密码"
            disabled
          />
          <ActionCard
            icon={<Users className="w-5 h-5 text-gray-400" />}
            title="管理团队成员"
            description="邀请或移除团队成员"
            disabled
          />
        </div>
      </div>
    </div>
  )
}
