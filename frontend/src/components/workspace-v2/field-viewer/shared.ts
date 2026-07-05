// 结构第二轮 B2：DarkFieldViewer 拆分——共享常量 / 类型 / 纯函数。
// 从 DarkFieldViewer.tsx 顶部原样搬出，被 rows / panels / views 各文件引用。
import { type Annotation, type ProcessingResult } from '../../../stores/workspace-store'

export const FORMAT_OPTIONS = ['string', 'number', 'date', 'boolean', 'array'] as const

export const LOW_CONFIDENCE_THRESHOLD = 85

export type ViewTab = 'fields' | 'rules' | 'stats'

export const FIELD_TYPES = ['text', 'number', 'date', 'boolean', 'array'] as const

// ─── Array detection ────────────────────────────────────────────────────────
//
// The store flattens nested structured_data into labels like
// `detailOfGoodsOrServices[0].articleName`. To render the array as a table we
// match this pattern and bucket annotations by their array path + index.

export const ARRAY_LABEL_RE = /^(.*?)\[(\d+)\](?:\.(.+))?$/

export interface ArrayCell {
  annotation: Annotation
  result?: ProcessingResult
}

export interface ArrayGroup {
  /** Path of the array itself, e.g. "detailOfGoodsOrServices" or "items[0].nested" */
  arrayPath: string
  /** Column keys in first-appearance order across all rows */
  columns: string[]
  /** Row index → column key → cell */
  rows: Map<number, Map<string, ArrayCell>>
}

export function groupAnnotations(
  annotations: Annotation[],
  resultMap: Map<string, ProcessingResult>,
): { scalars: Annotation[]; arrays: ArrayGroup[] } {
  const scalars: Annotation[] = []
  const arraysByPath = new Map<string, ArrayGroup>()

  for (const ann of annotations) {
    const m = ann.label.match(ARRAY_LABEL_RE)
    if (!m) {
      scalars.push(ann)
      continue
    }
    const [, arrayPath, idxStr, fieldName] = m
    const idx = Number(idxStr)
    const colKey = fieldName ?? '(value)'

    let group = arraysByPath.get(arrayPath)
    if (!group) {
      group = { arrayPath, columns: [], rows: new Map() }
      arraysByPath.set(arrayPath, group)
    }
    if (!group.columns.includes(colKey)) group.columns.push(colKey)

    let row = group.rows.get(idx)
    if (!row) {
      row = new Map()
      group.rows.set(idx, row)
    }
    row.set(colKey, { annotation: ann, result: resultMap.get(ann.id) })
  }

  return { scalars, arrays: Array.from(arraysByPath.values()) }
}
