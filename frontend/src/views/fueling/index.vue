<template>
  <section class="page" data-module="fueling">
    <header class="page-head">
      <div>
        <h2>航油加注管理</h2>
        <p class="page-desc">维护加油任务，结算清单按统一模板表头输出，出清单前先做三端条数自检。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记加油任务</button>
        <button class="btn" type="button" @click="exportRows">导出已发布清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="settlement-panel">
      <div class="settlement-head">
        <div>
          <p class="settlement-title">结算清单</p>
          <p class="settlement-meta">
            状态：{{ statusText }}
            <template v-if="settlement.version"> ｜ 第 {{ settlement.version }} 版</template>
            <template v-if="settlement.snapshot?.generated_at"> ｜ 生成于 {{ settlement.snapshot.generated_at }}</template>
          </p>
        </div>
        <div class="settlement-actions">
          <button class="btn primary" type="button" :disabled="busy" @click="generate(false)">
            {{ busy ? '生成中…' : '生成结算清单（先自检）' }}
          </button>
          <button
            v-if="settlement.status === 'failed' && !settlement.superseded"
            class="btn"
            type="button"
            :disabled="busy"
            @click="generate(true)"
          >
            从第 {{ settlement.next_line ?? settlement.failed_line ?? 1 }} 行续算
          </button>
          <button
            class="btn ghost"
            type="button"
            :disabled="busy"
            :title="simHint"
            @click="simulateInterrupt"
          >
            模拟中途打断（第 3 行）
          </button>
        </div>
      </div>

      <div class="audit-line">
        <span>实时对账（历史条数已重算）：</span>
        <span>列表页 {{ liveAudit.list_count ?? '-' }} 条</span>
        <span>详情页 {{ liveAudit.detail_count ?? '-' }} 条</span>
        <span>对账汇总 {{ liveAudit.summary_count ?? '-' }} 条</span>
        <span v-for="(value, key) in liveAudit.status_buckets" :key="key">{{ key }} {{ value }}</span>
      </div>

      <div v-if="settlement.discrepancy" class="alert-box error">
        <strong>自检未通过，已中止生成：</strong>{{ settlement.message }}
        <template v-if="settlement.discrepancy.line"> ｜ 差异行：第 {{ settlement.discrepancy.line }} 行（下表已标红）</template>
      </div>
      <div v-else-if="settlement.status === 'failed'" class="alert-box warn">
        {{ settlement.message }}，当前只暂存已处理行，不会留下半份清单。
      </div>
      <div v-if="settlement.warnings.length" class="alert-box warn">
        <strong>{{ settlement.warnings.length }} 行存在业务提示：</strong>
        <span v-for="(item, index) in settlement.warnings" :key="`${item.行号}-${index}`">
          第 {{ item.行号 }} 行 {{ item.加油编号 }}（{{ (item.原因 || []).join('、') }}）；
        </span>
      </div>
      <div v-if="settlement.snapshot" class="alert-box ok">
        {{ settlement.message }} ｜ 清单 {{ settlement.snapshot.total }} 行，已与对账汇总条数核对一致。
      </div>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>异常原因</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :class="rowClass(row)"
        >
          <td v-for="column in columns" :key="column" :class="{ 'num-cell': numericColumns.includes(column) }">
            {{ row[column] === '' || row[column] == null ? '—' : row[column] }}
          </td>
          <td class="reason-text">{{ row['异常原因'] || '—' }}</td>
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
          <td :colspan="columns.length + 2" class="empty-state">暂无航油加注数据，可先登记加油任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条航油加注记录（与对账汇总 {{ liveAudit.summary_count ?? '—' }} 条核对）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Warning = { 行号: number; 加油编号: string; 原因: string[] }
