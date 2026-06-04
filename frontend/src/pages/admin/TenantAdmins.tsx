import { useEffect, useState } from 'react'
import { Plus, Building2 } from 'lucide-react'
import {
  fetchTenantAdmins,
  fetchTenants,
  createTenantAdmin,
  updateUser,
  deactivateUser,
  type AuthUser,
  type TenantRow,
} from '../../lib/api-client'
import { toast } from '../../lib/toast'
import { Modal, TextField, formatDate } from '../../components/admin/adminui'
import { UserTable, ChangePwModal, errMsg } from './SystemAdmins'

export default function TenantAdmins() {
  const [admins, setAdmins] = useState<AuthUser[]>([])
  const [tenants, setTenants] = useState<TenantRow[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [pwTarget, setPwTarget] = useState<AuthUser | null>(null)

  async function load() {
    setLoading(true)
    try {
      const [a, t] = await Promise.all([fetchTenantAdmins(), fetchTenants()])
      setAdmins(a.data)
      setTenants(t.data)
    } catch {
      toast.error('加载用户管理员失败')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    load()
  }, [])

  const tenantName = (id: string | null) =>
    tenants.find((t) => t.id === id)?.name ?? '—'

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
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">用户管理员 / 租户</h1>
          <p className="text-sm text-gray-500 mt-1">
            为每个租户核发一个用户管理员（邮箱+密码）。用户管理员可在本租户内管理普通用户。
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
        >
          <Plus className="w-4 h-4" />
          创建用户管理员
        </button>
      </div>

      {/* tenant overview cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {tenants.map((t) => (
          <div key={t.id} className="bg-white border border-gray-200 rounded-xl p-4">
            <div className="flex items-center gap-2 text-gray-900">
              <Building2 className="w-4 h-4 text-indigo-500" />
              <span className="text-sm font-medium">{t.name}</span>
            </div>
            <div className="mt-2 flex gap-4 text-xs text-gray-500">
              <span>管理员 {t.admin_count}</span>
              <span>普通用户 {t.user_count}</span>
              <span>{formatDate(t.created_at)}</span>
            </div>
          </div>
        ))}
        {tenants.length === 0 && !loading && (
          <p className="text-sm text-gray-400 col-span-full">暂无租户</p>
        )}
      </div>

      <UserTable
        rows={admins}
        loading={loading}
        onChangePw={setPwTarget}
        onToggle={toggleActive}
        extraCol={{ header: '所属租户', render: (u) => tenantName(u.tenant_id) }}
      />

      {creating && (
        <CreateTenantAdminModal
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
    </div>
  )
}

function CreateTenantAdminModal({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: () => void
}) {
  const [tenantName, setTenantName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      await createTenantAdmin({
        email,
        password,
        tenant_name: tenantName,
        display_name: name || undefined,
      })
      toast.success('用户管理员已创建')
      onCreated()
    } catch (e: unknown) {
      toast.error(errMsg(e, '创建失败'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="创建用户管理员" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <TextField
          label="租户名称"
          value={tenantName}
          onChange={setTenantName}
          placeholder="如：Acme 公司"
          required
        />
        <TextField label="管理员邮箱" value={email} onChange={setEmail} type="email" required />
        <TextField label="初始密码" value={password} onChange={setPassword} type="password" required />
        <TextField label="显示名" value={name} onChange={setName} placeholder="可选" />
        <p className="text-xs text-gray-400">
          若租户名称已存在，将复用该租户并为其再增加一个管理员。
        </p>
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
