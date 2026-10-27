<template>
  <section v-if="route" class="block">
    <div class="head">
      <div>
        <div class="route-no">{{ route.route }}<span class="muted">번 · {{ route.route_type }}</span></div>
        <div class="muted small">{{ route.start }} ↔ {{ route.end }} · {{ route.company }}</div>
      </div>
      <n-tag v-if="level" round :color="{ color: levelColor(level), textColor: '#0b0b0b', borderColor: levelColor(level) }">
        {{ level }}
      </n-tag>
    </div>

    <!-- 현재 배차 현황 -->
    <div class="block-title">{{ date }} {{ hour }}시 배차 현황</div>
    <div class="stats">
      <div class="stat">
        <div class="lbl">예측 승차</div>
        <div class="val num">{{ n(hourRow?.pred) }}<span>명/h</span></div>
        <div class="muted tiny">실제 {{ n(hourRow?.actual) }} · 작년 같은 요일 {{ n(hourRow?.baseline) }}</div>
      </div>
      <div class="stat">
        <div class="lbl">운행 (현재)</div>
        <div class="val num">{{ hourRow?.trips ?? '-' }}<span>회/h</span></div>
        <div class="muted tiny">약 {{ interval }}분 간격 · 기준 {{ route.min_interval }}~{{ route.max_interval }}분</div>
      </div>
      <div class="stat">
        <div class="lbl">최대 혼잡도</div>
        <div class="val num" :style="{ color: '#0b0b0b' }">{{ fmtCongestion(hourRow?.max_congestion) }}</div>
        <n-progress
          type="line"
          :percentage="Math.min(100, (hourRow?.max_congestion || 0) * 100)"
          :color="levelColor(level)"
          :show-indicator="false"
          :height="6"
        />
      </div>
      <div class="stat">
        <div class="lbl">인가 대수</div>
        <div class="val num">{{ route.authorized_buses ?? '-' }}<span>대</span></div>
        <div class="muted tiny">왕복 {{ route.run_minutes }}분 · {{ route.first_bus }}~{{ route.last_bus }}</div>
      </div>
    </div>

    <n-alert :type="need.type" :show-icon="true" class="need">
      <b>추가 배차 필요성: {{ need.label }}</b> — {{ need.text }}
    </n-alert>
  </section>

  <section class="block tabs-block">
    <n-tabs v-model:value="tab" type="line" size="small" animated>
      <n-tab-pane name="advise" tab="배차 권고">
        <div class="advise-ctl">
          <n-switch v-model:value="useLlm" size="small" />
          <span class="small">규정 요약 (LLM, 유형당 10~30초)</span>
          <n-button type="primary" size="small" :loading="adviceLoading" @click="runAdviseAll" style="margin-left: auto">
            {{ hour }}시 전체 노선 권고
          </n-button>
        </div>

        <template v-if="batch">
          <div class="batch-head">
            <b>{{ batch.date }} {{ batch.hour }}시</b>
            <n-tag v-if="batch.summary.increase" size="small" type="error" round>증차 {{ batch.summary.increase }}</n-tag>
            <n-tag v-if="batch.summary.increase_limited" size="small" type="warning" round>별도 대책 {{ batch.summary.increase_limited }}</n-tag>
            <n-tag v-if="batch.summary.decrease" size="small" type="info" round>감차 검토 {{ batch.summary.decrease }}</n-tag>
            <n-tag v-if="batch.summary.keep" size="small" type="success" round>유지 {{ batch.summary.keep }}</n-tag>
          </div>
          <div class="muted tiny">노선을 누르면 지도에서 선택되고 상세 근거가 열립니다 · 모두 담당자 검토 필요</div>
          <div v-for="a in batch.items" :key="a.route" class="brow-wrap">
            <div
              class="brow"
              :class="{ active: a.route === route?.route, off: a.status !== 'ok' }"
              @click="a.status === 'ok' && emit('select-route', a.route)"
            >
              <span class="dot" :style="{ background: levelColor(a.congestion?.level || '정보없음') }"></span>
              <b class="rno">{{ a.route }}</b>
              <template v-if="a.status === 'ok'">
                <span class="num small">{{ fmtCongestion(a.congestion.ratio) }}</span>
                <span class="num small ink2">{{ a.current.interval_min }}→<b>{{ a.recommendation.recommended_interval_min }}</b>분</span>
                <span class="num small ink2" v-if="a.recommendation.extra_buses">{{ a.recommendation.extra_buses > 0 ? '+' : '' }}{{ a.recommendation.extra_buses }}대</span>
                <n-tag size="small" :type="ACTION_TYPES[a.recommendation.action]" class="act">{{ actionLabel(a.recommendation.action) }}</n-tag>
              </template>
              <span v-else class="muted small">운행 없음</span>
            </div>
            <AdviceCard
              v-if="a.status === 'ok' && a.route === route?.route"
              :key="a.advisory_id ?? a.route"
              :advice="a"
              :db-available="dbAvailable"
              class="inline-card"
              @reviewed="loadHistory"
            />
          </div>
        </template>
        <n-empty v-else-if="!adviceLoading" :description="`'${hour}시 전체 노선 권고'를 누르면 노선마다 증차·유지·감차 권고가 만들어집니다`" />
      </n-tab-pane>

      <n-tab-pane name="ask" tab="규정 Q&A">
        <AskPanel domain="bus" :llm="llm" :embedding="embedding" />
      </n-tab-pane>

      <n-tab-pane name="history" tab="권고 이력">
        <div v-if="!dbAvailable" class="muted small">MariaDB 미연결 — 이력이 저장되지 않습니다.</div>
        <template v-if="saved">
          <div class="saved-head">
            <n-button size="tiny" quaternary @click="saved = null">← 목록</n-button>
            <span class="small"><b>저장된 권고 #{{ saved.advisory_id }}</b> · {{ saved.created_at?.slice(0, 16) }} 생성</span>
          </div>
          <AdviceCard :key="'saved-' + saved.advisory_id" :advice="saved" :db-available="dbAvailable" @reviewed="loadHistory" />
        </template>
        <div v-else v-for="h in history" :key="h.id" class="hist" @click="openSaved(h.id)">
          <div>
            <b>#{{ h.id }} {{ h.target }}번</b> {{ h.date }} {{ h.hour }}시
            <span class="muted small">· {{ actionLabel(h.action) }}</span>
          </div>
          <div class="small ink2">
            {{ h.current_interval }}분 → {{ h.recommended_interval }}분 · 혼잡도 {{ fmtCongestion(h.congestion) }} ·
            <n-tag size="tiny" :type="statusType(h.review_status)">{{ statusLabel(h.review_status) }}</n-tag>
            <span v-if="h.reviewer" class="muted"> {{ h.reviewer }}</span>
          </div>
        </div>
        <n-empty v-if="dbAvailable && !history.length && !saved" description="저장된 권고가 없습니다" />
      </n-tab-pane>
    </n-tabs>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { NAlert, NButton, NEmpty, NProgress, NSwitch, NTabPane, NTabs, NTag, useMessage } from 'naive-ui'
