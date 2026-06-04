import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { User, Lock, ShieldCheck } from 'lucide-react'
import { useAuthStore } from '../stores/auth-store'
import { toast } from '../lib/toast'

export default function AdminLogin() {
  const navigate = useNavigate()
  const loginWithPassword = useAuthStore((s) => s.loginWithPassword)

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (submitting) return
    setSubmitting(true)
    try {
      const user = await loginWithPassword(username.trim(), password)
      if (user.role !== 'super_admin' && user.role !== 'system_admin') {
        // a tenant admin logged in via the admin portal → still allow, send home
        toast.success('登录成功')
        navigate('/', { replace: true })
        return
      }
      toast.success('登录成功')
      navigate('/admin', { replace: true })
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error
          ?.message ?? '登录失败，请检查账号与密码'
      toast.error(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-slate-800 to-indigo-900 px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-white/10 mb-3">
            <ShieldCheck className="w-6 h-6 text-indigo-300" />
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight">管理员入口</h1>
          <p className="text-sm text-slate-400 mt-1">国家模板 · 黄金种子 · 优化迭代平台</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-2xl shadow-xl p-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            <label className="block">
              <span className="text-xs text-gray-500">账号</span>
              <div className="mt-1 flex items-center gap-2 px-3 py-2.5 border border-gray-200 rounded-lg focus-within:border-indigo-400 focus-within:ring-1 focus-within:ring-indigo-200 transition-colors">
                <User className="w-4 h-4 text-gray-400" />
                <input
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="admin"
                  className="w-full bg-transparent outline-none text-sm text-gray-900 placeholder:text-gray-400"
                />
              </div>
            </label>

            <label className="block">
              <span className="text-xs text-gray-500">密码</span>
              <div className="mt-1 flex items-center gap-2 px-3 py-2.5 border border-gray-200 rounded-lg focus-within:border-indigo-400 focus-within:ring-1 focus-within:ring-indigo-200 transition-colors">
                <Lock className="w-4 h-4 text-gray-400" />
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="请输入密码"
                  className="w-full bg-transparent outline-none text-sm text-gray-900 placeholder:text-gray-400"
                />
              </div>
            </label>

            <button
              type="submit"
              disabled={submitting}
              className="w-full py-2.5 rounded-lg bg-slate-900 text-white text-sm font-medium hover:bg-slate-800 transition-colors disabled:opacity-60"
            >
              {submitting ? '登录中…' : '登 录'}
            </button>
          </form>
        </div>

        <div className="mt-6 text-center">
          <Link
            to="/login"
            className="text-xs text-slate-400 hover:text-indigo-300 transition-colors"
          >
            ← 返回用户登录
          </Link>
        </div>
      </div>
    </div>
  )
}
