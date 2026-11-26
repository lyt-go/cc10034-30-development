/** 密文检索申请、检索回执与扣费明细的数据访问层。 */

export type SearchItem = {
  id?: number
  sample_no?: string
  样本编号?: string
  entry_no?: string
  条目编号?: string
  domain?: string
  加密域?: string
  index_version?: string
  索引版本?: string
  readable?: string
  可读内容?: string
  ciphertext?: string
  密文片段?: string
  [key: string]: unknown
}

export type SearchReceipt = {
  id?: number
  receipt_no: string
  回执编号?: string
  client_token: string
  申请单号?: string
  ok: boolean
  delivered?: boolean
  conflict?: boolean
  status: string
  status_key?: string
  状态?: string
  code?: string
  message: string
  回执说明?: string
  charge_status?: string
  费用状态?: string
  charged?: boolean
  charged_epsilon?: number
  cost_epsilon?: number
  count?: number
  items?: SearchItem[]
  results?: SearchItem[]
  entries?: SearchItem[]
  idempotent_replay?: boolean
  budget_no?: string
  预算编号?: string
  grant_no?: string
  授权编号?: string
  scope?: string[]
  created_at?: string
  [key: string]: unknown
}

export type ChargeRecord = {
  id: number
  charge_no?: string
  扣费编号?: string
  receipt_no: string
  回执编号?: string
  client_token?: string
  budget_id?: number
  budget_no?: string
  预算编号?: string
  amount_epsilon?: number
  charged_epsilon?: number
  扣费Epsilon?: number
  status?: string
  状态?: string
  created_at?: string
  [key: string]: unknown
}

export type SearchOptions = {
  current_index_version: string
  versions: string[]
  default_cost_epsilon: number
  domains: string[]
  grants: Array<{
    id: number | null
    grant_no: string
    applicant: string
    target: string
    status: string
    approved: boolean
    scope: string[]
    scope_text: string
  }>
  budgets: Array<{
    id: number
    budget_no: string
    name: string
    status: string
    total_epsilon: number
    consumed_epsilon: number
    remaining_epsilon: number
    search_cost_epsilon: number
  }>
}

async function parseJsonOrThrow(response: Response): Promise<any> {
  const text = await response.text()
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`接口返回 ${response.status}，响应不是合法 JSON`)
  }
}

/** 提交检索申请。保留原始 fetch 以便用 AbortController 模拟/处理客户端超时。 */
export async function submitSearch(
  payload: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<SearchReceipt> {
  const response = await fetch('/api/encrypt_index/searches', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  const body = await parseJsonOrThrow(response)
  // 即使 HTTP 层返回错误状态，也尽量把回执体交给页面展示。
  if (body && typeof body === 'object' && 'receipt_no' in body) return body as SearchReceipt
  if (body?.receipt) return body.receipt as SearchReceipt
  if (!response.ok) throw new Error(body?.message ?? `检索申请失败（${response.status}）`)
  return body as SearchReceipt
}

export async function getSearchReceipt(ref: string): Promise<SearchReceipt> {
  const response = await fetch(`/api/encrypt_index/searches/${encodeURIComponent(ref)}`)
  const body = await parseJsonOrThrow(response)
  if (!response.ok) throw new Error(body?.message ?? '回执不存在')
  return (body?.receipt ?? body) as SearchReceipt
}

export async function listSearchReceipts(): Promise<SearchReceipt[]> {
  const response = await fetch('/api/encrypt_index/searches')
  if (!response.ok) throw new Error('检索回执列表读取失败')
  const body = await response.json()
  return (body.items ?? body.receipts ?? []) as SearchReceipt[]
}

export async function listBudgetCharges(): Promise<ChargeRecord[]> {
  const response = await fetch('/api/privacy_budget/charges')
  if (!response.ok) throw new Error('扣费明细读取失败')
  const body = await response.json()
  return (body.items ?? body.charges ?? []) as ChargeRecord[]
}

export async function loadSearchOptions(): Promise<SearchOptions> {
  const response = await fetch('/api/index_search/options')
  if (!response.ok) throw new Error('检索选项读取失败')
  return (await response.json()) as SearchOptions
}

export async function prepareConflictScenario(): Promise<Record<string, any>> {
  const response = await fetch('/api/index_search/scenario/prepare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  if (!response.ok) throw new Error('验收场景准备失败')
  return await response.json()
}

export function newClientToken(): string {
  const rand =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replace(/-/g, '').slice(0, 12)
      : Math.random().toString(36).slice(2, 14)
  return `REQ-WEB-${rand}`
}
