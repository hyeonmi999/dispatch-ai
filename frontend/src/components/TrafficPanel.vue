<template>
  <!-- 실시간 돌발·정체 (이슈 #42 방법 2): 노선 경로 위 사고·공사와 평소보다 늘어난 한 바퀴 시간을 알림. 배차는 바꾸지 않음
       받은 자료는 Dashboard로 올려 지도(도로 교통 띠·돌발 표식)도 같은 값을 그림 -->
  <div class="traffic">
    <section class="top">
      <div class="bar">
        <div class="seg" role="group" aria-label="자료 고르기">
          <button :aria-pressed="mode === 'live'" @click="setMode('live')">실시간</button>
          <button :aria-pressed="mode === 'replay'" @click="setMode('replay')">기록 다시 보기</button>
        </div>
        <span v-if="mode === 'live' && data?.source === 'live'" class="stamp"><i aria-hidden="true"></i>{{ hm(data.fetched_at) }} 받음</span>
        <button class="refresh" :disabled="loading" @click="load">{{ loading ? '받는 중' : '다시 받기' }}</button>
      </div>
      <div v-if="mode === 'replay'" class="replay" role="status">
        <select v-model="snapshot" aria-label="기록 고르기" :disabled="!snapshots.length" @change="load">
          <option v-if="!snapshots.length" value="">기록 없음</option>
          <option v-for="s in snapshots" :key="s.name" :value="s.name">{{ when(s.fetched_at) }} · 돌발 {{ s.incidents }}건</option>
        </select>
        <span v-if="data?.source === 'replay'"><strong>{{ when(data.fetched_at) }} 기록</strong> · 지금 상황 아님</span>
      </div>
      <div v-if="error" class="notice warn" role="alert">
        {{ error }}
        <button v-if="mode === 'live'" class="link" @click="setMode('replay')">기록 다시 보기로</button>
      </div>
      <p v-else-if="loading && !data" class="empty-note">TOPIS에서 받는 중</p>
    </section>

    <template v-if="data && !error">
      <section>
        <div class="sum">
          <div :class="{ hot: warned.length }">
            <div class="v">{{ warned.length }}</div>
            <div class="k">검토할 노선 (한 바퀴 +{{ Math.round(WARN * 100) }}% 이상)</div>
          </div>
          <div>
            <div class="v">{{ incidents.length }}</div>
            <div class="k">경로 위 돌발</div>
          </div>
          <div>
            <div class="v">{{ slowSegments }}</div>
            <div class="k">평소 속도 ⅔ 아래 정류장 구간</div>
          </div>
        </div>
      </section>

      <section>
        <h3 class="sec">한 바퀴 시간 <span class="sm">평소보다 얼마나 더 · 점선이 경고 기준 {{ Math.round(WARN * 100) }}%</span></h3>
        <ul class="runs">
          <li v-for="r in runs" :key="r.route">
            <button class="run" :class="[`r${r.route}`, { on: r.route === selectedRoute }]" :aria-pressed="r.route === selectedRoute" @click="emit('select-route', r.route)">
              <span class="rno" :class="`r${r.route}`">{{ r.route }}</span>
              <template v-if="r.ratio != null">
                <span class="track" aria-hidden="true">
                  <span class="fill" :class="{ hot: r.hot }" :style="{ width: `${Math.min(1, r.ratio / MAXR) * 100}%` }"></span>
                  <span class="th" :style="{ left: `${(WARN / MAXR) * 100}%` }"></span>
                </span>
                <span class="rv num" :class="{ hot: r.hot }">+{{ Math.round(r.t.extra_minutes) }}분 · {{ Math.round(r.ratio * 100) }}%</span>
                <span class="detail">
                  <span>한 바퀴 <b class="num">{{ r.t.usual_run }}→{{ r.t.est_run }}분</b></span>
                  <span v-if="r.t.buses_usual != null">도로 위 버스 <b class="num">{{ r.t.buses_usual }}→{{ r.t.buses_est }}대</b> / 인가 {{ r.t.authorized }}대</span>
                  <span v-if="r.hot" class="flag">검토 필요</span>
                  <span v-if="r.t.fleet_short" class="flag">인가대수 부족 가능</span>
                </span>
              </template>
              <span v-else class="nodata sm">{{ r.t ? `소통 정보 부족 (링크 ${Math.round(r.t.coverage * 100)}%)` : '정체 계산 표 없음' }}</span>
              <ul v-if="r.route === selectedRoute && r.slow.length" class="slowlist">
                <li v-for="s in r.slow.slice(0, 3)" :key="s.link_id">
                  <span class="road">{{ s.road_name || s.link_id }} {{ s.length_m }}m</span>
                  <span class="num">{{ s.live_kmh }} / 평소 {{ s.usual_kmh }}km/h · +{{ Math.round(s.extra_seconds / 60) }}분</span>
                </li>
                <li v-if="r.slow.length > 3" class="sm">외 {{ r.slow.length - 3 }}곳 · 지도의 도로 교통 띠로 위치 확인</li>
              </ul>
            </button>
          </li>
        </ul>
        <div class="axis" aria-hidden="true"><i></i><div><span v-for="p in [0, 5, 10, 15, 20, 25]" :key="p" :style="{ left: `${(p / 25) * 100}%` }">{{ p }}%</span></div><i></i></div>
      </section>

      <section>
        <h3 class="sec">경로 위 돌발 <span class="sm">{{ incidents.length }}건 · 노선 경로 {{ NEAR_M }}m 안</span></h3>
        <p v-if="!incidents.length" class="empty-note">{{ data.source === 'live' ? '지금' : '이 기록에서' }} {{ data.routes.length }}개 노선 경로에 걸친 돌발이 없습니다</p>
        <article v-for="i in incidents" :key="i.acc_id" class="inc" :class="{ on: i.acc_id === focusedIncident }">
          <div class="inc-h">
            <span class="glyph" aria-hidden="true" v-html="ICON[kindOf(i.type)]"></span>
            <div class="tt">
              <div class="kind">{{ i.dtype_name || i.type_name }} · {{ i.hits.length }}개 노선 경로에 걸침</div>
              <div class="name">{{ cleanInfo(i.info) }}</div>
            </div>
          </div>
          <div class="when">
            <span class="num">{{ at(i.start) }}</span>
            <span class="tl" :title="`지금까지 ${Math.round(progress(i) * 100)}%`"><i :style="{ width: `${progress(i) * 100}%` }"></i></span>
            <span class="num">{{ at(i.expected_clear) }}</span><span class="sm">해제 예정</span>
          </div>
          <ul class="hits">
            <li v-for="h in i.hits" :key="h.route">
              <span class="rno" :class="`r${h.route}`">{{ h.route }}</span>
              <span>{{ h.stop ? `${h.stop.seq}번째 ${h.stop.name}` : '경로 위' }}</span>
              <span class="d num">경로에서 {{ h.m ?? '-' }}m</span>
            </li>
          </ul>
          <p class="raw">{{ i.info }}</p>
          <button class="go" @click="emit('focus-incident', i.acc_id)">지도에서 보기</button>
        </article>
        <details v-if="longTerm.length" class="long">
          <summary class="sm">장기 공사 등 {{ longTerm.length }}건 (평소 운행시간에 들어 있음)</summary>
          <ul><li v-for="i in longTerm" :key="i.acc_id" class="sm">{{ i.dtype_name || i.type_name }} · {{ i.info }} · {{ day(i.expected_clear) }}까지</li></ul>
        </details>
      </section>

      <section class="foot">
        <ul v-if="data.warnings.length" class="warnings"><li v-for="w in data.warnings" :key="w">{{ w }}</li></ul>
        <span><b>배차는 바꾸지 않습니다.</b> 경고는 담당자 검토용입니다.</span>
        <span>소통은 차량 기준이라 중앙버스차로 구간은 버스가 덜 늦을 수 있습니다.</span>
        <span>평소 = 최근 2달 같은 요일 유형·시간대 차량통행속도 · 출처 서울 TOPIS · {{ when(data.fetched_at) }}</span>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, errorMessage } from '../api'

