<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            {{ row.status }}
            <span
              v-if="row.核对冲突"
              class="conflict-badge"
              :data-testid="`conflict-${row.id}`"
            >迟到凭据{{ row.迟到凭据数 }}笔待核对</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="meta.key === 'parse_metric'"
              class="link"
              type="button"
              :data-testid="`review-${row.id}`"
              @click="openReviewDetail(row)"
            >
              复核详情
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="showReview && reviewDetail" class="modal-backdrop" data-testid="review-modal">
      <div class="modal review-modal">
        <h3>复核详情 · {{ reviewDetail.reading.指标读数编号 }}</h3>
        <p class="review-meta">
          统计窗口 {{ reviewDetail.统计窗口 }} · 来源渠道 {{ reviewDetail.来源渠道 }} ·
          当前状态 {{ reviewDetail.reading.status }}
        </p>
        <p class="review-current" data-testid="review-current">
          当次读数：回退率 {{ reviewDetail.当前.回退率 }}
          （{{ reviewDetail.当前.回退决策计数 }}/{{ reviewDetail.当前.路由决策计数 }}）
        </p>
        <p v-if="reviewDetail.原账" class="review-original" data-testid="review-original">
          原账读数：回退率 {{ reviewDetail.原账.回退率 }}
          （{{ reviewDetail.原账.回退决策计数 }}/{{ reviewDetail.原账.路由决策计数 }}），
          原证据：{{ reviewDetail.原账.计入决策编号 || '无' }}
        </p>
        <p v-if="reviewDetail.核对冲突" class="error-text" data-testid="review-conflict">
          {{ reviewDetail.message }}
        </p>

        <h4>动作清单（本次真正计入的解析路由决策）</h4>
        <table class="data-table">
          <thead>
            <tr><th>决策编号</th><th>关联批次</th><th>回退次数</th><th>决策时间</th><th>计入分子</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in reviewDetail.动作清单" :key="item.决策编号">
              <td>{{ item.决策编号 }}</td>
              <td>{{ item.关联批次 }}</td>
              <td>{{ item.回退次数 }}</td>
              <td>{{ item.决策时间 }}</td>
              <td>{{ item.计入分子 ? '是' : '否' }}</td>
            </tr>
            <tr v-if="!reviewDetail.动作清单.length">
              <td colspan="5" class="empty-state">暂无计入决策</td>
            </tr>
          </tbody>
        </table>

        <template v-if="reviewDetail.迟到凭据.length">
          <h4>迟到凭据（属于本渠道本窗口、尚未计入）</h4>
          <table class="data-table">
            <thead>
              <tr><th>选择</th><th>决策编号</th><th>关联批次</th><th>回退次数</th><th>决策时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in reviewDetail.迟到凭据" :key="item.决策编号">
                <td>
                  <input
                    v-model="selectedReceipts"
                    type="checkbox"
                    :value="item.决策编号"
                    :data-testid="`receipt-${item.决策编号}`"
                  />
                </td>
                <td>{{ item.决策编号 }}</td>
                <td>{{ item.关联批次 }}</td>
                <td>{{ item.回退次数 }}</td>
                <td>{{ item.决策时间 }}</td>
              </tr>
            </tbody>
          </table>
          <div class="page-actions">
            <button
              class="btn primary"
              type="button"
              data-testid="submit-correction"
              :disabled="!selectedReceipts.length"
              @click="submitCorrection"
            >
              提交更正（并入所选凭据）
            </button>
            <button class="btn" type="button" data-testid="defer-receipts" @click="deferReceipts">
              沿用原证据（凭据转待补算）
            </button>
          </div>
        </template>

        <template v-if="reviewDetail.待补算凭据.length">
          <h4>待补算凭据（复核员决定不进入本次读数）</h4>
          <table class="data-table">
            <thead>
              <tr><th>决策编号</th><th>关联批次</th><th>回退次数</th><th>决策时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in reviewDetail.待补算凭据" :key="item.决策编号">
                <td>{{ item.决策编号 }}</td>
                <td>{{ item.关联批次 }}</td>
                <td>{{ item.回退次数 }}</td>
                <td>{{ item.决策时间 }}</td>
              </tr>
            </tbody>
          </table>
        </template>

        <h4>统计窗口内的批次</h4>
        <table class="data-table">
          <thead>
            <tr><th>批次记录编号</th><th>关联投递批次</th><th>总行数</th><th>完成时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="batch in reviewDetail.窗口批次" :key="batch.批次记录编号">
              <td>{{ batch.批次记录编号 }}</td>
              <td>{{ batch.关联投递批次 }}</td>
              <td>{{ batch.总行数 }}</td>
              <td>{{ batch.完成时间 }}</td>
            </tr>
            <tr v-if="!reviewDetail.窗口批次.length">
              <td colspan="4" class="empty-state">窗口内暂无闭合批次</td>
            </tr>
          </tbody>
        </table>

        <p v-if="reviewDetail.复核结论" class="review-conclusion" data-testid="review-conclusion">
          复核结论：{{ reviewDetail.复核结论 }}
        </p>
        <p v-if="reviewError" class="error-text">{{ reviewError }}</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showReview = false">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  correctMetricReading,
  createEntry,
  deferMetricReceipts,
  downloadEntries,
  fetchMetricReviewDetail,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { EntryRow, MetricReviewDetail } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

// 解析指标复核详情：渠道与时间窗筛选、归属校验均在服务端完成，界面只展示
const showReview = ref(false)
const reviewDetail = ref<MetricReviewDetail | null>(null)
const selectedReceipts = ref<string[]>([])
const reviewError = ref('')

async function openReviewDetail(row: EntryRow) {
  reviewError.value = ''
  try {
    reviewDetail.value = await fetchMetricReviewDetail(Number(row.id))
    selectedReceipts.value = []
    showReview.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核详情读取失败'
  }
}

async function refreshReviewDetail() {
  if (!reviewDetail.value) return
  reviewDetail.value = await fetchMetricReviewDetail(Number(reviewDetail.value.reading.id))
}

async function submitCorrection() {
  if (!reviewDetail.value) return
  reviewError.value = ''
  try {
    const result = await correctMetricReading(
      Number(reviewDetail.value.reading.id),
      selectedReceipts.value,
    )
    if (!result.ok) throw new Error(result.message)
    selectedReceipts.value = []
    await refreshReviewDetail()
    await reload()
  } catch (error) {
    reviewError.value = error instanceof Error ? error.message : '更正失败'
  }
}

async function deferReceipts() {
  if (!reviewDetail.value) return
  reviewError.value = ''
  try {
    const result = await deferMetricReceipts(Number(reviewDetail.value.reading.id))
    if (!result.ok) throw new Error(result.message)
    await refreshReviewDetail()
    await reload()
  } catch (error) {
    reviewError.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>