type Audit = {
  list_count: number
  detail_count: number
  summary_count: number
  status_buckets: Record<string, number>
}
type Snapshot = {
  version: number
  generated_at: string
  columns: string[]
  total: number
  items: Row[]
  warnings: Warning[]
  audit: Audit
}
type Settlement = {
  ok: boolean
  status: string
  message: string
  version: number | null
  total_lines: number
  failed_line: number | null
  next_line: number | null
  discrepancy: { line?: number; type?: string; message?: string } | null
  warnings: Warning[]
  snapshot: Snapshot | null
  latest: Snapshot | null
  live_audit: Audit
  superseded: boolean
}

const ENDPOINT = '/api/fueling'
const columns = ['行号', '加油编号', '对应航班', '油料类型', '加油车辆', '操作人员', '签收状态', '计划油量', '实际油量', '油量差值']
const numericColumns = ['计划油量', '实际油量', '油量差值']
const actions = ['开始加油', '完成加注', '签收确认']
const simHint = '本地演示用：生成在第 3 行打断，之后点「从第 3 行续算」继续'
const emptyAudit: Audit = { list_count: 0, detail_count: 0, summary_count: 0, status_buckets: {} }

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(1, 4)
const busy = ref(false)
const settlement = ref<Settlement>({
  ok: false,
  status: 'idle',
  message: '尚未生成过结算清单',
  version: null,
  total_lines: 0,
  failed_line: null,
  next_line: null,
  discrepancy: null,
  warnings: [],
  snapshot: null,
  latest: null,
  live_audit: emptyAudit,
  superseded: false,
})

const liveAudit = ref<Audit>(emptyAudit)

const statsMeta = [
  { label: '待加油', key: '待加油' },
  { label: '加油中', key: '加油中' },
  { label: '已加注', key: '已加注' },
  { label: '已签收', key: '已签收' },
]
const stats = ref(statsMeta.map((item) => ({ label: item.label, value: 0 })))

const statusTextMap: Record<string, string> = {
  idle: '未生成',
  processing: '生成中',
  failed: '已中止（可续算）',
  completed: '已发布最新版',
  superseded: '该版本已被作废',
}
const statusText = ref('未生成')

function applySettlement(payload: Settlement) {
  settlement.value = payload
  liveAudit.value = payload.live_audit ?? emptyAudit
  statusText.value = statusTextMap[payload.status] ?? payload.status
  stats.value = statsMeta.map((item) => ({
    label: item.label,
    value: payload.live_audit?.status_buckets?.[item.key] ?? 0,
  }))
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '加油任务登记入口尚未接入审批流'
}

function rowClass(row: Row): string {
  const discrepancy = settlement.value.discrepancy
  if (discrepancy?.line && Number(row['行号']) === Number(discrepancy.line)) {
    return 'diff-row'
  }
  return row['异常原因'] ? 'warn-row' : ''
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
    await Promise.all([reload(), refreshSettlement()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '航油加注操作失败'
  }
}

async function refreshSettlement() {
  try {
    const response = await request(`${ENDPOINT}/settlement`)
    if (response.ok) {
      applySettlement((await response.json()) as Settlement)
    }
  } catch {
    // 结算状态读不到不阻塞列表，页脚仍有列表读取错误提示
  }
}

async function generate(resume: boolean, failAfterLine: number | null = null) {
  busy.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/settlement`, {
      method: 'POST',
      body: JSON.stringify({ resume, fail_after_line: failAfterLine }),
    })
    if (response.status === 409) {
      const detail = (await response.json().catch(() => null)) as { detail?: { message?: string } } | null
      errorMessage.value = detail?.detail?.message ?? '该版本已作废，不能续算'
      await refreshSettlement()
      return
    }
    const payload = (await response.json()) as Settlement
    applySettlement(payload)
    if (!response.ok && !payload.status) {
      errorMessage.value = '结算清单生成请求失败'
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结算清单生成失败'
  } finally {
    busy.value = false
    await reload()
  }
}

function simulateInterrupt() {
  void generate(false, 3)
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
    rows.value = (payload.items ?? []) as Row[]
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '航油加注列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void refreshSettlement()
})
</script>
