import { useEffect, useState } from 'react'
import { X, Copy, Check, Key, Code2, Loader2, AlertCircle } from 'lucide-react'
import { fetchApiDocs, createApiKey, type ApiDocs } from '../../lib/api-client'
import { toast } from '../../lib/toast'

interface ApiUsageModalProps {
  apiDefId: string
  apiCode: string
  apiName: string
  onClose: () => void
}

type Lang = 'curl' | 'python' | 'javascript'

/** 已发布 API 的「调用引导」面板：拿 Key → 调用示例（多语言）→ 返回字段。
 *  目标：用户/客户在产品内自助完成首次调用，无需人工演示。 */
export default function ApiUsageModal({ apiDefId, apiCode, apiName, onClose }: ApiUsageModalProps) {
  const [docs, setDocs] = useState<ApiDocs | null>(null)
  const [loading, setLoading] = useState(true)
  const [lang, setLang] = useState<Lang>('curl')
  const [copied, setCopied] = useState<string | null>(null)
  const [creatingKey, setCreatingKey] = useState(false)
  const [rawKey, setRawKey] = useState<string | null>(null)

  const origin = window.location.origin
  const endpoint = `${origin}/api/v1/extract/${apiCode}`
  // 已生成 key 则填进示例，否则用占位符引导用户替换
  const keyShown = rawKey || '<YOUR_API_KEY>'

  useEffect(() => {
    let cancelled = false
    fetchApiDocs(apiDefId)
      .then((res) => { if (!cancelled) setDocs(res.data) })
      .catch(() => { if (!cancelled) toast.error('加载调用文档失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [apiDefId])

  async function handleGenerateKey() {
    setCreatingKey(true)
    try {
      const res = await createApiKey({ name: `${apiCode}-key` })
      const k = (res.data?.key ?? res.data?.raw_key) as string
      if (!k) throw new Error('no key')
      setRawKey(k)
      toast.success('已生成 Key —— 请立即复制保存，关闭后无法再次查看')
    } catch {
      toast.error('生成 Key 失败')
    } finally {
      setCreatingKey(false)
    }
  }

  function copy(text: string, tag: string) {
    navigator.clipboard.writeText(text)
    setCopied(tag)
    setTimeout(() => setCopied((c) => (c === tag ? null : c)), 1500)
  }

  const samples: Record<Lang, string> = {
    curl: `curl -X POST "${endpoint}" \\
  -H "X-API-Key: ${keyShown}" \\
  -F "file=@/path/to/invoice.pdf"`,
    python: `import requests

resp = requests.post(
    "${endpoint}",
    headers={"X-API-Key": "${keyShown}"},
    files={"file": open("invoice.pdf", "rb")},
    timeout=120,
)
print(resp.json()["data"])`,
    javascript: `const form = new FormData();
form.append("file", fileInput.files[0]);

const resp = await fetch("${endpoint}", {
  method: "POST",
  headers: { "X-API-Key": "${keyShown}" },
  body: form,
});
const { data } = await resp.json();
console.log(data);`,
  }

  const LANGS: { key: Lang; label: string }[] = [
    { key: 'curl', label: 'cURL' },
    { key: 'python', label: 'Python' },
    { key: 'javascript', label: 'JavaScript' },
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[88vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <div>
            <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2">
              <Code2 className="w-4 h-4 text-indigo-500" /> 调用 {apiName}
            </h2>
            <code className="text-xs font-mono text-gray-400">{apiCode}</code>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100">
            <X className="w-5 h-5" />
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-24">
            <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
          </div>
        ) : (
          <div className="overflow-y-auto px-6 py-5 space-y-6">
            {/* 端点 */}
            <section>
              <div className="flex items-center gap-2 mb-2">
                <span className="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 text-xs font-bold">POST</span>
                <code className="flex-1 text-xs font-mono bg-gray-50 border border-gray-200 rounded px-2 py-1.5 truncate">{endpoint}</code>
                <button onClick={() => copy(endpoint, 'ep')} className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50" title="复制">
                  {copied === 'ep' ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
              <p className="text-xs text-gray-500">认证：请求头 <code className="text-indigo-600">X-API-Key</code>　·　文件：multipart <code className="text-indigo-600">file</code> 字段（或 JSON <code>file_url</code> / <code>file_base64</code>）</p>
            </section>

            {/* Step 1 拿 Key */}
            <section>
              <h3 className="text-xs font-bold text-gray-700 mb-2 flex items-center gap-1.5"><Key className="w-3.5 h-3.5 text-amber-500" /> 第 1 步 · 获取 API Key</h3>
              {rawKey ? (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                  <p className="text-[11px] text-amber-700 mb-1.5 flex items-center gap-1"><AlertCircle className="w-3 h-3" /> 立即复制保存，关闭后无法再次查看：</p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 text-xs font-mono bg-white border border-amber-300 rounded px-2 py-1.5 break-all">{rawKey}</code>
                    <button onClick={() => copy(rawKey, 'key')} className="p-1.5 rounded text-amber-600 hover:bg-amber-100" title="复制">
                      {copied === 'key' ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-3">
                  <button
                    onClick={handleGenerateKey}
                    disabled={creatingKey}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-xs font-medium"
                  >
                    {creatingKey ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Key className="w-3.5 h-3.5" />}
                    生成新 Key
                  </button>
                  <span className="text-xs text-gray-400">已有 Key 可直接在下方示例替换 <code>&lt;YOUR_API_KEY&gt;</code></span>
                </div>
              )}
            </section>

            {/* Step 2 调用示例 */}
            <section>
              <h3 className="text-xs font-bold text-gray-700 mb-2 flex items-center gap-1.5"><Code2 className="w-3.5 h-3.5 text-indigo-500" /> 第 2 步 · 调用示例</h3>
              <div className="flex items-center gap-1 mb-2">
                {LANGS.map((l) => (
                  <button
                    key={l.key}
                    onClick={() => setLang(l.key)}
                    className={[
                      'px-2.5 py-1 text-xs rounded-md font-medium transition-colors',
                      lang === l.key ? 'bg-indigo-100 text-indigo-700' : 'text-gray-500 hover:bg-gray-100',
                    ].join(' ')}
                  >
                    {l.label}
                  </button>
                ))}
                <button onClick={() => copy(samples[lang], 'code')} className="ml-auto inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md text-gray-500 hover:text-indigo-600 hover:bg-indigo-50">
                  {copied === 'code' ? <><Check className="w-3.5 h-3.5 text-emerald-500" /> 已复制</> : <><Copy className="w-3.5 h-3.5" /> 复制</>}
                </button>
              </div>
              <pre className="text-[11px] leading-relaxed bg-gray-900 text-gray-100 rounded-lg p-3 overflow-x-auto font-mono whitespace-pre">{samples[lang]}</pre>
            </section>

            {/* 返回字段 */}
            {docs && docs.fields.length > 0 && (
              <section>
                <h3 className="text-xs font-bold text-gray-700 mb-2">返回字段（<code className="text-gray-500">data</code> 下，共 {docs.fields.length} 项）</h3>
                <div className="border border-gray-200 rounded-lg overflow-hidden">
                  <table className="w-full text-xs">
                    <tbody className="divide-y divide-gray-100">
                      {docs.fields.map((f) => (
                        <tr key={f.name} className="hover:bg-gray-50">
                          <td className="px-3 py-1.5 font-mono text-gray-800 align-top w-1/2">
                            {f.name}
                            {f.children && f.children.length > 0 && (
                              <div className="mt-1 pl-3 border-l-2 border-gray-100 space-y-0.5">
                                {f.children.map((c) => (
                                  <div key={c.name} className="text-[10px] text-gray-500">
                                    {c.name} <span className="text-gray-300">· {c.type}</span>
                                  </div>
                                ))}
                              </div>
                            )}
                          </td>
                          <td className="px-3 py-1.5 text-gray-400 align-top">{f.type}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* 错误码 */}
            {docs && docs.error_codes.length > 0 && (
              <section>
                <h3 className="text-xs font-bold text-gray-700 mb-2">错误码</h3>
                <div className="flex flex-wrap gap-1.5">
                  {docs.error_codes.map((e) => (
                    <span key={e.code} className="text-[10px] bg-gray-50 border border-gray-200 rounded px-1.5 py-0.5 text-gray-500">
                      <b className="text-gray-700">{e.http}</b> {e.description}
                    </span>
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
