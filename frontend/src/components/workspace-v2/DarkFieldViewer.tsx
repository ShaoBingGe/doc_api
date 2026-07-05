// 工作区中栏字段视图（薄入口）。
// B2：2431 行的 14 组件已按职责拆到 ./field-viewer/（shared / rows / panels
// / views）——本文件只保留 Tab 分发主组件。import 方（WorkspaceLayout 等）
// 从这里默认导入，路径不变。
import { type ViewTab } from './field-viewer/shared'
import { FieldsView, RulesView, StatsView } from './field-viewer/views'

export default function DarkFieldViewer({ activeTab = 'fields' }: { activeTab?: ViewTab }) {
  return (
    <div className="flex flex-col h-full bg-[#1e1e24] border-r border-white/10">
      {activeTab === 'fields' && <FieldsView />}
      {activeTab === 'rules'  && <RulesView />}
      {activeTab === 'stats'  && <StatsView />}
    </div>
  )
}