import { ACTION_TYPES, api, errorMessage, fmtCongestion, levelColor } from '../api'
import AdviceCard from './AdviceCard.vue'
import AskPanel from './AskPanel.vue'

const props = defineProps({
  route: { type: Object, default: null },
  date: { type: String, default: null },
  hour: { type: Number, default: 0 },
  hourRow: { type: Object, default: null },
  llm: { type: String, default: 'qwen' },
  embedding: { type: String, default: 'bge' },
  dbAvailable: { type: Boolean, default: false },
})

const emit = defineEmits(['select-route', 'goto'])
const message = useMessage()
const tab = ref('advise')
const useLlm = ref(true)
const batch = ref(null)
const adviceLoading = ref(false)
const history = ref([])
const saved = ref(null)

const level = computed(() => props.hourRow?.level)
const interval = computed(() => (props.hourRow?.trips ? (60 / props.hourRow.trips).toFixed(1) : '-'))
const n = (v) => (v == null ? '-' : Math.round(v).toLocaleString())

// 혼잡도 기준 간단 판정 (상세 수치는 '권고 생성')
const need = computed(() => {
  const c = props.hourRow?.max_congestion
  if (c == null) return { type: 'default', label: '정보 없음', text: '이 시간대 운행 데이터가 없습니다.' }
  if (c >= 0.8) return { type: 'error', label: '높음', text: `최대 혼잡도 ${fmtCongestion(c)} — 배차간격 단축(증차) 검토 대상입니다.` }
  if (c < 0.25) return { type: 'info', label: '낮음 (감차 검토)', text: `최대 혼잡도 ${fmtCongestion(c)} — 수요 대비 운행이 많습니다.` }
  return { type: 'success', label: '보통', text: `최대 혼잡도 ${fmtCongestion(c)} — 현행 배차로 수용 가능합니다.` }
})

