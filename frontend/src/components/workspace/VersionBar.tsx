import { useWorkspaceStore } from '../../stores/workspace-store'
import apiClient from '../../lib/api-client'

export default function VersionBar() {
  const { processingVersions, currentVersion, setCurrentVersion, documentInfo } = useWorkspaceStore()

  const handleSaveAndGenerateApi = async () => {
    if (!documentInfo?.id) return
    try {
      await apiClient.post(`/api/v1/documents/${documentInfo.id}/generate-api`)
    } catch {
      // fail silently — API may not be ready yet
    }
  }

  return (
    <div className="flex items-center justify-between px-4 py-1.5 bg-white border-b border-gray-100">
      {/* Version dots */}
      <div className="flex items-center gap-1.5">
        {processingVersions.map((v) => (
          <button
            key={v.version}
            onClick={() => setCurrentVersion(v.version)}
            title={`v${v.version}`}
            className={[
              'rounded-full transition-all duration-150',
              v.version === currentVersion
                ? 'w-3 h-3 bg-indigo-500 scale-110'
                : 'w-2.5 h-2.5 bg-gray-200 hover:bg-gray-300',
            ].join(' ')}
          />
        ))}
        {processingVersions.length > 0 && (
          <span className="ml-1 text-[10px] text-gray-400 font-mono select-none">
            v{currentVersion}
          </span>
        )}
      </div>

      {/* Save and generate API button */}
      <button
        onClick={handleSaveAndGenerateApi}
        className="px-3 py-1 text-xs font-semibold text-white rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 hover:from-indigo-600 hover:to-violet-600 shadow-sm shadow-indigo-200 transition-all duration-150"
      >
        保存并生成 API
      </button>
    </div>
  )
}
