import { useWorkspaceStore } from '../../stores/workspace-store'
import NlCorrectionBar from './NlCorrectionBar'
import FieldCard from './FieldCard'
import AddFieldForm from './AddFieldForm'
import ApiPreviewPanel from '../api/ApiPreviewPanel'

// ─── Tab button ───────────────────────────────────────────────────────────────

interface TabButtonProps {
  label: string
  active: boolean
  onClick: () => void
}

function TabButton({ label, active, onClick }: TabButtonProps) {
  return (
    <button
      onClick={onClick}
      className={[
        'flex-1 py-2 text-xs font-semibold rounded-lg transition-all duration-150',
        active
          ? 'bg-white text-indigo-600 shadow-sm'
          : 'text-gray-500 hover:text-gray-700',
      ].join(' ')}
    >
      {label}
    </button>
  )
}

// ─── Fields tab content ───────────────────────────────────────────────────────

function FieldsTab() {
  const { annotations, processingResults } = useWorkspaceStore()
  const resultMap = new Map(processingResults.map((r) => [r.annotationId, r]))

  return (
    <div className="flex flex-col h-full">
      <NlCorrectionBar />

      {/* Field list header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
        <span className="text-[11px] font-semibold text-gray-600 uppercase tracking-wide">
          识别字段
        </span>
        <span className="text-[10px] text-gray-400 tabular-nums">
          {annotations.length}
        </span>
      </div>

      {/* Scrollable field list */}
      <div className="flex-1 overflow-y-auto py-2">
        {annotations.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-gray-400">
            <p className="text-xs text-center leading-relaxed">
              暂无识别字段
              <br />
              <span className="text-[10px]">点击文档区域或手动添加</span>
            </p>
          </div>
        ) : (
          annotations.map((ann) => (
            <FieldCard
              key={ann.id}
              annotation={ann}
              result={resultMap.get(ann.id)}
            />
          ))
        )}
      </div>

      {/* Add field form — pinned at bottom */}
      <div className="border-t border-gray-100">
        <AddFieldForm />
      </div>
    </div>
  )
}

// ─── Main panel ───────────────────────────────────────────────────────────────

export default function FieldEditorPanel() {
  const { activeTab, setActiveTab } = useWorkspaceStore()

  return (
    <div
      className="flex flex-col bg-white border-l border-gray-200"
      style={{ width: 340, minWidth: 340, flexShrink: 0 }}
    >
      {/* Tab toggle */}
      <div className="flex items-center gap-1 p-2 bg-gray-50 border-b border-gray-200">
        <TabButton
          label="字段"
          active={activeTab === 'fields'}
          onClick={() => setActiveTab('fields')}
        />
        <TabButton
          label="API"
          active={activeTab === 'api'}
          onClick={() => setActiveTab('api')}
        />
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === 'fields' ? <FieldsTab /> : <ApiPreviewPanel />}
      </div>
    </div>
  )
}
