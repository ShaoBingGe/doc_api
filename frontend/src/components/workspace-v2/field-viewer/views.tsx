// B2：DarkFieldViewer 拆分——三个 Tab 视图（FieldsView / RulesView / StatsView）。
import { useState, useMemo, useCallback } from 'react'
import { ChevronDown, ChevronRight, FileText, List, Plus, Link as LinkIcon, CheckCircle2, AlertCircle, BarChart2, Trash2 } from 'lucide-react'
import { cn } from '../../../lib/utils'
import { toast } from '../../../lib/toast'
import { useWorkspaceStore, type Annotation } from '../../../stores/workspace-store'
import { groupAnnotations } from './shared'
import { FieldRow, NewFieldRow, ArrayTable } from './rows'
import {
  PendingFieldsBar, FieldEditPanel, MissingFieldsList, AddFieldList, CustomizeBar,
} from './panels'


export function FieldsView() {
  const {
    annotations, processingResults, hoveredFieldId, setHoveredFieldId,
    selectedFieldId, setSelectedFieldId,
    documentInfo, removeAnnotation, updateFieldValue,
    saveAnnotation, deleteAnnotationRemote,
    addPendingField, drawingFieldId, setDrawingFieldId,
    editingFieldId, fieldEditDrafts, startEditingField, cancelEditingField,
    updateEditDraft, addNewFieldDraft, addFieldDrafts,
    pendingEdits,  // design v8 — cross-sample overlay
    commitCurrentDraft,  // Phase 10
    commitFieldDeletion,  // Phase 11c
    requiredFields,  // Phase 13 — canonical field set
    lockedFields,  // country-locked (regulatory) field names — UI disables edits
    apiDefinitionId,  // Phase 25 — needed for in-row cascade delete
  } = useWorkspaceStore()

  // Country-locked lookup: a field is locked if its name (or its top-level
  // token, for nested leaves) is in lockedFields. Used to disable
  // rename/retype/delete/add in the field panel.
  const lockedSet = useMemo(() => new Set(lockedFields || []), [lockedFields])
  const isFieldLocked = useCallback(
    (name: string | undefined | null): boolean => {
      if (!name) return false
      if (lockedSet.has(name)) return true
      const top = name.split(/[.[]/)[0]
      return lockedSet.has(top)
    },
    [lockedSet],
  )

  // design v8 — derive "edited on OTHER files" set and the list of added
  // fields from OTHER files not yet present locally. See pending_edits_service.py.
  const currentDocId = documentInfo?.id

  // Phase 21 — uncommitted FieldEditPanel drafts are a SECOND source of
  // truth for rename / value-modification intent. Customers who use
  // "仅暂存" instead of "保存到模板（立即生效）" never write to
  // pending_edits, so cross-doc badges based on overlay alone go quiet
  // until customize is submitted. Bridge drafts → overlay-shape so
  // every downstream computation (renamesReverse, otherDocsEditedFields,
  // valueModifiedHere) sees the full picture.
  const draftRenameMap = useMemo(() => {
    // {oldName: newName} from any draft with a rename intent
    const m: Record<string, string> = {}
    for (const draft of Object.values(fieldEditDrafts)) {
      const oldN = (draft.originalName || '').trim()
      const newN = (draft.correctedName || '').trim()
      if (oldN && newN && oldN !== newN) m[oldN] = newN
    }
    return m
  }, [fieldEditDrafts])
  const draftValueEditedFields = useMemo(() => {
    // Set of field names that have a value-modifying draft
    const out = new Set<string>()
    for (const [key, draft] of Object.entries(fieldEditDrafts)) {
      const ov = String(draft.originalValue ?? '')
      const cv = String(draft.correctedValue ?? '')
      if (ov !== cv) out.add(key)
    }
    return out
  }, [fieldEditDrafts])

  const otherDocsEditedFields = useMemo(() => {
    const out = new Set<string>()
    // From committed overlay (per-doc value mods)
    if (pendingEdits && currentDocId) {
      for (const [docId, fieldsObj] of Object.entries(pendingEdits.modifications || {})) {
        if (docId === currentDocId) continue
        for (const fname of Object.keys(fieldsObj)) out.add(fname)
      }
    }
    // From uncommitted drafts — surface them too. Drafts are
    // workspace-scoped (Zustand store) and can belong to ANY doc;
    // treat them as "other docs' edits" unless they match a label on
    // the current doc (in which case the local "已暂存" badge takes
    // precedence — see FieldRow's badge priority).
    for (const draftKey of draftValueEditedFields) {
      out.add(draftKey)
    }
    return out
  }, [pendingEdits, currentDocId, draftValueEditedFields])

  // Fields the customer ADDED (committed to overlay) that this doc has no
  // value for yet. Rendered in the "其他文件已新增字段" section with the SAME
  // fillable interaction as the missing-fields list. The add handler stores
  // these in overlay.added_fields with NO per-doc annotation, so every doc
  // that lacks a local annotation for the name should offer to fill it.
  const otherDocsAddedFieldNames = useMemo(() => {
    const localNames = new Set(annotations.map((a) => a.label))
    const seen = new Set<string>()
    const out: string[] = []
    for (const f of pendingEdits?.added_fields || []) {
      const name = f.field_name
      if (!name || localNames.has(name) || seen.has(name)) continue
      seen.add(name)
      out.push(name)
    }
    return out
  }, [pendingEdits, annotations])

  // Phase 13 + 23.4 fix — fields the LLM should have produced but didn't
  // on this doc. requiredFields uses POST-rename names (from the
  // /required-fields endpoint, which already applies overlay.renames).
  // Bug: an annotation labeled `billFromName` doesn't equal
  // `salerCompany` in the simple set check, so the renamed field
  // wrongly appears in the missing list. Fix by considering an
  // annotation as covering BOTH its current label AND the rename
  // target (forward map) of that label. Same for draft renames so
  // uncommitted intent is also honored.
  const missingRequiredFields = useMemo(() => {
    if (!requiredFields || requiredFields.length === 0) return [] as string[]
    const forwardMap: Record<string, string> = {
      ...(pendingEdits?.renames || {}),
      ...draftRenameMap,
    }
    const covered = new Set<string>()
    for (const a of annotations) {
      covered.add(a.label)
      const mappedNew = forwardMap[a.label]
      if (mappedNew) covered.add(mappedNew)
      // Array/flattened labels like `detailOfGoodsOrServices[0].description`
      // cover the top-level base name `detailOfGoodsOrServices`, so an array
      // field that's clearly present (its rows render above) is NOT wrongly
      // listed as "未识别".
      const brk = a.label.indexOf('[')
      const dot = a.label.indexOf('.')
      let cut = -1
      if (brk >= 0) cut = brk
      if (dot >= 0) cut = cut < 0 ? dot : Math.min(cut, dot)
      if (cut > 0) covered.add(a.label.slice(0, cut))
    }
    // Overlay-added fields are surfaced in their OWN "其他文件已新增字段"
    // section (also fillable), so exclude them here to avoid double-listing.
    const overlayAdded = new Set(
      (pendingEdits?.added_fields || []).map((f) => f.field_name),
    )
    return requiredFields.filter(
      (f) => !covered.has(f) && !overlayAdded.has(f),
    )
  }, [requiredFields, annotations, pendingEdits, draftRenameMap])

  // design v8 + Phase 21 — reverse rename map {new → old} used to:
  //   - show "原: oldName" subtitle + "已重命名" badge on renamed rows
  //   - fall back to fieldEditDrafts[oldName] when looking up dirty drafts
  // Phase 21: merge BOTH committed overlay renames AND uncommitted draft
  // renames so cross-doc badges work even when the customer is still
  // editing (hasn't clicked "保存到模板（立即生效）" yet).
  const renamesReverse = useMemo(() => {
    const m: Record<string, string> = {}
    for (const [oldN, newN] of Object.entries(pendingEdits?.renames || {})) {
      m[newN] = oldN
    }
    for (const [oldN, newN] of Object.entries(draftRenameMap)) {
      // Overlay wins on conflict (it's already persisted)
      if (!m[newN]) m[newN] = oldN
    }
    return m
  }, [pendingEdits, draftRenameMap])

  // Set of field names whose value was modified on THIS doc and persisted
  // to pending_edits.modifications (used for purple "已保存修改" badge).
  const valueModifiedHere = useMemo(() => {
    if (!pendingEdits || !currentDocId) return new Set<string>()
    return new Set(Object.keys(pendingEdits.modifications?.[currentDocId] || {}))
  }, [pendingEdits, currentDocId])
  const resultMap = useMemo(
    () => new Map(processingResults.map((r) => [r.annotationId, r])),
    [processingResults],
  )

  // Bucket annotations: scalars stay in the basic-info list; anything matching
  // `path[N].field` gets pulled into a per-array table below.
  //
  // Bug fix (delete-reappear): even after pending_edits.deleted_fields
  // records a deletion + cascade hard-deletes annotation rows, the doc's
  // ProcessingResult.structured_data still contains the original key.
  // loadDocument re-parses that JSON and re-creates client annotations
  // for the deleted field. Filter them out at render time so the user
  // sees a consistent "deleted = gone" view.
  const deletedFieldsSet = useMemo(
    () => new Set(pendingEdits?.deleted_fields || []),
    [pendingEdits],
  )
  // Phase 23.4 — last-mile rename safety-net: even after the backend's
  // Phase 23.3 sweep rewrites structured_data, in-flight uncommitted
  // renames (FieldEditPanel drafts not yet pushed to overlay) need the
  // workspace to display the NEW name on the current doc too. We apply
  // both committed (overlay.renames) and draft renames at render time.
  // Forward map {oldName: newName} — overlay wins on conflict.
  const forwardRenameMap = useMemo(() => {
    const m: Record<string, string> = { ...(pendingEdits?.renames || {}) }
    for (const [oldN, newN] of Object.entries(draftRenameMap)) {
      if (!m[oldN]) m[oldN] = newN
    }
    return m
  }, [pendingEdits, draftRenameMap])
  const visibleAnnotations = useMemo(
    () => annotations
      .filter((a) => !deletedFieldsSet.has(a.label))
      // Filter out the OLD-named row when the rename's NEW-named row
      // is also present (post-23.3 docs may have both during a brief
      // window if structured_data caching races; show only the latest).
      .filter((a) => {
        const newN = forwardRenameMap[a.label]
        if (!newN) return true
        // a is an OLD-named row. Hide if a NEW-named row also exists.
        return !annotations.some((b) => b.label === newN)
      })
      .map((a) => {
        // Apply rename to displayed label
        const newN = forwardRenameMap[a.label]
        return newN ? { ...a, label: newN, _renamedFrom: a.label } : a
      }),
    [annotations, deletedFieldsSet, forwardRenameMap],
  )
  const { scalars, arrays } = useMemo(
    () => groupAnnotations(visibleAnnotations, resultMap),
    [visibleAnnotations, resultMap],
  )

  const [expanded, setExpanded] = useState({ basic: true, summary: true })
  const toggle = (k: keyof typeof expanded) =>
    setExpanded((p) => ({ ...p, [k]: !p[k] }))

  const [addingField, setAddingField] = useState(false)

  const docId = documentInfo?.id

  // ── Edit handlers ──────────────────────────────────────────────────────────

  const handleSaveLabel = useCallback((id: string, label: string) => {
    // Update local store label
    useWorkspaceStore.setState((state) => ({
      annotations: state.annotations.map((a) =>
        a.id === id ? { ...a, label } : a,
      ),
    }))
    // Persist to backend
    if (docId) saveAnnotation(docId, id, { field_name: label })
  }, [docId, saveAnnotation])

  const handleSaveValue = useCallback((id: string, value: string) => {
    updateFieldValue(id, value)
    if (docId) saveAnnotation(docId, id, { field_value: value })
  }, [docId, updateFieldValue, saveAnnotation])

  const handleSaveType = useCallback((id: string, type: string) => {
    useWorkspaceStore.setState((state) => ({
      annotations: state.annotations.map((a) =>
        a.id === id ? { ...a, fieldType: type as Annotation['fieldType'] } : a,
      ),
    }))
    if (docId) saveAnnotation(docId, id, { field_type: type })
  }, [docId, saveAnnotation])

  const handleConfirmConfidence = useCallback((id: string) => {
    // Set local confidence to 100
    useWorkspaceStore.setState((state) => ({
      processingResults: state.processingResults.map((r) =>
        r.annotationId === id ? { ...r, confidence: 100 } : r,
      ),
    }))
    // Persist: mark as confirmed (confidence=1.0 on backend is 0-1 scale)
    if (docId) saveAnnotation(docId, id, { confidence: 1.0 })
    toast.success('字段已确认')
  }, [docId, saveAnnotation])

  // Phase 25 — the in-row trash icon now mirrors the edit-panel's
  // "删除此字段（所有样本 + 优化器同步生效）" button: it cascades the deletion
  // to every doc of the ApiDef AND drops the field from the next customize
  // fork's modules (no meta, no reflection, no schema slot) via the overlay.
  // The old per-doc-only deleteAnnotationRemote path is retained as a
  // fallback when there's no ApiDef context (draft-only workspace).
  const handleDeleteField = useCallback(async (id: string) => {
    const ann = annotations.find((a) => a.id === id)
    const label = ann?.label ?? id
    if (!apiDefinitionId) {
      // No ApiDef → local/per-doc removal only.
      if (!docId) { removeAnnotation(id); return }
      deleteAnnotationRemote(docId, id)
      toast.success('字段已删除')
      return
    }
    if (!confirm(
      `确认从所有样本中删除字段 "${label}"？\n\n` +
      `· 所有文件中该字段的标注将被永久删除\n` +
      `· 客户化时不再生成该字段的 module / 反思 / schema\n` +
      `· 此操作不可撤销`,
    )) return
    await commitFieldDeletion(id)
    toast.success('字段已从所有样本中删除')
  }, [annotations, apiDefinitionId, docId, removeAnnotation, deleteAnnotationRemote, commitFieldDeletion])

  const handleQueueField = useCallback((name: string, value: string) => {
    addPendingField(name, value)
    toast.info(value ? `已暂存：${name} = ${value}` : `已暂存：${name}`)
  }, [addPendingField])

  const handleStartDrawing = useCallback((id: string) => {
    setDrawingFieldId(id)
    toast.info('在左侧文档上点击字段位置，按 Esc 取消')
  }, [setDrawingFieldId])

  // Find the currently-editing annotation + its draft, if any
  const editingAnnotation = editingFieldId
    ? annotations.find((a) => a.id === editingFieldId)
    : null
  // Draft key = full label (same as startEditingField).
  const editingDraftKey = editingAnnotation ? editingAnnotation.label : null
  const editingDraft = editingDraftKey ? fieldEditDrafts[editingDraftKey] : undefined

  // ── Edit-mode view: show the panel + customize bar + nothing else ─────
  if (editingAnnotation && editingDraft) {
    return (
      <div className="flex-1 overflow-auto p-4 space-y-4">
        <FieldEditPanel
          annotation={editingAnnotation}
          result={resultMap.get(editingAnnotation.id)}
          draft={editingDraft}
          onUpdate={(patch) => updateEditDraft(editingDraftKey!, patch)}
          onCancel={cancelEditingField}
          onSaveToOverlay={async () => {
            await commitCurrentDraft()
            toast.success('已保存到模板，其他样本会自动同步显示')
          }}
          onDelete={async () => {
            await commitFieldDeletion()
            toast.success('字段已从所有样本中删除')
          }}
        />
        <CustomizeBar />
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      {/* Customize job progress / save CTA — lives at the top so it's always visible */}
      <CustomizeBar />

      {/* Add field list (4-column) */}
      {addFieldDrafts.length > 0 && <AddFieldList />}

      {/* Add field + Skill bar */}
      <div className="flex gap-2">
        <button
          onClick={() => { addNewFieldDraft(); setAddingField(false) }}
          className="flex-1 flex items-center gap-2 px-3 py-1.5 text-sm text-purple-400 bg-purple-500/10 border border-purple-500/20 rounded-md hover:bg-purple-500/20 transition-colors justify-center"
        >
          <Plus className="w-4 h-4" />
          添加识别字段
        </button>
        <button
          onClick={() => toast.info('Skill 功能即将上线 (Coming Soon)')}
          className="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-400 bg-white/5 border border-white/10 rounded-md hover:bg-white/10 hover:text-purple-300 transition-colors"
          title="挂载可复用的 OCR 技能（Coming Soon）"
        >
          <Plus className="w-4 h-4" />
          Skill
        </button>
      </div>

      {/* Inline new field row (queues; stays open for multiple entries) */}
      {addingField && (
        <NewFieldRow
          onAdd={handleQueueField}
          onClose={() => setAddingField(false)}
        />
      )}

      {/* Pending fields + confirm button */}
      <PendingFieldsBar />

      {/* Basic info section */}
      <div className="bg-[#2a2a32] rounded-lg border border-white/5 overflow-hidden">
        <button
          onClick={() => toggle('basic')}
          className="w-full flex items-center justify-between p-3 bg-white/5 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-purple-500/20 flex items-center justify-center text-purple-400">
              <FileText className="w-3.5 h-3.5" />
            </div>
            <span className="text-sm font-medium text-gray-200">基本信息</span>
            <span className="text-xs text-gray-500 ml-2">{scalars.length} 字段</span>
          </div>
          {expanded.basic
            ? <ChevronDown className="w-4 h-4 text-gray-500" />
            : <ChevronRight className="w-4 h-4 text-gray-500" />}
        </button>

        {expanded.basic && (
          <div className="p-3 space-y-1">
            {scalars.length === 0 ? (
              <p className="text-xs text-gray-500 text-center py-4">暂无字段，等待文档处理完成</p>
            ) : (
              scalars.map((ann) => {
                // design v8: lookup drafts by current label OR by the
                // pre-rename old name (so cascade-rename doesn't make
                // the editor's draft association vanish).
                const oldName = renamesReverse[ann.label]
                const d = fieldEditDrafts[ann.label] ?? (oldName ? fieldEditDrafts[oldName] : undefined)
                const hasDirtyDraft = !!d && (
                  (d.originalName || '') !== d.correctedName ||
                  String(d.originalValue ?? '') !== d.correctedValue ||
                  (d.originalFormat || '') !== d.correctedFormat
                )
                return (
                  <FieldRow
                    key={ann.id}
                    annotation={ann}
                    locked={isFieldLocked(ann.label)}
                    result={resultMap.get(ann.id)}
                    isHovered={hoveredFieldId === ann.id}
                    isSelected={selectedFieldId === ann.id}
                    hasDirtyDraft={hasDirtyDraft}
                    editedOnOtherDocs={otherDocsEditedFields.has(ann.label)}
                    isRenamedFrom={oldName ?? null}
                    isValueModifiedHere={valueModifiedHere.has(ann.label)}
                    onHover={setHoveredFieldId}
                    onSelect={setSelectedFieldId}
                    onStartEdit={startEditingField}
                    onDeleteField={handleDeleteField}
                    onSaveLabel={handleSaveLabel}
                    onSaveValue={handleSaveValue}
                    onSaveType={handleSaveType}
                    onConfirmConfidence={handleConfirmConfidence}
                    onStartDrawing={handleStartDrawing}
                    isDrawingThis={drawingFieldId === ann.id}
                  />
                )
              })
            )}
          </div>
        )}
      </div>

      {/* Array tables (detailOfGoodsOrServices, detailOfTaxSummary, ...) */}
      {arrays.map((group) => (
        <ArrayTable
          key={group.arrayPath}
          group={group}
          hoveredFieldId={hoveredFieldId}
          selectedFieldId={selectedFieldId}
          fieldEditDrafts={fieldEditDrafts}
          onHover={setHoveredFieldId}
          onSelect={setSelectedFieldId}
          onStartEdit={startEditingField}
        />
      ))}

      {/* Phase 13 — fields the customer wants on every sample but the
          LLM did NOT emit on THIS doc. Inline form mirrors AddFieldList
          UX: type the actual value, OR tick "NONE" when this doc genuinely
          doesn't have this field. Saves a real Annotation row immediately. */}
      {missingRequiredFields.length > 0 && (
        <MissingFieldsList names={missingRequiredFields} docId={docId} />
      )}

      {/* Phase 11c — fields the customer has deleted from all samples.
          Kept as a small audit footer so the user can verify what was
          removed without cluttering the active field list. */}
      {(pendingEdits?.deleted_fields?.length ?? 0) > 0 && (
        <div className="bg-[#2a2a32] rounded-lg border border-red-500/20 overflow-hidden">
          <div className="flex items-center justify-between p-3 bg-red-500/10">
            <div className="flex items-center gap-2">
              <div className="w-6 h-6 rounded bg-red-500/20 flex items-center justify-center text-red-300">
                <Trash2 className="w-3.5 h-3.5" />
              </div>
              <span className="text-sm font-medium text-gray-300">已删除字段</span>
              <span className="text-xs text-gray-500 ml-2">{pendingEdits?.deleted_fields?.length ?? 0} 个</span>
            </div>
          </div>
          <div className="p-3 space-y-1">
            <p className="text-[11px] text-gray-500 leading-relaxed mb-1.5">
              下列字段在所有样本中已被永久删除，客户化时不再生成模块/反思/schema。
            </p>
            {(pendingEdits?.deleted_fields || []).map((name) => (
              <div
                key={name}
                className="flex items-center justify-between py-1 px-2 rounded bg-red-500/5 border border-red-500/15"
              >
                <span className="text-gray-400 text-sm font-mono line-through">{name}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-500/20 text-red-300 font-medium flex-shrink-0">
                  已删除
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 其他文件已新增字段 — fields added (committed) on the ApiDef that this
          doc has no value for yet. Same fillable interaction as the
          missing-fields list (input / 无此字段 / 保存 / 删除), per-doc value. */}
      {otherDocsAddedFieldNames.length > 0 && (
        <MissingFieldsList
          names={otherDocsAddedFieldNames}
          docId={docId}
          variant="added"
          title="其他文件已新增字段"
          countLabel="字段待补充"
          description="下列字段是你（或在其他样本上）新增的识别字段。本样本若也有此字段，请直接填写票面实际值；如本样本确无此字段，勾选“无此字段”。保存后即作为本样本该字段的值，删除则从所有样本中移除该新增字段。"
        />
      )}

      {/* Summary section */}
      <div className="bg-[#2a2a32] rounded-lg border border-white/5 overflow-hidden">
        <button
          onClick={() => toggle('summary')}
          className="w-full flex items-center justify-between p-3 bg-white/5 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-amber-500/20 flex items-center justify-center text-amber-400">
              <LinkIcon className="w-3.5 h-3.5" />
            </div>
            <span className="text-sm font-medium text-gray-200">汇总信息</span>
          </div>
          {expanded.summary
            ? <ChevronDown className="w-4 h-4 text-gray-500" />
            : <ChevronRight className="w-4 h-4 text-gray-500" />}
        </button>

        {expanded.summary && (
          <div className="p-3 divide-y divide-white/5">
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-gray-400">字段总数</span>
              <span className="text-gray-200">{annotations.length}</span>
            </div>
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-gray-400">平均置信度</span>
              <span className="text-emerald-400">
                {processingResults.length > 0
                  ? Math.round(processingResults.reduce((s, r) => s + r.confidence, 0) / processingResults.length)
                  : 0}%
              </span>
            </div>
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-gray-400">高置信字段</span>
              <span className="text-emerald-400">
                {processingResults.filter((r) => r.confidence >= 95).length}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-gray-400">需复核字段</span>
              <span className="text-red-400">
                {processingResults.filter((r) => r.confidence < 85).length}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-gray-400">手动添加</span>
              <span className="text-blue-400">
                {annotations.filter((a) => a.isManual).length}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Validation rules view ───────────────────────────────────────────────────

export function RulesView() {
  const { annotations, processingResults } = useWorkspaceStore()
  const resultMap = new Map(processingResults.map((r) => [r.annotationId, r]))

  const checks = annotations.map((ann) => {
    const r = resultMap.get(ann.id)
    const val = r?.value ?? ann.value
    const isEmpty = val === null || val === undefined || val === ''
    const isLowConf = (r?.confidence ?? 0) < 85
    const isNumericInvalid = ann.fieldType === 'number' && val != null && val !== '' && isNaN(Number(val))
    const status = isEmpty ? 'error' : isNumericInvalid ? 'error' : isLowConf ? 'warning' : 'ok'
    const message = isEmpty ? '值为空'
      : isNumericInvalid ? `值 "${val}" 不是有效数字`
      : isLowConf ? `置信度偏低 (${Math.round(r?.confidence ?? 0)}%)`
      : `置信度 ${Math.round(r?.confidence ?? 0)}%`
    return { id: ann.id, label: ann.label, status, message }
  })

  return (
    <div className="flex-1 overflow-auto p-4 space-y-3">
      <p className="text-xs text-gray-500">自动校验提取字段的完整性与置信度</p>
      {checks.length === 0 ? (
        <p className="text-xs text-gray-500 text-center py-8">暂无字段数据</p>
      ) : (
        checks.map((c) => (
          <div
            key={c.id}
            className={cn(
              'flex items-center gap-3 p-3 rounded-lg border text-sm',
              c.status === 'ok'      ? 'bg-emerald-500/5 border-emerald-500/20'
              : c.status === 'warning' ? 'bg-amber-500/5 border-amber-500/20'
              : 'bg-red-500/5 border-red-500/20',
            )}
          >
            {c.status === 'ok'
              ? <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
              : c.status === 'warning'
              ? <AlertCircle className="w-4 h-4 text-amber-400 flex-shrink-0" />
              : <AlertCircle className="w-4 h-4 text-red-400 flex-shrink-0" />}
            <div className="flex-1 min-w-0">
              <p className="text-gray-300 font-medium truncate">{c.label}</p>
              <p className="text-xs text-gray-500 truncate">{c.message}</p>
            </div>
          </div>
        ))
      )}
    </div>
  )
}

// ─── Stats view ──────────────────────────────────────────────────────────────

export function StatsView() {
  const { annotations, processingResults } = useWorkspaceStore()

  const total = processingResults.length
  const high = processingResults.filter((r) => r.confidence >= 95).length
  const med  = processingResults.filter((r) => r.confidence >= 85 && r.confidence < 95).length
  const low  = processingResults.filter((r) => r.confidence < 85).length
  const avg  = total > 0
    ? Math.round(processingResults.reduce((s, r) => s + r.confidence, 0) / total)
    : 0
  const confirmed = processingResults.filter((r) => r.confidence >= 100).length
  const manual = annotations.filter((a) => a.isManual).length

  const bars = [
    { label: '高置信 >=95%', count: high, color: 'bg-emerald-500', textColor: 'text-emerald-400' },
    { label: '中置信 85-95%', count: med,  color: 'bg-amber-500',   textColor: 'text-amber-400' },
    { label: '低置信 <85%',  count: low,  color: 'bg-red-500',     textColor: 'text-red-400' },
  ]

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      {/* KPI cards */}
      <div className="grid grid-cols-2 gap-3">
        {[
          { label: '总字段数', value: annotations.length, icon: List, color: 'text-blue-400' },
          { label: '平均置信度', value: `${avg}%`, icon: BarChart2, color: 'text-emerald-400' },
          { label: '已确认', value: confirmed, icon: CheckCircle2, color: 'text-purple-400' },
          { label: '手动添加', value: manual, icon: Plus, color: 'text-cyan-400' },
        ].map((kpi) => {
          const Icon = kpi.icon
          return (
            <div key={kpi.label} className="bg-[#2a2a32] rounded-lg p-4 border border-white/5">
              <div className="flex items-center gap-2 mb-2">
                <Icon className={cn('w-4 h-4', kpi.color)} />
                <span className="text-xs text-gray-500">{kpi.label}</span>
              </div>
              <p className={cn('text-2xl font-bold', kpi.color)}>{kpi.value}</p>
            </div>
          )
        })}
      </div>

      {/* Distribution */}
      <div className="bg-[#2a2a32] rounded-lg border border-white/5 p-4 space-y-3">
        <p className="text-xs font-medium text-gray-400 uppercase tracking-wide">置信度分布</p>
        {bars.map((b) => (
          <div key={b.label} className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">{b.label}</span>
              <span className={b.textColor}>{b.count} 字段</span>
            </div>
            <div className="h-2 bg-white/10 rounded-full overflow-hidden">
              <div
                className={cn('h-full rounded-full transition-all', b.color)}
                style={{ width: total > 0 ? `${(b.count / total) * 100}%` : '0%' }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Main component ──────────────────────────────────────────────────────────
