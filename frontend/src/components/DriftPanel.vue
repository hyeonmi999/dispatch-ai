<template>
  <!-- 모델 점검 (요청 R-3): 미리 만든 예측과 나중에 들어온 실측을 주별로 견주고, 기준을 넘으면 재학습을 권고. 결정은 담당자 -->
  <div>
    <div v-if="error" class="notice warn" role="alert">{{ error }}</div>
    <p v-else-if="!data" class="empty-note">불러오는 중</p>
    <p v-else-if="!data.items.length" class="empty-note">
      점검 결과가 없습니다. 새 주 데이터를 받은 뒤 <code>python scripts/check_drift.py</code>
      (평가 구간 다시 돌려 보기는 <code>--replay</code>)를 실행하세요.
    </p>

    <template v-else>
      <p class="sm head">
        권고 기준: 오차가 평가 때의 {{ data.rules.mae_ratio }}배 초과 · 작년 같은 요일보다 못함 ·
        상한(P90) 적중 {{ data.rules.p90_coverage_min }}% 미만 중 하나
      </p>
      <div class="scroll">
        <table>
          <caption class="sm">주별 점검 · 행을 누르면 자세히</caption>
          <thead>
            <tr>
              <th scope="col">기간</th>
              <th scope="col" class="r">오차(명)</th>
              <th scope="col" class="r">작년 같은 요일</th>
              <th scope="col" class="r">상한 적중</th>
              <th scope="col">판정</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in data.items" :key="c.id" :class="{ on: c.id === selectedId }" tabindex="0"
                @click="selectedId = c.id" @keydown.enter.prevent="selectedId = c.id" @keydown.space.prevent="selectedId = c.id">
              <th scope="row">
                <span class="num">{{ shortDate(c.window.start) }}~{{ shortDate(c.window.end) }}</span>
                <small>{{ c.kind_label }} · {{ c.window.days }}일</small>
              </th>
              <td class="r num">{{ fmt(c.metrics.stop_hour_mae, 2) }} <small>×{{ fmt(c.metrics.mae_ratio, 2) }}</small></td>
              <td class="r num">{{ fmt(c.metrics.baseline_mae, 2) }}</td>
              <td class="r num">{{ c.metrics.p90_coverage_pct == null ? '-' : `${fmt(c.metrics.p90_coverage_pct, 1)}%` }}</td>
              <td>
                <span class="verdict" :class="c.verdict">{{ VERDICT[c.verdict] }}</span>
                <small v-if="c.review">{{ STATUS[c.review.status] }}</small>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <section v-if="selected" class="detail" :aria-label="`${selected.window.start}~${selected.window.end} 점검 자세히`">
        <ul class="checks">
          <li v-for="k in selected.checks" :key="k.name" :class="{ bad: k.failed }">
            <span aria-hidden="true">{{ k.failed ? '✕' : k.value == null ? '–' : '✓' }}</span>
            {{ k.label }}
            <small class="num">{{ checkValue(k) }}</small>
            <span class="sr-only">{{ k.failed ? '기준 넘음' : k.value == null ? '판단 안 함' : '통과' }}</span>
          </li>
        </ul>
        <p class="reason" :class="selected.verdict">{{ selected.reasons.join(' ') }}</p>

        <template v-if="selected.recommend">
          <div class="foot">
            <span v-if="selected.review" class="done">
              {{ STATUS[selected.review.status] }} 완료<small>{{ selected.review.reviewer }} · {{ selected.review.reviewed_at }}</small>
            </span>
            <div class="rv" role="group" :aria-label="`${selected.window.start} 주 재학습 권고 결정`">
              <button v-for="(label, key) in STATUS" :key="key" :disabled="!data.db.available || busy"
                      :aria-pressed="selected.review?.status === key" @click="review(key)">{{ label }}</button>
            </div>
          </div>
          <p v-if="!data.db.available" class="sm">DB가 꺼져 있어 결정을 남길 수 없습니다 (MariaDB를 켜고 다시 열기).</p>
          <div v-if="selected.review?.status === 'approved'" class="steps">
            <p class="sm">서버는 학습을 돌리지 않습니다. 아래 순서로 직접 실행하세요 (API 서버를 끈 뒤).</p>
            <ol><li v-for="s in selected.next_steps" :key="s"><code>{{ s }}</code></li></ol>
          </div>
        </template>

        <div class="notice caution">{{ selected.caution }}</div>
        <p class="sm meta">{{ selected.id }} · 기준 MAE {{ fmt(selected.reference_mae, 2) }}명 · {{ selected.model_version }}</p>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { api, errorMessage } from '../api'

