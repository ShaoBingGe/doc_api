import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Mail, KeyRound, Lock, ShieldCheck } from 'lucide-react'
import { useAuthStore } from '../stores/auth-store'
import { toast } from '../lib/toast'

type Tab = 'code' | 'password'

export default function Login() {
  const navigate = useNavigate()
  const loginWithCode = useAuthStore((s) => s.loginWithCode)
  const loginWithPassword = useAuthStore((s) => s.loginWithPassword)

  const [tab, setTab] = useState<Tab>('code')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (submitting) return
    setSubmitting(true)
    try {
      if (tab === 'code') {
        await loginWithCode(email.trim(), code.trim())
      } else {
        await loginWithPassword(email.trim(), password)
      }
      toast.success('登录成功')
      navigate('/', { replace: true })
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error
          ?.message ?? '登录失败，请检查邮箱与凭证'
      toast.error(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-50 via-white to-blue-50 px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-gray-900 tracking-tight">ApiAnything</h1>
          <p className="text-sm text-gray-500 mt-1">用户登录</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          {/* Tabs */}
          <div className="grid grid-cols-2 border-b border-gray-100">
            <button
              onClick={() => setTab('code')}
              className={`py-3 text-sm font-medium transition-colors ${
                tab === 'code'
                  ? 'text-indigo-600 border-b-2 border-indigo-600 bg-indigo-50/40'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              普通用户
            </button>
            <button
              onClick={() => setTab('password')}
              className={`py-3 text-sm font-medium transition-colors ${
                tab === 'password'
                  ? 'text-indigo-600 border-b-2 border-indigo-600 bg-indigo-50/40'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              用户管理员
            </button>
          </div>

          <form onSubmit={handleSubmit} className="p-6 space-y-4">
            <Field icon={<Mail className="w-4 h-4" />} label="邮箱">
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full bg-transparent outline-none text-sm text-gray-900 placeholder:text-gray-400"
              />
            </Field>

            {tab === 'code' ? (
              <Field icon={<KeyRound className="w-4 h-4" />} label="验证码">
                <input
                  required
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="请输入验证码"
                  className="w-full bg-transparent outline-none text-sm text-gray-900 placeholder:text-gray-400"
                />
              </Field>
            ) : (
              <Field icon={<Lock className="w-4 h-4" />} label="密码">
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="请输入密码"
                  className="w-full bg-transparent outline-none text-sm text-gray-900 placeholder:text-gray-400"
                />
              </Field>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="w-full py-2.5 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 transition-colors disabled:opacity-60"
            >
              {submitting ? '登录中…' : '登 录'}
            </button>

            {tab === 'code' && (
              <p className="text-xs text-gray-400 text-center">
                普通用户由所属租户的用户管理员开通账号后，凭邮箱 + 验证码登录。
              </p>
            )}
          </form>
        </div>

        <div className="mt-6 text-center">
          <Link
            to="/admin/login"
            className="inline-flex items-center gap-1.5 text-xs text-gray-400 hover:text-indigo-600 transition-colors"
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            管理员入口
          </Link>
        </div>
      </div>
    </div>
  )
}

function Field({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode
  label: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      <div className="mt-1 flex items-center gap-2 px-3 py-2.5 border border-gray-200 rounded-lg focus-within:border-indigo-400 focus-within:ring-1 focus-within:ring-indigo-200 transition-colors">
        <span className="text-gray-400">{icon}</span>
        {children}
      </div>
    </label>
  )
}
