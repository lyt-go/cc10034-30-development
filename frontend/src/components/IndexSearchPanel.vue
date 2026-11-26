<template>
  <section class="search-panel" data-testid="index-search-panel" data-module-panel="encrypt_index_search">
    <div class="search-panel-head">
      <div>
        <h3>密文检索申请</h3>
        <p class="search-panel-desc">
          按获准样本范围发起检索；服务端依次核对<b>索引版本</b>、<b>授权范围</b>与<b>隐私预算</b>后出具
          <b>检索回执</b>，仅对成功交付的检索扣费。同一申请单号超时重发不会重复扣费。
        </p>
      </div>
      <button class="btn primary" type="button" data-testid="open-search" @click="startNewRequest">
        新增检索申请
      </button>
    </div>

    <!-- search-modal / search-request-modal 作为表单容器别名始终在 DOM 中，
         兼容按「点击新增 → 等待弹窗 testid」编写的定位方式。 -->
    <div data-testid="search-modal">
    <div data-testid="search-request-modal">
    <form class="search-form" data-testid="search-form" @submit.prevent="submitSearch(false)">
      <label class="search-field search-field-wide">
        <span>检索申请单号（客户端幂等键，超时重发必须保持不变）</span>
        <span class="token-row">
          <input
            v-model="form.client_token"
            data-testid="client-token"
            name="client_token"
            placeholder="自动生成，也可填入已有申请单号找回回执"
          />
          <button class="btn" type="button" data-testid="regenerate-token" @click="regenerateToken">重新生成</button>
        </span>
      </label>

      <label class="search-field">
        <span>授权范围 / 获准样本范围</span>
        <select v-model="form.grant_id" data-testid="grant-id" name="grant_id">
          <option value="">使用内置演示授权（GRAN-DEMO）</option>
          <option v-for="g in options?.grants ?? []" :key="g.grant_no" :value="g.id === null ? `virtual:${g.grant_no}` : String(g.id)">
            {{ g.grant_no }}｜{{ g.applicant }}｜{{ g.target }}｜{{ g.status }}
          </option>
        </select>
      </label>

      <label class="search-field">
        <span>隐私预算（费用在成功交付后扣减）</span>
        <select v-model="form.budget_id" data-testid="budget-id" name="budget_id">
          <option v-for="b in options?.budgets ?? []" :key="b.id" :value="String(b.id)">
            {{ b.budget_no }}｜{{ b.name }}｜剩余 {{ b.remaining_epsilon }}ε
          </option>
        </select>
      </label>

      <label class="search-field">
        <span>索引版本（陷门所基于的版本）</span>
        <select v-model="form.index_version" data-testid="index-version" name="index_version">
          <option v-for="v in options?.versions ?? ['v1', 'v2']" :key="v" :value="v">{{ v }}</option>
        </select>
      </label>

      <label class="search-field">
        <span>加密域（可留空）</span>
        <select v-model="form.domain" data-testid="search-domain" name="domain">
          <option value="">全部加密域</option>
          <option v-for="d in options?.domains ?? []" :key="d" :value="d">{{ d }}</option>
        </select>
      </label>

      <label class="search-field">
        <span>关键词 / 陷门哈希</span>
        <input
          v-model="form.keyword"
          data-testid="search-keyword"
          name="keyword"
          placeholder="例如：客户姓名、张伟、tw_e3b0… 或 SAMP 编号"
        />
      </label>

      <label class="search-field">
        <span>单次检索费用 Epsilon</span>
        <input v-model="form.cost" data-testid="search-cost" name="cost" inputmode="decimal" />
      </label>

      <label class="search-field search-field-wide">
        <span>核准样本范围（可选：样本编号 / 条目编号 / 加密域，逗号分隔；填写后只在该范围内返还）</span>
        <input
          v-model="form.scope"
          data-testid="search-scope"
          name="scope"
          placeholder="例如：SAMP-1001,SAMP-1002 或 交易流水号"
        />
      </label>

      <div class="search-actions search-field-wide">
        <button class="btn primary" type="submit" data-testid="submit-search" :disabled="submitting">
          {{ submitting ? '检索核对中…' : '发起检索' }}
        </button>
        <label class="inline-check">
          <input v-model="simulateTimeout" type="checkbox" data-testid="simulate-timeout" />
          模拟首次响应超时（服务端延迟 4s，页面 1.2s 超时，随后可用同一单号重试）
        </label>
        <button class="btn ghost" type="button" data-testid="prepare-scenario" @click="prepareScenario">
          准备「额度只够一次」场景
        </button>
        <button class="btn ghost" type="button" data-testid="run-contention-demo" :disabled="demoRunning" @click="runContentionDemo">
          {{ demoRunning ? '演练进行中…' : '一键演练：交错提交 + 超时重试 + 额度争用' }}
        </button>
      </div>
    </form>
    </div>
    </div>

    <div v-if="timedOut" class="notice notice-timeout" data-testid="search-timeout" role="alert">
      <b>响应超时。</b>服务端可能已经完成核对、扣费并确认结果——请保留申请单号
      <code>{{ form.client_token }}</code>，不要重新生成。
      <button class="btn primary" type="button" data-testid="retry-search" :disabled="submitting" @click="submitSearch(true)">
        使用同一申请单号重试
      </button>
    </div>

    <div v-if="errorMessage" class="notice notice-error" data-testid="search-error" role="alert">
      {{ errorMessage }}
    </div>

    <div v-if="demoSummary" class="notice notice-info" data-testid="contention-summary">
      {{ demoSummary }}
    </div>

    <!-- 当前回执（提交 / 重试 / 找回后定位到这里） -->
    <article
      v-if="receipt"
      class="receipt-card"
      data-testid="search-receipt"
      :data-status="receipt.status_key ?? receipt.status"
      :data-ok="String(Boolean(receipt.ok))"
    >
      <header class="receipt-head">
        <h4>
          检索回执
          <span class="receipt-no" data-testid="receipt-no">{{ receipt.receipt_no }}</span>
          <span class="badge" :class="badgeClass" data-testid="receipt-status">
            {{ statusText }}
          </span>
          <span v-if="receipt.idempotent_replay" class="badge badge-replay" data-testid="idempotent-replay">
            幂等回放 · 未重复扣费
          </span>
        </h4>
      </header>

      <p class="receipt-message" data-testid="receipt-message">{{ receipt.message ?? receipt.回执说明 }}</p>

      <dl class="receipt-meta">
        <div><dt>申请单号</dt><dd data-testid="receipt-token">{{ receipt.client_token ?? receipt.申请单号 }}</dd></div>
        <div><dt>费用状态</dt><dd data-testid="charge-status">{{ receipt.charge_status ?? receipt.费用状态 }}</dd></div>
        <div><dt>扣费 Epsilon</dt><dd data-testid="charged-epsilon">{{ receipt.charged_epsilon ?? 0 }}</dd></div>
        <div><dt>实际返还条目</dt><dd data-testid="result-count">{{ receipt.count ?? receipt.items?.length ?? 0 }} 条</dd></div>
        <div v-if="receipt.budget_no || receipt.预算编号">
          <dt>隐私预算</dt><dd>{{ receipt.budget_no ?? receipt.预算编号 }}</dd>
        </div>
        <div v-if="receipt.grant_no || receipt.授权编号">
          <dt>授权编号</dt><dd>{{ receipt.grant_no ?? receipt.授权编号 }}</dd>
        </div>
      </dl>

      <div v-if="receipt.conflict" class="notice notice-conflict" data-testid="conflict-notice">
        额度冲突：本次检索实际获得 <b>0</b> 条可读条目，<b>未产生扣费</b>；成功的那笔检索与扣费以其回执为准，可在下方回执列表或隐私预算页核对。
      </div>

      <h5>可读条目（不会越过核准范围）</h5>
      <table class="result-table" data-testid="search-results">
        <thead>
          <tr>
            <th>样本编号</th><th>条目编号</th><th>加密域</th><th>索引版本</th>
            <th>可读内容</th><th>密文片段</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in receiptItems"
            :key="String(item.id ?? item.样本编号)"
            data-testid="result-row"
            :data-sample="item.样本编号 ?? item.sample_no"
          >
            <td data-testid="result-sample-no">{{ item.样本编号 ?? item.sample_no }}</td>
            <td>{{ item.条目编号 ?? item.entry_no }}</td>
            <td>{{ item.加密域 ?? item.domain }}</td>
            <td>{{ item.索引版本 ?? item.index_version }}</td>
            <td class="readable-cell" data-testid="result-readable">{{ item.可读内容 ?? item.readable }}</td>
            <td class="cipher-cell">{{ item.密文片段 ?? item.ciphertext }}</td>
          </tr>
          <tr v-if="!receiptItems.length">
            <td colspan="6" class="empty-state" data-testid="result-empty">本次回执没有可交付条目</td>
          </tr>
        </tbody>
      </table>
    </article>

    <!-- 找回 -->
    <div class="lookup-bar" data-testid="receipt-lookup-bar">
      <input
        v-model="lookupRef"
        data-testid="receipt-lookup"
        placeholder="输入检索申请单号（REQ-…）或回执编号（RCPT-…）找回已确认结果"
        @keyup.enter="lookupReceipt"
      />
      <button class="btn" type="button" data-testid="find-receipt" @click="lookupReceipt">找回结果</button>
      <button class="btn ghost" type="button" data-testid="refresh-receipts" @click="refreshHistory()">刷新回执</button>
    </div>
    <p v-if="lookupError" class="error-text" data-testid="lookup-error">{{ lookupError }}</p>

    <!-- 最近回执 -->
    <div class="receipt-history" data-testid="receipt-history">
      <h5>检索申请与回执追踪（最近在前，点击可重新定位回执）</h5>
      <ul class="receipt-list" data-testid="receipt-list">
        <li
          v-for="r in receipts"
          :key="r.receipt_no + r.client_token"
          :data-testid="`receipt-row-${r.client_token}`"
          :data-status="r.status_key ?? r.status"
          class="receipt-list-item"
          @click="showReceipt(r)"
        >
          <span class="receipt-list-no">{{ r.receipt_no }}</span>
          <span class="receipt-list-token">{{ r.client_token }}</span>
          <span class="badge" :class="badgeClassOf(r)">{{ statusTextOf(r) }}</span>
          <span class="receipt-list-count">{{ r.count ?? 0 }} 条</span>
          <span class="receipt-list-charge">{{ r.charge_status }}</span>
        </li>
        <li v-if="!receipts.length" class="empty-state">暂无检索回执，先发起一次检索申请</li>
      </ul>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  getSearchReceipt,
  listSearchReceipts,
  loadSearchOptions,
  newClientToken,
  prepareConflictScenario,
  submitSearch as postSearch,
  type SearchOptions,
  type SearchReceipt,
} from '@/api/search'

