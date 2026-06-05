import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import MainLayout from './components/MainLayout'
import SettingsLayout from './components/SettingsLayout'
import AdminLayout from './components/AdminLayout'
import RequireAuth from './components/RequireAuth'
import ApiList from './pages/ApiList'
import Workspace from './pages/Workspace'
import Login from './pages/Login'
import AdminLogin from './pages/AdminLogin'
import SystemAdmins from './pages/admin/SystemAdmins'
import TenantAdmins from './pages/admin/TenantAdmins'
import CountryTemplates from './pages/admin/CountryTemplates'
import ApiKeyManagement from './pages/settings/ApiKeyManagement'
import UserManagement from './pages/settings/UserManagement'
import TrafficMonitoring from './pages/settings/TrafficMonitoring'
import Billing from './pages/settings/Billing'
import OcrOptimizer from './pages/settings/OcrOptimizer'
import ToastContainer from './components/ToastContainer'

const PLATFORM = ['super_admin', 'system_admin'] as const

export default function App() {
  return (
    <BrowserRouter>
      <ToastContainer />
      <Routes>
        {/* ── Public: two login portals ── */}
        <Route path="/login" element={<Login />} />
        <Route path="/admin/login" element={<AdminLogin />} />

        {/* ── Customer product (any authenticated user) ── */}
        <Route
          path="/workspace/new"
          element={
            <RequireAuth>
              <Workspace />
            </RequireAuth>
          }
        />
        <Route
          path="/workspace/api/:apiDefinitionId"
          element={
            <RequireAuth>
              <Workspace />
            </RequireAuth>
          }
        />
        <Route
          path="/workspace/:documentId"
          element={
            <RequireAuth>
              <Workspace />
            </RequireAuth>
          }
        />

        <Route
          element={
            <RequireAuth>
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<ApiList />} />
        </Route>

        <Route
          path="/settings"
          element={
            <RequireAuth>
              <SettingsLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="users" replace />} />
          <Route path="users" element={<UserManagement />} />
          <Route path="api-keys" element={<ApiKeyManagement />} />
          <Route path="traffic" element={<TrafficMonitoring />} />
          <Route path="billing" element={<Billing />} />
          {/* template optimization platform — platform admins only */}
          <Route
            path="ocr-optimizer"
            element={
              <RequireAuth roles={[...PLATFORM]}>
                <OcrOptimizer />
              </RequireAuth>
            }
          />
        </Route>

        {/* ── Admin console (super / system admin) ── */}
        <Route
          path="/admin"
          element={
            <RequireAuth roles={[...PLATFORM]} loginPath="/admin/login">
              <AdminLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="tenant-admins" replace />} />
          <Route path="system-admins" element={<SystemAdmins />} />
          <Route path="tenant-admins" element={<TenantAdmins />} />
          <Route path="country-templates" element={<CountryTemplates />} />
        </Route>

        {/* fallback */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
