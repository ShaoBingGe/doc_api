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
import CountryPickerBar from '../components/workspace-v2/CountryPickerBar'
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

      {/* New-API entry: country picker takes over the whole content area.
          Once the user picks a country, the backend creates a placeholder API +
          v1 + 30 modules, and we navigate to /workspace/api/<id>. */}
      {isNewMode ? (
        <div className="flex-1 flex flex-col overflow-hidden">
          <CountryPickerBar
            onPicked={(apiDefId) =>
              navigate(`/workspace/api/${apiDefId}`, { replace: true })
            }
          />
          <div className="flex-1 flex items-center justify-center text-gray-500 text-sm px-8 text-center">
            <div>
              <p className="text-gray-300 font-medium mb-1">请先选择一个国家模板</p>
              <p className="text-gray-500">
                选中国家后会基于该国家预设 prompt 创建一个占位 API，
                <br />
                之后你可以在它的工作台里上传样本、跑 OCR、编辑 Ground Truth。
              </p>
            </div>
          </div>
        </div>
      ) : (
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
                {hasSelectedDoc ? (
                  <DarkDocumentViewer />
                ) : apiDefinitionId ? (
                  <InlineUploadPanel
                    onUploadComplete={() => {
                      // After upload, the doc list refresh + selection happens via
                      // workspace-store; just re-load the API to pick up the new sample.
                      if (apiDefinitionId) loadApiDefinition(apiDefinitionId)
                    }}
                    apiDefinitionId={apiDefinitionId}
                  />
                ) : (
                  <EmptySampleHint />
                )}
              </div>

              {/* Column B: Field Structure */}
              <div className="flex-1 min-w-[320px]">
                {hasSelectedDoc ? (
                  <DarkFieldViewer activeTab={activeTab === 'fields' ? 'fields' : 'fields'} />
                ) : (
                  <div className="flex items-center justify-center h-full text-gray-500 text-sm">
                    选择左侧样本查看字段
                  </div>
                )}
              </div>

              {/* Column C: JSON Output */}
              <div className="flex-1 min-w-[320px]">
                {hasSelectedDoc ? (
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
      )}

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
