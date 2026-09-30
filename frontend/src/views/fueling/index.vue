<template>
  <section class="page" data-module="fueling">
    <header class="page-head">
      <div>
        <h2>航油加注管理</h2>
        <p class="page-desc">维护加油任务；出结算清单前先自检加油车辆、油料类型、实际油量、签收状态、操作人员，并核对列表页、详情页与对账汇总条数。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="busy" @click="generate(false)">生成结算清单</button>
        <button class="btn" type="button" :disabled="busy || !canResume" @click="generate(true)">从失败行继续</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <!-- 自检结果横幅：通过 / 中止 / 被更新版本取代 -->
    <div v-if="result" class="settle-banner" :class="bannerClass" role="status">
      <div>
        <strong>{{ bannerTitle }}</strong>
        <span>{{ result.message }}</span>
      </div>
      <button v-if="result.status === 'aborted'" class="btn" type="button" :disabled="busy" @click="generate(true)">
        从{{ result.resume?.label ?? '失败行' }}继续
      </button>
    </div>

    <!-- 中止现场：指出差在哪一行、原因，并提供就地补录修正 -->
    <section v-if="result?.status === 'aborted'" class="issue-panel">
      <h3>差异行（共 {{ result.issues.length }} 行，清单已中止、未落任何行）</h3>
      <table class="data-table issue-table">
        <thead>
          <tr>
            <th>行号</th>
            <th>加油编号</th>
            <th>差异原因</th>
            <th v-for="field in fixFields" :key="field">{{ field }}</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="issue in result.issues" :key="String(issue.id ?? issue.row)" class="issue-row">
            <td>第 {{ issue.row }} 行</td>
            <td>{{ issue['加油编号'] || '—' }}</td>
            <td class="issue-reasons">
              <span v-for="reason in issue.reasons" :key="reason" class="issue-tag">{{ reason }}</span>
            </td>
            <td v-for="field in fixFields" :key="field">
              <input
                v-model="drafts[String(issue.id ?? issue.row)][field]"
                class="fix-input"
                :placeholder="`补录${field}`"
              />
            </td>
            <td>
              <button class="link" type="button" :disabled="busy" @click="fixIssue(issue)">补录并继续</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="result.counts" class="reconcile-text">
        条数核对：列表页 {{ result.counts.list_total }} 条 / 详情页 {{ result.counts.detail_count }} 条 / 对账汇总 {{ result.counts.summary_total }} 条
      </p>
    </section>

    <!-- 结算清单：表头严格按结算模板顺序，差异行整行标出 -->
    <section v-if="shownItems.length" class="settlement">
      <h3>
        结算清单（第 {{ shownResult?.generation }} 版，{{ shownItems.length }} 行）
        <span class="settle-time">{{ shownResult?.generated_at }}</span>
      </h3>
      <table class="data-table settlement-table">
        <thead>
          <tr>
            <th v-for="column in shownColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in shownItems" :key="String(item.id)" :class="{ 'issue-row': issueIdSet.has(Number(item.id)) }">
            <td v-for="column in shownColumns" :key="column">{{ formatCell(item[column]) }}</td>
          </tr>
        </tbody>
      </table>

      <dl v-if="shownResult?.summary" class="summary-grid">
        <div><dt>总行数</dt><dd>{{ shownResult.summary.total }}</dd></div>
        <div><dt>已签收</dt><dd>{{ shownResult.summary.signed }}</dd></div>
        <div><dt>未签收</dt><dd>{{ shownResult.summary.unsigned }}</dd></div>
        <div><dt>计划油量合计(升)</dt><dd>{{ shownResult.summary.planned_total }}</dd></div>
        <div><dt>实际油量合计(升)</dt><dd>{{ shownResult.summary.actual_total }}</dd></div>
        <div v-if="shownResult.counts">
          <dt>三处条数核对</dt>
          <dd :class="shownResult.counts.matched ? 'ok-text' : 'error-text'">
            {{ shownResult.counts.matched ? '一致' : '不一致' }}
            （{{ shownResult.counts.list_total }}/{{ shownResult.counts.detail_count }}/{{ shownResult.counts.summary_total }}）
          </dd>
        </div>
      </dl>
    </section>

    <!-- 加油任务台账 -->
    <h3 class="section-title">加油任务台账</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无航油加注数据，可先登记加油任务</td>
        </tr>
      </tbody>
    </table>

    <!-- 历史版本：每次出单按当前数据重算条数 -->
    <section v-if="history.length" class="history-panel">
      <h3>历史结算版本（条数按出单时数据重算）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>版本</th>
            <th>生成时间</th>
            <th>总行数</th>
            <th>已签收</th>
            <th>未签收</th>
            <th>计划油量合计(升)</th>
            <th>实际油量合计(升)</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in history" :key="record.generation">
            <td>第 {{ record.generation }} 版</td>
            <td>{{ record.generated_at }}</td>
            <td>{{ record.total }}</td>
            <td>{{ record.signed }}</td>
            <td>{{ record.unsigned }}</td>
            <td>{{ record.planned_total }}</td>
            <td>{{ record.actual_total }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条航油加注记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

interface Issue {
  row: number
  id: number | null
  加油编号: string
  reasons: string[]
}

interface SettlementResult {
  status: 'complete' | 'aborted' | 'superseded'
  generation: number
  columns: string[]
  items: Array<Record<string, string | number | null>>
  issues: Issue[]
  message: string
  generated_at: string
  resume: { from_row: number; label: string } | null
  counts: { list_total: number; detail_count: number; summary_total: number; matched: boolean } | null
  summary: {
    total: number
    signed: number
    unsigned: number
    planned_total: number
    actual_total: number
  } | null
  history: Array<{
    generation: number
    generated_at: string
    total: number
    signed: number
    unsigned: number
    planned_total: number
    actual_total: number
  }>
}

const ENDPOINT = '/api/fueling'
const columns = ['加油编号', '对应航班', '油料类型', '计划油量', '实际油量', '加油车辆', '操作人员', '加油状态']
const fixFields = ['对应航班', '油料类型', '计划油量', '实际油量', '加油车辆', '操作人员']
const actions = ['开始加油', '完成加注', '签收确认']
const stats = [
  { label: '待加油航班', value: 0 },
  { label: '加油中航班', value: 0 },
  { label: '已加注航班', value: 0 },
]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const busy = ref(false)
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const result = ref<SettlementResult | null>(null)
const published = ref<SettlementResult | null>(null)
const drafts = reactive<Record<string, Record<string, string>>>({})

const shownResult = computed<SettlementResult | null>(() => {
  if (result.value && result.value.status === 'complete') return result.value
  return published.value
})
const shownItems = computed(() => shownResult.value?.items ?? [])
const shownColumns = computed(() => shownResult.value?.columns ?? [])
const history = computed(() => result.value?.history ?? published.value?.history ?? [])
const canResume = computed(() => result.value?.status === 'aborted')
const issueIdSet = computed(() => new Set((result.value?.issues ?? []).map((issue) => Number(issue.id))))
const bannerClass = computed(() => {
  const status = result.value?.status
  return status === 'complete' ? 'banner-ok' : status === 'aborted' ? 'banner-error' : 'banner-warn'
})
const bannerTitle = computed(() => {
  const status = result.value?.status
  return status === 'complete' ? '自检通过，清单已生成' : status === 'aborted' ? '自检中止' : '版本已被取代'
})

function formatCell(value: string | number | null): string {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function seedDraft(issue: Issue) {
  const key = String(issue.id ?? issue.row)
  if (!drafts[key]) {
    const source = rows.value.find((row) => Number(row.id) === Number(issue.id)) ?? {}
    drafts[key] = {}
    for (const field of fixFields) {
      const current = source[field]
      drafts[key][field] = current === null || current === undefined ? '' : String(current)
    }
  }
}

async function loadSettlement() {
  try {
    const response = await request(`${ENDPOINT}/settlement`)
    if (!response.ok) return
    const payload = await response.json()
    published.value = payload.published ?? null
    // 进入页面先不自动展示历史中止现场，避免误以为又失败；点“继续”时才触发
  } catch {
    // 结算状态读不到不影响台账浏览
  }
}

async function generate(resume: boolean) {
  errorMessage.value = ''
  busy.value = true
  try {
    const response = await request(`${ENDPOINT}/settlement/generate?resume=${resume ? 'true' : 'false'}`, {
      method: 'POST',
    })
    if (!response.ok) throw new Error('结算清单生成请求失败')
    const settled: SettlementResult = await response.json()
    result.value = settled
    if (settled.status === 'aborted') {
      settled.issues.forEach(seedDraft)
    }
    if (settled.status === 'complete') {
      published.value = settled
      void reload()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结算清单生成失败'
  } finally {
    busy.value = false
  }
}

async function fixIssue(issue: Issue) {
  errorMessage.value = ''
  const key = String(issue.id ?? issue.row)
  if (issue.id === null) {
    errorMessage.value = `第 ${issue.row} 行没有任务编号，无法补录`
    return
  }
  busy.value = true
  try {
    const response = await request(`${ENDPOINT}/${issue.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ values: drafts[key] ?? {} }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || payload?.ok === false) {
      throw new Error(payload?.message ?? '补录修正未生效')
    }
    await reload()
    await generate(true)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补录修正失败'
  } finally {
    busy.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('航油加注动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '航油加注操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('加油任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '航油加注列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadSettlement()
})
</script>

<style scoped>
.page-actions {
  display: flex;
  gap: 8px;
}

.settle-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  background: #fff;
  font-size: 13px;
}

.settle-banner strong {
  display: block;
  margin-bottom: 2px;
}

.banner-ok {
  border-color: #1a7f37;
  background: #ecfdf3;
}

.banner-error {
  border-color: #b42318;
  background: #fef3f2;
}

.banner-warn {
  border-color: #b54708;
  background: #fffaeb;
}

.issue-panel,
.settlement,
.history-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin: 12px 0;
}

.issue-panel h3,
.settlement h3,
.history-panel h3 {
  margin: 0 0 8px;
  font-size: 14px;
}

.settle-time {
  font-weight: 400;
  color: var(--muted);
  font-size: 12px;
  margin-left: 8px;
}

.issue-row {
  background: #fef3f2;
}

.issue-reasons {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.issue-tag {
  display: inline-block;
  background: #fecdca;
  color: #7a271a;
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 12px;
}

.fix-input {
  width: 110px;
  padding: 4px 6px;
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 12px;
}

.reconcile-text,
.ok-text {
  color: #1a7f37;
  font-size: 12px;
  margin: 8px 0 0;
}

.summary-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin: 12px 0 0;
  font-size: 13px;
}

.summary-grid dt {
  color: var(--muted);
  font-size: 12px;
}

.summary-grid dd {
  margin: 2px 0 0;
  font-weight: 600;
}

.section-title {
  font-size: 14px;
  margin: 16px 0 8px;
}
</style>
