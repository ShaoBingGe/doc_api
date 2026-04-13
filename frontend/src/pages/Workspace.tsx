import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useWorkspaceStore } from '../stores/workspace-store'
import WorkspaceHeader from '../components/workspace-v2/WorkspaceHeader'
import DarkDocumentViewer from '../components/workspace-v2/DarkDocumentViewer'
import DarkFieldViewer from '../components/workspace-v2/DarkFieldViewer'
import DarkJsonViewer from '../components/workspace-v2/DarkJsonViewer'
import AiChat from '../components/workspace-v2/AiChat'
import WorkspaceModals from '../components/workspace-v2/WorkspaceModals'
import InlineUploadPanel from '../components/workspace-v2/InlineUploadPanel'

type HeaderTab = 'fields' | 'rules' | 'stats'

export default function Workspace() {
  const { documentId } = useParams<{ documentId: string }>()
  const navigate = useNavigate()
  const { documentLoading, loadDocument, reset } = useWorkspaceStore()

  const isNewMode = !documentId || documentId === 'new'

  const [activeTab, setActiveTab] = useState<HeaderTab>('fields')
  const [activeModal, setActiveModal] = useState<'save' | null>(null)

  useEffect(() => {
    if (isNewMode || !documentId) return
    reset()
    loadDocument(documentId)
  }, [documentId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Loading state — only for non-new mode
  if (!isNewMode && documentLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-[#18181c]">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="w-8 h-8 animate-spin text-purple-500" />
          <p className="text-sm text-gray-400">加载文档中...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-screen bg-[#18181c] text-white font-sans overflow-hidden">
      <WorkspaceHeader
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onOpenModal={() => setActiveModal('save')}
        isNewMode={isNewMode}
      />

      <div className="flex-1 flex overflow-hidden pb-12">
        {/* Column A: Document Preview / Upload */}
        <div className="w-1/3 min-w-[400px]">
          {isNewMode ? (
            <InlineUploadPanel
              onUploadComplete={(id) => navigate('/workspace/' + id, { replace: true })}
            />
          ) : (
            <DarkDocumentViewer />
          )}
        </div>

        {/* Column B: Field Structure */}
        <div className="w-1/3 min-w-[350px]">
          {isNewMode ? (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">
              上传文档后显示字段
            </div>
          ) : (
            <DarkFieldViewer activeTab={activeTab} />
          )}
        </div>

        {/* Column C: JSON Output */}
        <div className="w-1/3 min-w-[350px]">
          {isNewMode ? (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">
              上传文档后显示 JSON
            </div>
          ) : (
            <DarkJsonViewer />
          )}
        </div>
      </div>

      {!isNewMode && <AiChat />}

      <WorkspaceModals
        activeModal={activeModal}
        onClose={() => setActiveModal(null)}
      />
    </div>
  )
}
