import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import MainLayout from './components/MainLayout'
import SettingsLayout from './components/SettingsLayout'
import ApiList from './pages/ApiList'
import Workspace from './pages/Workspace'
import ApiKeyManagement from './pages/settings/ApiKeyManagement'
import UserManagement from './pages/settings/UserManagement'
import TrafficMonitoring from './pages/settings/TrafficMonitoring'
import Billing from './pages/settings/Billing'
import ToastContainer from './components/ToastContainer'

export default function App() {
  return (
    <BrowserRouter>
      <ToastContainer />
      <Routes>
        <Route path="/workspace/new" element={<Workspace />} />
        <Route path="/workspace/:documentId" element={<Workspace />} />
        <Route element={<MainLayout />}>
          <Route index element={<ApiList />} />
        </Route>
        <Route path="/settings" element={<SettingsLayout />}>
          <Route index element={<Navigate to="api-keys" replace />} />
          <Route path="users" element={<UserManagement />} />
          <Route path="api-keys" element={<ApiKeyManagement />} />
          <Route path="traffic" element={<TrafficMonitoring />} />
          <Route path="billing" element={<Billing />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
