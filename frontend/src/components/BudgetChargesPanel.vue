<template>
  <div data-testid="budget-charges">
  <section
    class="charge-panel"
    data-testid="budget-charge-panel"
    data-module-panel="privacy_budget_charges"
  >
    <div class="charge-head">
      <div>
        <h3>检索扣费明细</h3>
        <p class="search-panel-desc">
          仅对状态为「已交付」的检索回执扣费；超时重发命中同一回执不重复扣费，额度冲突/校验拒绝不扣费。
        </p>
      </div>
      <button class="btn" type="button" data-testid="refresh-charges" @click="loadCharges">刷新扣费明细</button>
    </div>

    <div class="charge-summary">
      <span data-testid="charge-count">扣费笔数：{{ charges.length }}</span>
      <span data-testid="charge-total">累计成功交付费用：{{ totalCharged }} ε</span>
    </div>
    <p v-if="loadError" class="error-text" data-testid="charge-error">{{ loadError }}</p>

    <div data-testid="charge-list" class="charge-table-wrap">
      <div data-testid="deduction-detail">
      <table class="charge-table" data-testid="charge-table">
        <thead>
          <tr>
            <th>扣费编号</th>
            <th>检索回执</th>
            <th>检索申请单号</th>
            <th>隐私预算</th>
            <th>扣费 Epsilon</th>
            <th>费用状态</th>
            <th>交付条目</th>
            <th>扣费时间</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="c in charges"
            :key="String(c.id)"
            :data-testid="`charge-row-${c.id}`"
            :data-receipt="c.receipt_no"
          >
            <td data-testid="charge-no">{{ c.charge_no ?? c.扣费编号 }}</td>
            <td data-testid="charge-receipt-no">{{ c.receipt_no ?? c.回执编号 }}</td>
            <td class="token-cell">{{ c.client_token }}</td>
            <td>{{ c.budget_no ?? c.预算编号 ?? c.budget_id }}</td>
            <td data-testid="charge-amount">{{ c.amount_epsilon ?? c.charged_epsilon ?? c.扣费Epsilon }}</td>
            <td data-testid="charge-status-cell">{{ c.status ?? c.状态 }}</td>
            <td>{{ c.result_count ?? '—' }}</td>
            <td>{{ c.created_at }}</td>
          </tr>
          <tr v-if="!charges.length">
            <td colspan="8" class="empty-state" data-testid="charge-empty">
              暂无检索扣费；在「密文索引」页发起检索并成功交付后，这里会出现扣费明细。
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </div>
  </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { listBudgetCharges, type ChargeRecord } from '@/api/search'

const charges = ref<ChargeRecord[]>([])
const loadError = ref('')

const totalCharged = computed(() =>
  charges.value
    .reduce((sum, c) => sum + Number(c.amount_epsilon ?? c.charged_epsilon ?? c.扣费Epsilon ?? 0), 0)
    .toFixed(2),
)

async function loadCharges() {
  loadError.value = ''
  try {
    charges.value = await listBudgetCharges()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '扣费明细读取失败'
  }
}

function handleCharged() {
  void loadCharges()
}

onMounted(() => {
  void loadCharges()
  window.addEventListener('index-search:charged', handleCharged)
})

onUnmounted(() => {
  window.removeEventListener('index-search:charged', handleCharged)
})
</script>