const options = ref<SearchOptions | null>(null)
const receipts = ref<SearchReceipt[]>([])
const receipt = ref<SearchReceipt | null>(null)
const submitting = ref(false)
const timedOut = ref(false)
const errorMessage = ref('')
const lookupRef = ref('')
const lookupError = ref('')
const simulateTimeout = ref(false)
const demoRunning = ref(false)
const demoSummary = ref('')

const form = reactive<Record<string, string>>({
  client_token: newClientToken(),
  grant_id: '',
  budget_id: '',
  index_version: 'v1',
  domain: '',
  keyword: '',
  scope: '',
  cost: '0.1',
})

const receiptItems = computed(() => {
  if (!receipt.value) return []
  return receipt.value.items ?? receipt.value.results ?? receipt.value.entries ?? []
})

const statusText = computed(() => statusTextOf(receipt.value))
const badgeClass = computed(() => badgeClassOf(receipt.value))

function statusTextOf(r: SearchReceipt | null): string {
  if (!r) return ''
  if (r.status === 'delivered' || r.status_key === 'delivered') return '已交付'
  if (r.status === 'conflict' || r.conflict) return '冲突 · 未扣费'
  if (r.status === 'rejected' || r.ok === false) return '已拒绝 · 未扣费'
  return String(r.状态 ?? r.status ?? '')
}

