import { useEffect, useState } from 'react'
import { User, Mail, Lock, Plus, ShieldCheck } from 'lucide-react'
import {
  fetchTenantUsers,
  createNormalUser,
  updateUser,
  deactivateUser,
  changePassword,
  type AuthUser,
} from '../../lib/api-client'
import { toast } from '../../lib/toast'
import { useAuthStore, ROLE_LABELS } from '../../stores/auth-store'
import { Modal, TextField } from '../../components/admin/adminui'
import { UserTable, ChangePwModal, errMsg } from '../admin/SystemAdmins'

export default function UserManagement() {
  const user = useAuthStore((s) => s.user)
  const tenant = useAuthStore((s) => s.tenant)
  const isTenantAdmin = user?.role === 'tenant_admin'

  const [rows, setRows] = useState<AuthUser[]>([])
  const [loading, setLoading] = useState(isTenantAdmin)
  const [creating, setCreating] = useState(false)
  const [pwTarget, setPwTarget] = useState<AuthUser | null>(null)
  const [ownPwOpen, setOwnPwOpen] = useState(false)

  async function load() {
    if (!isTenantAdmin) return
    setLoading(true)
    try {
      const { data } = await fetchTenantUsers()
      setRows(data)
    } catch {
      toast.error('加载普通用户失败')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isTenantAdmin])

  async function toggleActive(u: AuthUser) {
    try {
      if (u.is_active) await deactivateUser(u.id)
      else await updateUser(u.id, { is_active: true })
      toast.success(u.is_active ? '已停用' : '已启用')
      load()
    } catch (e: unknown) {
      toast.error(errMsg(e, '操作失败'))
    }
  }

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">用户管理</h1>
        <p className="text-sm text-gray-500 mt-1">查看账户信息、管理本租户普通用户</p>
      </div>

      {/* Own profile */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-900">个人信息</h2>
        </div>
        <div className="p-6 flex items-start gap-5">
          <div className="w-16 h-16 bg-indigo-100 rounded-xl flex items-center justify-center flex-shrink-0">
            <User className="w-8 h-8 text-indigo-600" />
          </div>
          <div className="flex-1 grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Info icon={<Mail className="w-4 h-4 text-gray-400" />} label="邮箱" value={user?.email ?? '—'} />
            <Info
              icon={<ShieldCheck className="w-4 h-4 text-gray-400" />}
              label="角色"
              value={user ? ROLE_LABELS[user.role] : '—'}
            />
            <Info icon={<User className="w-4 h-4 text-gray-400" />} label="显示名" value={user?.display_name || '—'} />
            <Info icon={<Mail className="w-4 h-4 text-gray-400" />} label="租户" value={tenant?.name ?? '—'} />
          </div>
          <button
            onClick={() => setOwnPwOpen(true)}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm text-gray-600 border border-gray-200 hover:bg-gray-50"
          >
            <Lock className="w-4 h-4" />
            修改密码
          </button>
        </div>
      </div>

      {/* Normal users (tenant admin only) */}
      {isTenantAdmin && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-900">普通用户</h2>
            <button
              onClick={() => setCreating(true)}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
            >
              <Plus className="w-4 h-4" />
              新增普通用户
            </button>
          </div>
          <p className="text-xs text-gray-400">
            普通用户凭「邮箱 + 验证码」登录。请在此处先开通其邮箱账号。
          </p>
          <UserTable rows={rows} loading={loading} onChangePw={setPwTarget} onToggle={toggleActive} />
        </div>
      )}

      {creating && (
        <CreateNormalUserModal
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false)
            load()
          }}
        />
      )}
      {pwTarget && (
        <ChangePwModal target={pwTarget} onClose={() => setPwTarget(null)} onDone={() => setPwTarget(null)} />
      )}
      {ownPwOpen && <OwnPasswordModal onClose={() => setOwnPwOpen(false)} />}
    </div>
  )
}

function Info({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
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

function CreateNormalUserModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      await createNormalUser({ email, display_name: name || undefined })
      toast.success('普通用户已开通')
      onCreated()
    } catch (e: unknown) {
      toast.error(errMsg(e, '创建失败'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title="新增普通用户" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <TextField label="邮箱" value={email} onChange={setEmail} type="email" required />
        <TextField label="显示名" value={name} onChange={setName} placeholder="可选" />
        <p className="text-xs text-gray-400">该用户将以「邮箱 + 验证码」登录（无需设置密码）。</p>
        <button
          type="submit"
          disabled={busy}
          className="w-full py-2.5 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-60"
        >
          {busy ? '提交中…' : '开通'}
        </button>
      </form>
    </Modal>
  )
}

function OwnPasswordModal({ onClose }: { onClose: () => void }) {
  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      await changePassword(oldPw, newPw)
      toast.success('密码已修改')
      onClose()
    } catch (e: unknown) {
      toast.error(errMsg(e, '修改失败'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title="修改密码" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <TextField label="原密码" value={oldPw} onChange={setOldPw} type="password" required />
        <TextField label="新密码" value={newPw} onChange={setNewPw} type="password" required />
        <button
          type="submit"
          disabled={busy}
          className="w-full py-2.5 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-60"
        >
          {busy ? '提交中…' : '确认修改'}
        </button>
      </form>
    </Modal>
  )
}