const data = ref(null)
const error = ref('')
const busy = ref(false)
const selectedId = ref(null)
const message = useMessage()

const VERDICT = { retrain: '재학습 권고', ok: '그대로', insufficient: '판정 보류' }
const STATUS = { approved: '승인', held: '보류', rejected: '반려' }
const selected = computed(() => data.value?.items.find((c) => c.id === selectedId.value) ?? null)

const fmt = (v, d) => (v == null ? '-' : Number(v).toFixed(d))
const shortDate = (s) => s.slice(5).replace('-', '/')
function checkValue(k) {
  if (k.value == null) return '자료 없음'
  if (k.name === 'mae_ratio') return `×${fmt(k.value, 2)} / 기준 ×${k.limit}`
  if (k.name === 'vs_baseline') return `모델 ${fmt(k.value, 2)}명 / 작년 ${fmt(k.limit, 2)}명`
  return `${fmt(k.value, 1)}% / 기준 ${k.limit}%`
}

async function load() {
  try {
    data.value = await api.drift()
    // 처음에는 권고가 있는 가장 최근 점검, 없으면 가장 최근 점검
    if (!selected.value) selectedId.value = (data.value.items.find((c) => c.recommend) ?? data.value.items[0])?.id ?? null
  } catch (e) {
    error.value = errorMessage(e)
  }
}

async function review(status) {
  busy.value = true
  try {
    const updated = await api.reviewDrift(selected.value.id, { status, reviewer: '배차 담당자', note: '' })
    data.value.items = data.value.items.map((c) => (c.id === updated.id ? updated : c))
    message.success(`${updated.window.start} 주 재학습 권고 ${STATUS[status]} 처리됨`)
  } catch (e) {
    message.error(errorMessage(e))
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.head { margin: 0 0 6px; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
caption { text-align: left; caption-side: top; padding-bottom: 3px; }
th, td { padding: 5px 6px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
thead th { font-size: 11px; font-weight: 500; color: var(--ink3); background: var(--panel2); }
tbody tr { cursor: pointer; }
tbody tr:hover { background: var(--panel2); }
tbody tr:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
tbody tr.on th { box-shadow: inset 2px 0 0 var(--accent); }
tbody th { font-weight: 500; }
.r { text-align: right; }
small { font-size: 10px; color: var(--ink3); margin-left: 4px; }
th small { display: block; margin-left: 0; }
.verdict { font-weight: 600; }
.verdict.retrain { color: var(--c3); }
.verdict.insufficient { color: var(--ink3); font-weight: 500; }
.detail { margin-top: 8px; }
.checks { list-style: none; margin: 0 0 6px; padding: 0; font-size: 12px; }
.checks li { padding: 3px 0; }
.checks li > span[aria-hidden] { display: inline-block; width: 16px; font-weight: 600; }
.checks li.bad { color: var(--c3); font-weight: 600; }
.reason { font-size: 12px; margin: 0 0 8px; }
.reason.retrain { font-weight: 600; }
.foot { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.done { font-size: 12px; font-weight: 600; }
.rv { display: inline-flex; border: 1px solid var(--line); border-radius: 2px; overflow: hidden; }
.rv button { font-size: 12px; padding: 3px 10px; border-right: 1px solid var(--line); min-height: 28px; }
.rv button:last-child { border-right: 0; }
.rv button[aria-pressed='true'] { background: var(--ink2); color: var(--panel); }
.rv button:hover:not(:disabled) { background: var(--ink); color: var(--panel); }
.rv button:disabled { opacity: 0.45; cursor: not-allowed; }
.steps ol { margin: 4px 0 8px; padding-left: 18px; font-size: 11px; }
.steps li { margin: 2px 0; }
code { font-size: 11px; overflow-wrap: anywhere; white-space: normal; }
.caution { font-size: 11px; }
.meta { text-align: right; margin: 6px 0 0; overflow-wrap: anywhere; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
@media (max-width: 820px) {
  .rv button { min-height: 44px; }
  tbody th, tbody td { padding: 10px 6px; }
}
</style>