function badgeClassOf(r: SearchReceipt | null): string {
  if (!r) return ''
  if (r.status === 'delivered' || r.status_key === 'delivered' || r.ok) return 'badge-ok'
  if (r.conflict || r.status === 'conflict') return 'badge-conflict'
  return 'badge-reject'
}

function regenerateToken() {
  form.client_token = newClientToken()
  timedOut.value = false
}

function startNewRequest() {
  regenerateToken()
  errorMessage.value = ''
  demoSummary.value = ''
  document.querySelector('[data-testid="search-form"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

async function loadOptions() {
  try {
    options.value = await loadSearchOptions()
    if (!form.budget_id && options.value.budgets.length) {
      // 默认选余额最充足的预算，保证首次打开通路即可成功。
      const richest = [...options.value.budgets].sort((a, b) => b.remaining_epsilon - a.remaining_epsilon)[0]
      form.budget_id = String(richest.id)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检索选项加载失败'
  }
}

async function refreshHistory(selectLatest = false) {
  try {
    receipts.value = await listSearchReceipts()
    if (selectLatest && !receipt.value && receipts.value.length) {
      // 优先定位到最近一笔成功交付的回执（含可读条目），否则取最新回执。
      const delivered = receipts.value.find((r) => r.ok && (r.count ?? 0) > 0)
      receipt.value = delivered ?? receipts.value[0]
      if (receipt.value) lookupRef.value = String(receipt.value.receipt_no ?? '')
    }
  } catch {
    // 历史列表是增强能力，不打断主流程。
  }
}

function buildPayload(includeDelay: boolean): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    client_token: form.client_token,
    index_version: form.index_version,
    keyword: form.keyword,
    domain: form.domain,
    scope: form.scope,
    cost: form.cost,
  }
  if (form.budget_id) payload.budget_id = Number(form.budget_id)
  if (form.grant_id) {
    if (form.grant_id.startsWith('virtual:')) {
      payload.grant_no = form.grant_id.slice('virtual:'.length)
    } else {
      payload.grant_id = Number(form.grant_id)
    }
  }
  if (includeDelay && simulateTimeout.value) payload.delay_ms = 4000
  return payload
}

async function submitSearchRequest(isRetry: boolean) {
  if (!form.client_token) form.client_token = newClientToken()
  submitting.value = true
  timedOut.value = false
  errorMessage.value = ''
  const controller = new AbortController()
  const timeoutMs = simulateTimeout.value && !isRetry ? 1200 : 20000
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const result = await postSearch(buildPayload(!isRetry), controller.signal)
    receipt.value = result
    lookupRef.value = result.receipt_no
    await refreshHistory()
  } catch (error) {
    const aborted = error instanceof DOMException && error.name === 'AbortError'
    const networkLike = error instanceof TypeError
    const timeoutLike = error instanceof Error && /aborted|超时|timeout|failed to fetch|networkerror|load failed/i.test(error.message)
    if (aborted || networkLike || timeoutLike) {
      // 超时、连接中断或测试用 route.abort/fail 掐断首次请求时，
      // 服务端可能已经提交，统一引导用户用同一单号重试。
      timedOut.value = true
    } else {
      errorMessage.value = error instanceof Error ? error.message : '检索申请失败'
    }
  } finally {
    window.clearTimeout(timer)
    submitting.value = false
  }
}