defineProps({
  selectedRoute: { type: String, default: null },
  focusedIncident: { type: String, default: null },
})
const emit = defineEmits(['select-route', 'loaded', 'focus-incident'])

const WARN = 0.15 // app/config.py TOPIS_EXTRA_WARN_RATIO와 같은 값
const NEAR_M = 150 // app/config.py TOPIS_ACC_NEAR_M
const MAXR = 0.25 // 막대 끝 = 평소보다 25% 더

const mode = ref('live')
const data = ref(null)
const error = ref('')
const loading = ref(false)
const snapshots = ref([])
const snapshot = ref('')

// 받은 자료(또는 실패하면 null)를 지도로 넘김
watch(data, (v) => emit('loaded', v))

const runs = computed(() =>
  (data.value?.routes ?? [])
    .map((r) => {
      const t = r.traffic
      const base = t?.usual_run || t?.usual_minutes
      const ratio = t?.extra_minutes != null && base ? t.extra_minutes / base : null
      return { route: r.route, t, ratio, hot: ratio != null && ratio >= WARN, slow: r.slow_links ?? [] }
    })
    .sort((a, b) => (b.ratio ?? -1) - (a.ratio ?? -1)),
)
const warned = computed(() => runs.value.filter((r) => r.hot))
const slowSegments = computed(() => (data.value?.routes ?? []).reduce((n, r) => n + (r.segments ?? []).filter((s) => s.ratio < 0.67).length, 0))

