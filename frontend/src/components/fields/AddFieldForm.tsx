import { useState } from 'react'
import { Plus, X, Check } from 'lucide-react'
import { useWorkspaceStore, type Annotation } from '../../stores/workspace-store'
import { createAnnotation } from '../../lib/api-client'

type FieldType = 'string' | 'number' | 'date' | 'array'

const FIELD_TYPES: { value: FieldType; label: string }[] = [
  { value: 'string', label: '字符串' },
  { value: 'number', label: '数字' },
  { value: 'date', label: '日期' },
  { value: 'array', label: '数组' },
]

export default function AddFieldForm() {
  const { addAnnotation, documentInfo } = useWorkspaceStore()
  const [expanded, setExpanded] = useState(false)
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [fieldType, setFieldType] = useState<FieldType>('string')

  const handleSave = async () => {
    const trimmedName = name.trim()
    if (!trimmedName) return

    const annotation: Annotation = {
      id: `manual-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      label: trimmedName,
      fieldType: fieldType === 'string' ? 'text' : fieldType,
      page: 1,
      // Place in a default position that doesn't overlap existing fields
      boundingBox: { x: 5, y: 85, width: 40, height: 5 },
      value: value.trim() || null,
      isManual: true,
    }

    addAnnotation(annotation)

    // T3.5 — persist to backend
    if (documentInfo?.id) {
      try {
        await createAnnotation(documentInfo.id, {
          label: annotation.label,
          field_type: annotation.fieldType,
          bounding_box: annotation.boundingBox,
          value: annotation.value,
          source: 'manual',
        })
      } catch {
        // fail silently — local state already updated
      }
    }

    setName('')
    setValue('')
    setFieldType('string')
    setExpanded(false)
  }

  const handleCancel = () => {
    setName('')
    setValue('')
    setFieldType('string')
    setExpanded(false)
  }

  if (!expanded) {
    return (
      <div className="px-2 py-2">
        <button
          onClick={() => setExpanded(true)}
          className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium text-indigo-600 border border-dashed border-indigo-200 rounded-xl hover:bg-indigo-50 hover:border-indigo-300 transition-all duration-150"
        >
          <Plus className="w-3.5 h-3.5" />
          添加识别字段
        </button>
      </div>
    )
  }

  return (
    <div className="mx-2 mb-2 rounded-xl border border-indigo-200 bg-indigo-50/60 overflow-hidden">
      <div className="px-3 pt-2.5 pb-1.5 flex items-center justify-between">
        <span className="text-[11px] font-semibold text-indigo-700">新增字段</span>
        <button onClick={handleCancel} className="p-0.5 text-gray-400 hover:text-gray-600 rounded">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="px-3 pb-3 space-y-2">
        {/* Name */}
        <div>
          <label className="block text-[10px] text-gray-500 mb-0.5 font-medium">字段名称</label>
          <input
            autoFocus
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSave()}
            placeholder="e.g. invoice_number"
            className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-400 placeholder:text-gray-300"
          />
        </div>

        {/* Value */}
        <div>
          <label className="block text-[10px] text-gray-500 mb-0.5 font-medium">默认值</label>
          <input
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="（可选）"
            className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-400 placeholder:text-gray-300"
          />
        </div>

        {/* Type */}
        <div>
          <label className="block text-[10px] text-gray-500 mb-0.5 font-medium">数据类型</label>
          <select
            value={fieldType}
            onChange={(e) => setFieldType(e.target.value as FieldType)}
            className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-400"
          >
            {FIELD_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        {/* Actions */}
        <div className="flex gap-1.5 pt-0.5">
          <button
            onClick={handleSave}
            disabled={!name.trim()}
            className="flex-1 flex items-center justify-center gap-1 py-1.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-200 disabled:text-gray-400 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
          >
            <Check className="w-3 h-3" />
            保存
          </button>
          <button
            onClick={handleCancel}
            className="flex-1 flex items-center justify-center gap-1 py-1.5 text-xs font-medium text-gray-600 bg-white border border-gray-200 hover:bg-gray-50 rounded-lg transition-colors"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  )
}