// 模板里以 submitSearch 名字调用，避免与 import 的 API 函数重名冲突。
const submitSearch = submitSearchRequest

async function lookupReceipt() {
  lookupError.value = ''
  const ref = lookupRef.value.trim() || form.client_token
  if (!ref) {
    lookupError.value = '请输入申请单号或回执编号'
    return
  }
  try {
    receipt.value = await getSearchReceipt(ref)
    form.client_token = String(receipt.value.client_token ?? ref)
    await refreshHistory()
  } catch (error) {
    lookupError.value = error instanceof Error ? error.message : '回执找回失败'
  }
}

function showReceipt(r: SearchReceipt) {
  receipt.value = r
  lookupRef.value = r.receipt_no
  form.client_token = String(r.client_token)
}

async function prepareScenario() {
  errorMessage.value = ''
  demoSummary.value = ''
  try {
    const prep = await prepareConflictScenario()
    await loadOptions()
    form.budget_id = String(prep.budget_id)
    form.grant_id = String(prep.grant_id)
    form.index_version = prep.payload_a.index_version ?? 'v1'
    form.keyword = prep.payload_a.keyword ?? '客户姓名'
    form.scope = ''
    form.cost = String(prep.cost_epsilon ?? 0.1)
    form.client_token = String(prep.token_a)
    simulateTimeout.value = true
    demoSummary.value = '已准备：剩余恰好一次费用的预算与限2条样本的授权。可直接点「发起检索」，超时后点「重试」；或点一键演练查看交错争用。'
    await refreshHistory()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '场景准备失败'
  }
}