// 같은 돌발이 여러 노선 경로에 걸치므로 돌발 기준으로 묶음
function group(key) {
  const byId = new Map()
  for (const r of data.value?.routes ?? []) {
    for (const i of r[key] ?? []) {
      if (!byId.has(i.acc_id)) byId.set(i.acc_id, { ...i, hits: [] })
      byId.get(i.acc_id).hits.push({ route: r.route, stop: i.near_stop, m: i.distance_m })
    }
  }
  return [...byId.values()]
}
const incidents = computed(() => group('incidents'))
const longTerm = computed(() => group('long_term'))

const ICON = {
  crash: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2.5 20h19z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.4" r=".6" fill="currentColor"/></svg>',
  work: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 4h5l4 15h-13z"/><path d="M7.6 11.5h8.8M6.3 16h11.4"/><path d="M3 20h18"/></svg>',
  event: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="7" width="19" height="6" rx="1"/><path d="M7 7l-3 6M13 7l-3 6M19 7l-3 6"/><path d="M5 13v7M19 13v7"/></svg>',
}
const kindOf = (t) => (['A01', 'A02', 'A03'].includes(t) ? 'crash' : ['A06', 'A10'].includes(t) ? 'event' : 'work')
const cleanInfo = (s) => String(s || '').replace(/^\[[^\]]+\]\s*/, '').replace(/\s*\((월|화|수|목|금|토|일)\).*$/, '')

