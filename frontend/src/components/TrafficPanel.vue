<template>
  <!-- 실시간 돌발·정체 (이슈 #42 방법 2): 노선 경로 위 사고·공사와 평소보다 늘어난 한 바퀴 시간을 알림. 배차는 바꾸지 않음 -->
  <div class="traffic">
    <div class="bar">
      <div class="seg" role="group" aria-label="자료 고르기">
        <button :aria-pressed="mode === 'live'" @click="setMode('live')">실시간</button>
        <button :aria-pressed="mode === 'replay'" @click="setMode('replay')">기록 다시 보기</button>
      </div>
      <select v-if="mode === 'replay'" v-model="snapshot" aria-label="기록 고르기" :disabled="!snapshots.length" @change="load">
        <option v-if="!snapshots.length" value="">기록 없음</option>
        <option v-for="s in snapshots" :key="s.name" :value="s.name">{{ when(s.fetched_at) }} · 돌발 {{ s.incidents }}건</option>
      </select>
      <button class="refresh" :disabled="loading" @click="load">{{ loading ? '받는 중' : '다시 받기' }}</button>
    </div>

    <p v-if="data?.source === 'replay'" class="replay" role="status">
      {{ when(data.fetched_at) }} 기록 — <strong>지금 상황 아님</strong>
    </p>

    <div v-if="error" class="notice warn" role="alert">
      {{ error }}
      <button v-if="mode === 'live'" class="link" @click="setMode('replay')">기록 다시 보기로</button>
    </div>
    <p v-else-if="loading && !data" class="empty-note">TOPIS에서 받는 중</p>
    <template v-else-if="data">
      <p v-if="!alerted.length" class="empty-note">
        {{ data.source === 'live' ? '지금' : '이 기록에서' }} {{ data.routes.length }}개 노선 경로에 돌발·정체 알림이 없습니다
      </p>

      <ul class="routes">
        <li v-for="r in ordered" :key="r.route" :class="{ on: r.route === selectedRoute, quiet: !r.messages.length }">
          <button class="rno" :class="`r${r.route}`" :aria-pressed="r.route === selectedRoute" @click="emit('select-route', r.route)">{{ r.route }}</button>
          <div class="body">
            <p v-if="!r.messages.length" class="sm">알림 없음{{ runText(r) }}</p>
            <template v-else>
              <ul class="incidents">
                <li v-for="i in r.incidents" :key="i.acc_id">
                  <strong :class="{ hot: ['A01', 'A02', 'A03'].includes(i.type) }">{{ i.dtype_name || i.type_name }}</strong>
                  <span v-if="i.near_stop" class="sm">{{ i.near_stop.seq }}번째 {{ i.near_stop.name }} 근처 · 경로에서 {{ i.distance_m }}m</span>
                  <span class="sm">{{ at(i.start) }} 발생 · {{ at(i.expected_clear) }} 해제 예정</span>
                  <span class="info">{{ i.info }}</span>
                </li>
              </ul>
              <p v-if="r.traffic?.extra_minutes != null" class="run" :class="{ hot: r.traffic.fleet_short }">
                한 바퀴 평소 {{ r.traffic.usual_run }}분 → 약 {{ r.traffic.est_run }}분 (+{{ Math.round(r.traffic.extra_minutes) }}분)
                · 도로 위 버스 {{ r.traffic.buses_usual }} → {{ r.traffic.buses_est }}대 / 인가 {{ r.traffic.authorized }}대
                <strong v-if="r.traffic.fleet_short">인가대수 부족 가능</strong>
              </p>
            </template>
            <details v-if="r.long_term.length" class="long">
              <summary class="sm">장기 공사 등 {{ r.long_term.length }}건 (평소 운행시간에 들어 있음)</summary>
              <ul><li v-for="i in r.long_term" :key="i.acc_id" class="sm">{{ i.dtype_name || i.type_name }} · {{ i.info }} · {{ day(i.expected_clear) }}까지</li></ul>
            </details>
          </div>
        </li>
      </ul>

      <ul v-if="data.warnings.length" class="warnings sm">
        <li v-for="w in data.warnings" :key="w">{{ w }}</li>
      </ul>
      <p class="foot sm">
        배차는 바꾸지 않습니다 · 담당자 검토 · 소통은 차량 기준(중앙버스차로 구간은 버스가 덜 늦을 수 있음) ·
        출처 서울 TOPIS · {{ when(data.fetched_at) }}
      </p>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, errorMessage } from '../api'

