import { useEffect, useState } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useWorkspaceStore } from '../stores/workspace-store'
import WorkspaceHeader, { type HeaderTab } from '../components/workspace-v2/WorkspaceHeader'
import DarkDocumentViewer from '../components/workspace-v2/DarkDocumentViewer'
import DarkFieldViewer from '../components/workspace-v2/DarkFieldViewer'
import DarkJsonViewer from '../components/workspace-v2/DarkJsonViewer'
import AiChat from '../components/workspace-v2/AiChat'
import WorkspaceModals from '../components/workspace-v2/WorkspaceModals'
import InlineUploadPanel from '../components/workspace-v2/InlineUploadPanel'
import DocumentThumbnailColumn from '../components/workspace-v2/DocumentThumbnailColumn'
import OptimizationProcessPanel from '../components/workspace-v2/OptimizationProcessPanel'
import apiClient from '../lib/api-client'

export default function Workspace() {
  const { apiDefinitionId, documentId } = useParams<{
    apiDefinitionId?: string
    documentId?: string
  }>()
  const navigate = useNavigate()
  const location = useLocation()
  const {
    documentLoading,
    samplesLoading,
    documents,
    selectedDocId,
    loadApiDefinition,
    triggerInitialExtraction,
    reset,
  } = useWorkspaceStore()

  // Three modes:
  //   - new:           /workspace/new (no doc, no api yet)
  //   - api (v3):      /workspace/api/:apiDefinitionId  ← canonical batch URL
  //   - legacy doc:    /workspace/:documentId  ← resolve owning API then redirect
  const isNewMode = documentId === 'new' || location.pathname === '/workspace/new'

  const [activeTab, setActiveTab] = useState<HeaderTab>('fields')
  const [activeModal, setActiveModal] = useState<'save' | null>(null)
  // bump to force OptimizationProcessPanel to refetch after a Run finishes
  const [optimizeReloadKey, setOptimizeReloadKey] = useState(0)
  const [optimizing, setOptimizing] = useState(false)

  // ── Mode A: new ────────────────────────────────────────────────────────
  // Stay in new-mode UI until user uploads. After upload completes, they'll
  // be navigated to /workspace/api/{apiDefinitionId} from the create flow.

  // ── Mode B: by-API (v3) ────────────────────────────────────────────────
  useEffect(() => {
    if (isNewMode) return
    if (!apiDefinitionId) return
    reset()
    loadApiDefinition(apiDefinitionId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiDefinitionId, isNewMode])

  // ── Mode C: legacy by-doc → resolve & redirect ─────────────────────────
  useEffect(() => {
    if (isNewMode) return
    if (apiDefinitionId) return // mode B
    if (!documentId) return
    let cancelled = false
    ;(async () => {
      try {
        const defsRes = await apiClient.get('/api/v1/api-definitions')
        const list: Array<Record<string, unknown>> = Array.isArray(defsRes.data)
          ? defsRes.data
          : defsRes.data?.items ?? []
        const owner = list.find((d) => {
          const cfg = (d.config as Record<string, unknown>) || {}
          const ids = (cfg.sample_document_ids as string[]) || []
          return (
            d.sample_document_id === documentId ||
            ids.includes(documentId)
          )
        })
        if (cancelled) return
        if (owner) {
          navigate(`/workspace/api/${owner.id}`, { replace: true })
        } else {
          // No owning API found — fall back to opening the doc by itself
          // in an "ad-hoc" mode: treat the doc as if it were an unbound sample
          // by injecting a synthetic state in the store.
          reset()
          await useWorkspaceStore.getState().loadDocument(documentId)
          if ((location.state as { fromNewApi?: boolean } | null)?.fromNewApi) {
            await triggerInitialExtraction(documentId, { isNewApi: true })
          }
        }
      } catch {
        // network failure — show empty workspace
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId, apiDefinitionId, isNewMode])

  // Loading state — only while we're actually loading
  const showLoading =
    !isNewMode &&
    ((apiDefinitionId && samplesLoading && documents.length === 0) ||
      (!apiDefinitionId && documentLoading))
  if (showLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-[#18181c]">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="w-8 h-8 animate-spin text-purple-500" />
          <p className="text-sm text-gray-400">
            {apiDefinitionId ? '加载样本集中…' : '加载文档中…'}
          </p>
        </div>
      </div>
    )
  }

  const showThumbnailRail = !isNewMode && !!apiDefinitionId
  const hasSelectedDoc = !!selectedDocId

  return (
    <div className="flex flex-col h-screen bg-[#18181c] text-white font-sans overflow-hidden">
      <WorkspaceHeader
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onOpenModal={() => setActiveModal('save')}
        isNewMode={isNewMode}
        onOptimizationTriggered={() => {
          // Bump reload key so the optimize panel refetches; also flip optimizing flag
          // (the header keeps its own optimizing state for the button spinner).
          setOptimizing((prev) => !prev) // toggle: pre-fire & post-fire
          setOptimizeReloadKey((k) => k + 1)
        }}
      />

      <div className="flex-1 flex overflow-hidden pb-12">
        {/* Thumbnail rail (only in by-API mode) */}
        {showThumbnailRail && <DocumentThumbnailColumn />}

        {/* Main content: switches by activeTab */}
        {activeTab === 'optimize' && apiDefinitionId ? (
          <div className="flex-1">
            <OptimizationProcessPanel
              apiDefinitionId={apiDefinitionId}
              reloadKey={optimizeReloadKey}
              optimizing={optimizing}
            />
          </div>
        ) : (
          <>
            {/* Column A: Document Preview / Upload */}
            <div className="flex-1 min-w-[360px]">
              {isNewMode ? (
                <InlineUploadPanel
                  onUploadComplete={(id) =>
                    // Legacy: after first upload during "new API" flow, the
                    // create-API modal saves the ApiDefinition with that doc
                    // as the first sample, then redirects via apiDefinitionId.
                    // Until that's wired through, fall back to legacy URL.
                    navigate('/workspace/' + id, {
                      replace: true,
                      state: { fromNewApi: true },
                    })
                  }
                />
              ) : hasSelectedDoc ? (
                <DarkDocumentViewer />
              ) : (
                <EmptySampleHint />
              )}
            </div>

            {/* Column B: Field Structure */}
            <div className="flex-1 min-w-[320px]">
              {isNewMode ? (
                <div className="flex items-center justify-center h-full text-gray-500 text-sm">
                  上传文档后显示字段
                </div>
              ) : hasSelectedDoc ? (
                <DarkFieldViewer activeTab={activeTab === 'fields' ? 'fields' : 'fields'} />
              ) : (
                <div className="flex items-center justify-center h-full text-gray-500 text-sm">
                  选择左侧样本查看字段
                </div>
              )}
            </div>

            {/* Column C: JSON Output */}
            <div className="flex-1 min-w-[320px]">
              {isNewMode ? (
                <div className="flex items-center justify-center h-full text-gray-500 text-sm">
                  上传文档后显示 JSON
                </div>
              ) : hasSelectedDoc ? (
                <DarkJsonViewer />
              ) : (
                <div className="flex items-center justify-center h-full text-gray-500 text-sm">
                  选择左侧样本查看 JSON
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {!isNewMode && hasSelectedDoc && activeTab !== 'optimize' && <AiChat />}

      <WorkspaceModals
        activeModal={activeModal}
        onClose={() => setActiveModal(null)}
      />
    </div>
  )
}

function EmptySampleHint() {
  return (
    <div className="flex flex-col items-center justify-center h-full bg-[#18181c] text-gray-500 px-8 text-center">
      <p className="text-sm font-medium text-gray-300 mb-1">
        该 API 尚无样本文档
      </p>
      <p className="text-xs text-gray-500">
        点击左侧 "+" 按钮上传第一张样本，
        <br />
        至少 3 张才能触发优化。
      </p>
    </div>
  )
}