const pad = (n) => String(n).padStart(2, '0')
const asDate = (s) => (s ? new Date(s) : null)
const hm = (s) => { const d = asDate(s); return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '-' }
const day = (s) => { const d = asDate(s); return d ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` : '-' }
const when = (s) => { const d = asDate(s); return d ? `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${hm(s)}` : '-' }
// 받은 날과 같은 날이면 시각만, 다른 날(전날 발생·다음 날 해제)이면 날짜도
const at = (s) => (s && data.value && day(s) !== day(data.value.fetched_at) ? when(s) : hm(s))
function progress(i) {
  const s = asDate(i.start)
  const e = asDate(i.expected_clear)
  const now = asDate(data.value?.fetched_at)
  if (!s || !e || !now || e <= s) return 0
  return Math.max(0, Math.min(1, (now - s) / (e - s)))
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
// 패널을 닫으면 지도에서도 실시간 표시를 지움
onBeforeUnmount(() => emit('loaded', null))
</script>

<style scoped>
.traffic { font-size: 12px; display: flex; flex-direction: column; }
.traffic > section { padding: 10px 0; border-bottom: 1px solid var(--line); }
.traffic > section:last-child { border-bottom: 0; }
.traffic > section.top { padding-top: 0; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg button { font-size: 12px; padding: 3px 10px; border-right: 1px solid var(--line); min-height: 28px; color: var(--ink2); }
.seg button:last-child { border-right: 0; }
.seg button[aria-pressed='true'] { background: var(--accent-fill, var(--ink2)); color: var(--on-accent, var(--panel)); font-weight: 600; }
.stamp { color: var(--ink2); }
.stamp i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--c0); margin-right: 5px; vertical-align: 1px; }
.refresh { font-size: 12px; padding: 3px 10px; min-height: 28px; border: 1px solid var(--line); border-radius: 8px; margin-left: auto; }
.refresh:hover { background: var(--panel2); }
.refresh:disabled { opacity: 0.5; cursor: wait; }
.replay { margin-top: 8px; padding: 6px 8px; border-radius: 8px; border: 1px dashed var(--ink3); color: var(--ink2); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.replay select { font-size: 12px; min-height: 28px; max-width: 220px; }
.notice { margin-top: 8px; }
.link { margin-left: 6px; text-decoration: underline; font-size: 12px; }
.sum { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.sum > div { padding: 8px 10px; border-radius: 10px; background: var(--panel2); min-width: 0; }
.sum > div.hot { background: color-mix(in srgb, var(--c2) 16%, var(--panel)); }
.sum .v { font: 600 20px/1.2 var(--mono); }
.sum .hot .v { color: var(--c2); }
.sum .k { font-size: 11px; color: var(--ink2); }
h3.sec { margin: 0 0 8px; font-size: 12px; font-weight: 700; display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
h3.sec .sm { font-weight: 400; }
.runs { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
.run { display: grid; grid-template-columns: 46px minmax(0, 1fr) 96px; gap: 4px 10px; align-items: center; padding: 6px; border-radius: 8px; text-align: left; width: 100%; }
.run:hover { background: var(--panel2); }
.run.on { background: var(--panel2); box-shadow: inset 3px 0 0 var(--rc); }
.rno { display: inline-flex; justify-content: center; align-items: center; min-width: 44px; height: 24px; font-weight: 600; font-size: 12px; border-radius: 6px; }
.track { position: relative; height: 10px; border-radius: 5px; background: var(--panel2); }
.run:hover .track, .run.on .track { background: var(--panel); }
.fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 5px; background: var(--ink3); opacity: 0.55; }
.fill.hot { background: var(--c2); opacity: 1; }
.th { position: absolute; top: -4px; bottom: -4px; width: 0; border-left: 2px dashed var(--ink); opacity: 0.6; }
.rv { text-align: right; white-space: nowrap; font-weight: 500; }
.rv.hot { color: var(--c2); font-weight: 700; }
.detail { grid-column: 2 / 4; font-size: 11px; color: var(--ink3); display: flex; gap: 4px 10px; flex-wrap: wrap; }
.detail b { color: var(--ink2); font-weight: 500; }
.nodata { grid-column: 2 / 4; }
.flag { font-weight: 600; color: var(--c2); background: color-mix(in srgb, var(--c2) 14%, var(--panel)); border-radius: 5px; padding: 0 6px; }
.slowlist { grid-column: 2 / 4; list-style: none; margin: 4px 0 2px; padding: 6px 8px; border-radius: 8px; background: var(--panel); border: 1px solid var(--line); display: grid; gap: 3px; font-size: 11px; }
.slowlist li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
.slowlist .road { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.slowlist .num { color: var(--ink2); }
.axis { display: grid; grid-template-columns: 46px minmax(0, 1fr) 96px; gap: 10px; padding: 0 6px; font-size: 10px; color: var(--ink3); }
.axis > div { position: relative; height: 14px; }
.axis span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
.inc { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; display: grid; gap: 6px; margin-bottom: 8px; }
.inc.on { border-color: var(--accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 25%, transparent); }
.inc-h { display: flex; gap: 10px; align-items: flex-start; }
.glyph { width: 30px; height: 30px; flex: none; border-radius: 8px; background: var(--ink); color: var(--panel); display: grid; place-items: center; }
.tt { flex: 1; min-width: 0; }
.kind { font-size: 11px; color: var(--ink3); }
.name { font-weight: 600; font-size: 13px; overflow-wrap: anywhere; }
.when { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; color: var(--ink2); }
.tl { flex: 1; min-width: 80px; height: 4px; border-radius: 2px; background: var(--panel2); position: relative; overflow: hidden; }
.tl i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--ink3); }
.hits { list-style: none; margin: 0; padding: 0; display: grid; gap: 3px; }
.hits li { display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; gap: 8px; align-items: center; }
.hits .rno { min-width: 40px; height: 20px; font-size: 11px; }
.d { font-size: 11px; color: var(--ink3); }
.raw { font-size: 11px; color: var(--ink3); margin: 0; overflow-wrap: anywhere; }
.go { justify-self: start; font-size: 12px; font-weight: 600; color: var(--accent); padding: 2px 0; }
.go:hover { text-decoration: underline; }
.long summary { cursor: pointer; }
.long ul { margin: 2px 0 0; padding-left: 16px; }
.foot { display: grid; gap: 2px; font-size: 11px; color: var(--ink3); overflow-wrap: anywhere; }
.foot b { color: var(--ink2); }
.warnings { margin: 0 0 4px; padding-left: 16px; color: var(--ink2); }
.sm { font-size: 11px; color: var(--ink3); }
@media (max-width: 820px) {
  .seg button, .refresh, select { min-height: 44px; }
}
</style>