defineProps({ selectedRoute: { type: String, default: null } })
const emit = defineEmits(['select-route'])

const mode = ref('live')
const data = ref(null)
const error = ref('')
const loading = ref(false)
const snapshots = ref([])
const snapshot = ref('')

const alerted = computed(() => (data.value?.routes ?? []).filter((r) => r.messages.length))
// 알림이 있는 노선을 위로
const ordered = computed(() => [...(data.value?.routes ?? [])].sort((a, b) => (b.messages.length > 0) - (a.messages.length > 0)))

const pad = (n) => String(n).padStart(2, '0')
const asDate = (s) => (s ? new Date(s) : null)
const hm = (s) => { const d = asDate(s); return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '-' }
const day = (s) => { const d = asDate(s); return d ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` : '-' }
const when = (s) => { const d = asDate(s); return d ? `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${hm(s)}` : '-' }
// 받은 날과 같은 날이면 시각만, 다른 날(전날 발생·다음 날 해제)이면 날짜도
const at = (s) => (s && data.value && day(s) !== day(data.value.fetched_at) ? when(s) : hm(s))
function runText(r) {
  const t = r.traffic
  if (!t) return ''
  if (t.extra_minutes == null) return ` · 소통 정보 부족 (링크 ${Math.round(t.coverage * 100)}%)`
  return ` · 한 바퀴 +${Math.round(t.extra_minutes)}분`
}

async function loadSnapshots() {
  try {
    snapshots.value = (await api.trafficSnapshots())?.items ?? []
    if (!snapshots.value.some((s) => s.name === snapshot.value)) snapshot.value = snapshots.value[0]?.name ?? ''
  } catch (e) {
    snapshots.value = []
    error.value = errorMessage(e)
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (mode.value === 'replay') {
      await loadSnapshots()
      if (!snapshot.value) {
        data.value = null
        error.value = '받아 둔 기록이 없습니다. 실시간으로 한 번 받거나 scripts/record_topis.sh를 실행하세요.'
        return
      }
      data.value = await api.trafficAlerts({ mode: 'replay', snapshot: snapshot.value })
    } else {
      data.value = await api.trafficAlerts({ mode: 'live' })
    }
  } catch (e) {
    data.value = null
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

function setMode(m) {
  if (mode.value === m) return
  mode.value = m
  load()
}

onMounted(load)
</script>

<style scoped>
.traffic { font-size: 12px; }
.bar { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 2px; overflow: hidden; }
.seg button { font-size: 12px; padding: 3px 10px; border-right: 1px solid var(--line); min-height: 28px; }
.seg button:last-child { border-right: 0; }
.seg button[aria-pressed='true'] { background: var(--ink2); color: var(--panel); }
select { font-size: 12px; min-height: 28px; max-width: 220px; }
.refresh { font-size: 12px; padding: 3px 10px; min-height: 28px; border: 1px solid var(--line); margin-left: auto; }
.refresh:disabled { opacity: 0.5; cursor: wait; }
.replay { margin: 0 0 6px; padding: 5px 8px; border: 1px dashed var(--ink3); color: var(--ink2); }
.link { margin-left: 6px; text-decoration: underline; font-size: 12px; }
.routes { list-style: none; margin: 0; padding: 0; }
.routes > li { display: flex; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--line); }
.routes > li.on { box-shadow: inset 2px 0 0 var(--accent); padding-left: 4px; }
.routes > li.quiet { color: var(--ink3); }
.rno { min-width: 40px; height: 24px; font-weight: 600; font-size: 12px; border: 1px solid var(--line); border-radius: 2px; flex: none; }
.body { flex: 1; min-width: 0; }
.incidents { list-style: none; margin: 0; padding: 0; }
.incidents li { display: flex; flex-direction: column; gap: 1px; padding: 2px 0 4px; }
.info { color: var(--ink2); overflow-wrap: anywhere; }
.hot { color: var(--c3); }
.run { margin: 2px 0 0; }
.run strong { margin-left: 4px; }
.long summary { cursor: pointer; color: var(--ink3); }
.long ul { margin: 2px 0 0; padding-left: 16px; }
.sm { font-size: 11px; color: var(--ink3); }
.warnings { margin: 6px 0 0; padding-left: 16px; }
.foot { margin: 6px 0 0; overflow-wrap: anywhere; }
@media (max-width: 820px) {
  .seg button, .refresh, select { min-height: 44px; }
}
</style>