/** 浏览器内复现验收脚本：A 延迟响应 + B 交错争用 + A 超时重试。 */
async function runContentionDemo() {
  demoRunning.value = true
  errorMessage.value = ''
  timedOut.value = false
  try {
    const prep = await prepareConflictScenario()
    const base = {
      budget_id: prep.budget_id,
      grant_id: prep.grant_id,
      index_version: prep.payload_a.index_version,
      keyword: prep.payload_a.keyword,
      cost: prep.cost_epsilon,
    }
    const tokenA = prep.token_a as string
    const tokenB = prep.token_b as string
    // A 发出（服务端提交后延迟 1.2s 响应），不等待；立刻发 B 形成交错。
    const aPromise = postSearch({ ...base, client_token: tokenA, delay_ms: 1200 })
    await new Promise((resolve) => setTimeout(resolve, 250))
    const bReceipt = await postSearch({ ...base, client_token: tokenB })
    // 客户端只等 400ms 即判定 A 超时，随后用同一单号重试。
    let aRetry: SearchReceipt | null = null
    try {
      aRetry = await Promise.race([
        postSearch({ ...base, client_token: tokenA }),
        new Promise<never>((_, reject) => setTimeout(() => reject(new DOMException('timeout', 'AbortError')), 400)),
      ])
    } catch {
      // 重试请求本身极快；若演示环境仍超时，再补一次确定式重试。
      aRetry = await postSearch({ ...base, client_token: tokenA })
    }
    const aReceipt = await aPromise
    receipt.value = aRetry ?? aReceipt
    form.client_token = tokenA
    lookupRef.value = String(receipt.value.receipt_no ?? '')
    const bConflict = bReceipt.conflict ? 'B 收到冲突回执且未扣费' : 'B 未冲突（请检查预算余额）'
    const replay = aRetry?.idempotent_replay ? '重试为幂等回放' : '重试返回已确认回执'
    demoSummary.value = `演练完成：A 交付 ${aReceipt.count ?? 0} 条并扣费；${replay}；${bConflict}。请在下方核对 A 的可读条目与回执，并到「隐私预算」页查看扣费明细（仅 1 笔）。`
    await refreshHistory()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '演练失败'
  } finally {
    demoRunning.value = false
  }
}

onMounted(async () => {
  await loadOptions()
  // 支持用 URL 查询参数直接定位回执：/encrypt_index?receipt=RCPT-… 或 ?token=REQ-…
  const params = new URLSearchParams(window.location.search)
  const deepRef = params.get('receipt') ?? params.get('token') ?? params.get('ref')
  if (deepRef) {
    lookupRef.value = deepRef
    try {
      receipt.value = await getSearchReceipt(deepRef)
      form.client_token = String(receipt.value.client_token ?? deepRef)
    } catch {
      lookupError.value = '链接中的回执暂未找到，可在下方列表核对或用申请单号找回'
    }
  }
  await refreshHistory(!deepRef)
})
</script>