const ACTIONS = { increase: '증차', increase_limited: '별도 대책', decrease: '감차 검토', keep: '현행 유지' }
const actionLabel = (a) => ACTIONS[a] || a
const STATUS = { pending: ['검토 대기', 'warning'], approved: ['승인', 'success'], held: ['보류', 'default'], rejected: ['반려', 'error'] }
const statusLabel = (s) => STATUS[s]?.[0] || s
const statusType = (s) => STATUS[s]?.[1] || 'default'

const contextKey = () => `${props.date}|${props.hour}`

// 선택한 날짜·시간대의 전체 노선 권고 (증차 필요 → 별도 대책 → 감차 검토 → 유지 순)
async function runAdviseAll() {
  adviceLoading.value = true
  const key = contextKey()
  try {
    const result = await api.adviseAll({
      date: props.date, hour: props.hour, llm: props.llm, embedding: props.embedding, use_llm: useLlm.value,
    })
    // LLM 응답을 기다리는 사이 날짜·시간을 바꿨다면 지난 결과는 표시하지 않음
    if (key === contextKey()) {
      batch.value = result
      const top = result.items.find((a) => a.status === 'ok')
      if (top && !result.items.some((a) => a.route === props.route?.route && a.status === 'ok')) emit('select-route', top.route)
    }
    loadHistory()
  } catch (e) {
    message.error(errorMessage(e))
  } finally {
    adviceLoading.value = false
  }
}

// 이력 항목 클릭: 저장된 권고 전체를 불러오고, 지도·날짜·시간도 그 권고 시점으로 이동
async function openSaved(id) {
  try {
    const item = await api.advisory(id)
    saved.value = item
    emit('goto', { route: item.route, date: item.date, hour: item.hour })
  } catch (e) {
    message.error(errorMessage(e))
  }
}

async function loadHistory() {
  if (!props.dbAvailable) return
  try {
    history.value = (await api.advisories('bus')).items
  } catch {
    history.value = []
  }
}

watch(() => [props.date, props.hour], () => (batch.value = null))
watch(tab, (t) => t === 'history' && loadHistory())
</script>

<style scoped>
.block { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 12px; }
.block-title { font-size: 13px; font-weight: 600; margin: 12px 0 8px; color: var(--ink-2); }
.head { display: flex; justify-content: space-between; align-items: flex-start; }
.route-no { font-size: 24px; font-weight: 800; }
.route-no span { font-size: 14px; font-weight: 500; margin-left: 2px; }
.small { font-size: 12px; }
.tiny { font-size: 11px; margin-top: 2px; }
.stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.stat { border: 1px solid #efeee8; border-radius: 8px; padding: 8px 10px; }
.lbl { font-size: 11px; color: var(--muted); }
.val { font-size: 20px; font-weight: 700; }
.val span { font-size: 11px; color: var(--muted); font-weight: 500; margin-left: 2px; }
.need { margin-top: 10px; font-size: 13px; }
.tabs-block { flex: 1; }
.advise-ctl { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.hist { padding: 8px 6px; border-bottom: 1px solid #f0efe9; font-size: 13px; cursor: pointer; border-radius: 6px; }
.hist:hover { background: #f4f3ef; }
.saved-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.batch-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 4px 0; }
.brow { display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 8px; border: 1px solid transparent; cursor: pointer; }
.brow:hover { background: #f4f3ef; }
.brow.active { border-color: var(--accent); background: #eef4fc; }
.brow.off { cursor: default; opacity: 0.6; }
.brow .rno { width: 34px; }
.brow .act { margin-left: auto; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.inline-card { margin: 6px 0 10px; }
</style>
