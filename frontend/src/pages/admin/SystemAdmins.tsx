import { useEffect, useState } from 'react'
import { Plus, Lock, Ban, CheckCircle2 } from 'lucide-react'
import {
  fetchSystemAdmins,
  createSystemAdmin,
  updateUser,
  deactivateUser,
  type AuthUser,
} from '../../lib/api-client'
import { toast } from '../../lib/toast'
import { Modal, TextField, StatusPill, formatDate } from '../../components/admin/adminui'

export default function SystemAdmins() {
  const [rows, setRows] = useState<AuthUser[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [pwTarget, setPwTarget] = useState<AuthUser | null>(null)

  async function load() {
    setLoading(true)
    try {
      const { data } = await fetchSystemAdmins()
      setRows(data)
    } catch {
      toast.error('加载系统管理员失败')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    load()
  }, [])

  async function toggleActive(u: AuthUser) {
    try {
      if (u.is_active) {
        await deactivateUser(u.id)
      } else {
        await updateUser(u.id, { is_active: true })
      }
      toast.success(u.is_active ? '已停用' : '已启用')
      load()
    } catch (e: unknown) {
      toast.error(errMsg(e, '操作失败'))
    }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">系统管理员</h1>
          <p className="text-sm text-gray-500 mt-1">
            可进入模板优化平台、维护用户管理员。仅超级管理员可创建。
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
        >
          <Plus className="w-4 h-4" />
          创建系统管理员
        </button>
      </div>

      <UserTable
        rows={rows}
        loading={loading}
        onChangePw={setPwTarget}
        onToggle={toggleActive}
      />

      {creating && (
        <CreateModal
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false)
            load()
          }}
        />
      )}
      {pwTarget && (
        <ChangePwModal
          target={pwTarget}
          onClose={() => setPwTarget(null)}
          onDone={() => setPwTarget(null)}
        />
      )}
    </div>
  )
}

function CreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      await createSystemAdmin({ email, password, display_name: name || undefined })
      toast.success('系统管理员已创建')
      onCreated()
    } catch (e: unknown) {
      toast.error(errMsg(e, '创建失败'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="创建系统管理员" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <TextField label="邮箱" value={email} onChange={setEmail} type="email" required />
        <TextField label="初始密码" value={password} onChange={setPassword} type="password" required />
        <TextField label="显示名" value={name} onChange={setName} placeholder="可选" />
        <button
          type="submit"
          disabled={busy}
          className="w-full py-2.5 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-60"
        >
          {busy ? '创建中…' : '创建'}
        </button>
      </form>
    </Modal>
  )
}

export function ChangePwModal({
  target,
  onClose,
  onDone,
}: {
  target: AuthUser
  onClose: () => void
  onDone: () => void
}) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      await updateUser(target.id, { password })
      toast.success('密码已重置')
      onDone()
    } catch (e: unknown) {
      toast.error(errMsg(e, '重置失败'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={`重置密码 · ${target.email}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <TextField label="新密码" value={password} onChange={setPassword} type="password" required />
        <button
          type="submit"
          disabled={busy}
          className="w-full py-2.5 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-60"
        >
          {busy ? '提交中…' : '确认重置'}
        </button>
      </form>
    </Modal>
  )
}

export function UserTable({
  rows,
  loading,
  onChangePw,
  onToggle,
  extraCol,
}: {
  rows: AuthUser[]
  loading: boolean
  onChangePw: (u: AuthUser) => void
  onToggle: (u: AuthUser) => void
  extraCol?: { header: string; render: (u: AuthUser) => React.ReactNode }
}) {
  if (loading) return <p className="text-sm text-gray-400 py-8 text-center">加载中…</p>
  if (rows.length === 0)
    return (
      <div className="border border-dashed border-gray-200 rounded-xl py-12 text-center text-sm text-gray-400">
        暂无数据
      </div>
    )
  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-400 border-b border-gray-100">
            <th className="px-4 py-3 font-medium">邮箱</th>
            <th className="px-4 py-3 font-medium">显示名</th>
            {extraCol && <th className="px-4 py-3 font-medium">{extraCol.header}</th>}
            <th className="px-4 py-3 font-medium">状态</th>
            <th className="px-4 py-3 font-medium">创建时间</th>
            <th className="px-4 py-3 font-medium text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((u) => (
            <tr key={u.id} className="border-b border-gray-50 last:border-0">
              <td className="px-4 py-3 text-gray-900">{u.email}</td>
              <td className="px-4 py-3 text-gray-600">{u.display_name || '—'}</td>
              {extraCol && <td className="px-4 py-3 text-gray-600">{extraCol.render(u)}</td>}
              <td className="px-4 py-3">
                <StatusPill active={u.is_active} />
              </td>
              <td className="px-4 py-3 text-gray-400">{formatDate(u.created_at)}</td>
              <td className="px-4 py-3">
                <div className="flex items-center justify-end gap-1">
                  <button
                    onClick={() => onChangePw(u)}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-gray-600 hover:bg-gray-100"
                    title="重置密码"
                  >
                    <Lock className="w-3.5 h-3.5" />
                    重置密码
                  </button>
                  <button
                    onClick={() => onToggle(u)}
                    className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs ${
                      u.is_active
                        ? 'text-red-600 hover:bg-red-50'
                        : 'text-green-600 hover:bg-green-50'
                    }`}
                    title={u.is_active ? '停用' : '启用'}
                  >
                    {u.is_active ? (
                      <>
                        <Ban className="w-3.5 h-3.5" />
                        停用
                      </>
                    ) : (
                      <>
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        启用
                      </>
                    )}
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function errMsg(e: unknown, fallback: string): string {
  return (
    (e as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error
      ?.message ?? fallback
  )
}
