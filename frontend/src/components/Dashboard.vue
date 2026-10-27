<template>
  <div class="app">
    <!-- 상단: 서비스명 · 도메인 선택 · LLM 선택 -->
    <header class="topbar">
      <div class="brand">
        <span class="logo">🚌</span>
        <div>
          <div class="title">버스 배차 최적화 도우미</div>
          <div class="sub">서비스·물류 배차 AI · Oh!gimbak</div>
        </div>
      </div>

      <n-radio-group v-model:value="domain" size="small" class="domains">
        <n-radio-button v-for="d in domains" :key="d.name" :value="d.name">
          {{ d.title }}<span v-if="d.status !== 'ready'" class="soon">준비 중</span>
        </n-radio-button>
      </n-radio-group>

      <div class="options">
        <span class="opt-label">LLM</span>
        <n-select v-model:value="llm" :options="llmOptions" size="small" style="width: 170px" />
        <span class="opt-label">임베딩</span>
        <n-select v-model:value="embedding" :options="embeddingOptions" size="small" style="width: 150px" />
        <n-tag v-if="meta" size="small" :type="meta.data_mode === 'real' ? 'success' : 'warning'" round>
          {{ meta.data_mode === 'real' ? '실데이터' : '합성 데이터' }}
        </n-tag>
        <n-tag size="small" :type="health?.db?.available ? 'success' : 'default'" round>
          DB {{ health?.db?.available ? '연결' : '미연결' }}
        </n-tag>
      </div>
    </header>

    <ComingSoon v-if="domain !== 'bus'" :domain="currentDomain" />

    <main v-else-if="meta" class="grid">
      <!-- 좌측: 날짜·시간 · 노선 목록 · 모델 성능 -->
      <aside class="left">
        <section class="block">
          <div class="block-title">조회 시점</div>
          <n-date-picker
            v-model:formatted-value="date"
            value-format="yyyy-MM-dd"
            type="date"
            :is-date-disabled="isDateDisabled"
            style="width: 100%"
          />
          <div class="date-tags">
            <n-tag v-if="isForecastDate" size="small" type="info" round>향후 예측 (실측 없음)</n-tag>
            <n-tag v-else-if="isValidDate" size="small" round>검증 구간 · 실측과 비교</n-tag>
            <n-tag v-else size="small" round>학습 구간</n-tag>
            <span class="muted small">{{ dayLabel }}</span>
          </div>
          <div class="hour-row">
            <n-button size="small" circle @click="togglePlay" :title="playing ? '정지' : '시간대 재생'">
              {{ playing ? '❚❚' : '▶' }}
            </n-button>
            <n-slider v-model:value="hour" :min="0" :max="23" :step="1" :format-tooltip="(v) => `${v}시`" style="flex: 1" />
            <span class="hour-label num">{{ String(hour).padStart(2, '0') }}시</span>
          </div>
        </section>

        <section class="block">
          <div class="block-title">노선 ({{ hour }}시 최대 혼잡도)</div>
          <div
            v-for="r in routeRows"
            :key="r.route"
            class="route-item"
            :class="{ active: r.route === selected }"
            @click="selected = r.route"
          >
            <span class="dot" :style="{ background: levelColor(r.level) }"></span>
            <div class="route-main">
              <div><b>{{ r.route }}</b> <span class="muted small">{{ r.route_type }}</span></div>
              <div class="muted small ellipsis">{{ r.start }} ↔ {{ r.end }}</div>
            </div>
            <div class="route-side">
              <div class="num">{{ fmtCongestion(r.max_congestion) }}</div>
              <div class="small" :style="{ color: '#52514e' }">{{ r.level || '-' }}</div>
            </div>
          </div>
        </section>

        <MetricsCard :metrics="meta.metrics" />
      </aside>

      <!-- 가운데: 지도 + 차트 -->
      <section class="center">
        <div class="map-wrap">
          <MapView :geometries="geometries" :overview="overview" :selected="selected" @select="(r) => (selected = r)" />
          <div v-if="hotspotCount" class="map-banner">
            🔴 {{ hour }}시 혼잡 구간 {{ hotspotCount }}곳
          </div>
        </div>
        <div class="charts">
          <div class="chart-card">
            <div class="chart-title">{{ selected }}번 시간대별 승차 인원 (노선 전체)</div>
            <ForecastChart :hours="timeseries?.hours || []" :hour="hour" @hour="(h) => (hour = h)" />
          </div>
          <div class="chart-card">
            <div class="chart-title">{{ selected }}번 {{ hour }}시 정류장 구간별 혼잡도</div>
            <StopChart :stops="stops?.stops || []" />
          </div>
        </div>
      </section>

      <!-- 우측: 선택 노선 현황 · 권고 · Q&A · 이력 -->
      <aside class="right">
        <RoutePanel
          :route="selectedMeta"
          :date="date"
          :hour="hour"
          :hour-row="hourRow"
          :llm="llm"
          :embedding="embedding"
          :db-available="!!health?.db?.available"
        />
      </aside>
    </main>

    <div v-else class="loading">
      <n-spin v-if="!loadError" description="모델·데이터 불러오는 중" />
      <n-alert v-else type="error" title="백엔드 연결 실패">{{ loadError }}<br />FastAPI 서버(포트 8000)가 실행 중인지 확인하세요.</n-alert>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert, NButton, NDatePicker, NRadioButton, NRadioGroup, NSelect, NSlider, NSpin, NTag, useMessage,
} from 'naive-ui'
import { api, errorMessage, fmtCongestion, levelColor } from '../api'
import ComingSoon from './ComingSoon.vue'
import ForecastChart from './ForecastChart.vue'
import MapView from './MapView.vue'
import MetricsCard from './MetricsCard.vue'
import RoutePanel from './RoutePanel.vue'
import StopChart from './StopChart.vue'

const message = useMessage()

const domains = ref([{ name: 'bus', title: '버스 배차', status: 'ready' }])
const domain = ref('bus')
const llm = ref('qwen')
const embedding = ref('bge')
const llmOptions = [
  { label: 'Qwen3-1.7B (로컬)', value: 'qwen' },
  { label: 'OpenAI gpt-4o-mini', value: 'openai' },
]
const embeddingOptions = [
  { label: 'bge-m3 (로컬)', value: 'bge' },
  { label: 'OpenAI 3-small', value: 'openai' },
]

const meta = ref(null)
const health = ref(null)
const loadError = ref('')
const geometries = ref({})
const overview = ref(null)
const timeseries = ref(null)
const stops = ref(null)

const date = ref(null)
const hour = ref(7)
const selected = ref(null)
const playing = ref(false)
let timer = null

const missingDates = computed(() => new Set(meta.value?.dates.missing_dates || []))
const currentDomain = computed(() => domains.value.find((d) => d.name === domain.value))
const selectedMeta = computed(() => meta.value?.routes.find((r) => r.route === selected.value))
const hourRow = computed(() => timeseries.value?.hours.find((h) => h.hour === hour.value) || null)
const isForecastDate = computed(() => meta.value && date.value > meta.value.dates.max_date)
const isValidDate = computed(() => meta.value && date.value >= meta.value.dates.valid_start)
const dayLabel = computed(() => {
  if (!date.value) return ''
  const d = new Date(date.value + 'T00:00:00')
  const map = { weekday: '평일', saturday: '토요일', holiday: '일요일·공휴일' }
  return `${'일월화수목금토'[d.getDay()]}요일 · ${map[timeseries.value?.daytype] || ''}`
})

const routeRows = computed(() =>
  (meta.value?.routes || []).map((r) => {
    const o = overview.value?.routes.find((x) => x.route === r.route) || {}
    return { ...r, level: o.level, max_congestion: o.max_congestion }
  }),
)
const hotspotCount = computed(() =>
  (overview.value?.routes || []).reduce((n, r) => n + r.segments.filter((s) => s.congestion >= 0.8).length, 0),
)

function isDateDisabled(ts) {
  if (!meta.value) return true
  const d = new Date(ts)
  const s = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return s < meta.value.dates.min_date || s > meta.value.dates.max_forecast_date || missingDates.value.has(s)
}

// 기본 날짜: 데이터 마지막 날 이전의 가장 최근 화요일 (평일 출근 혼잡을 바로 보이도록)
function defaultDate(maxDate) {
  const d = new Date(maxDate + 'T00:00:00')
  const fmt = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`
  while (d.getDay() !== 2 || missingDates.value.has(fmt(d))) d.setDate(d.getDate() - 1)
  return fmt(d)
}

// 노선·시간을 빠르게 바꾸면 응답 순서가 뒤바뀔 수 있어, 가장 마지막 요청의 응답만 반영
const latest = { overview: 0, timeseries: 0, stops: 0 }
async function fetchLatest(key, target, request, onError) {
  const id = ++latest[key]
  try {
    const data = await request()
    if (id === latest[key]) target.value = data
  } catch (e) {
    if (id === latest[key]) {
      target.value = null
      onError?.(e)
    }
  }
}
function loadOverview() {
  if (!date.value) return
  fetchLatest('overview', overview, () => api.overview(date.value, hour.value), (e) => message.warning(errorMessage(e)))
}
function loadTimeseries() {
  if (!date.value || !selected.value) return
  fetchLatest('timeseries', timeseries, () => api.timeseries(selected.value, date.value), (e) => message.warning(errorMessage(e)))
}
function loadStops() {
  if (!date.value || !selected.value) return
  fetchLatest('stops', stops, () => api.stops(selected.value, date.value, hour.value))
}

function togglePlay() {
  playing.value = !playing.value
  if (playing.value) {
    timer = setInterval(() => {
      hour.value = hour.value >= 23 ? 4 : hour.value + 1
    }, 1800)
  } else {
    clearInterval(timer)
  }
}

watch([date, hour], loadOverview)
watch([date, hour, selected], loadStops)
watch([date, selected], loadTimeseries)

onMounted(async () => {
  try {
    const [ds, h] = await Promise.all([api.domains(), api.health()])
    domains.value = ds
    health.value = h
    meta.value = await api.busMeta()
    const geos = await Promise.all(meta.value.routes.map((r) => api.geometry(r.route).catch(() => null)))
    geometries.value = Object.fromEntries(geos.filter(Boolean).map((g) => [g.route, g]))
    selected.value = meta.value.routes[0]?.route
    date.value = defaultDate(meta.value.dates.max_date)
  } catch (e) {
    loadError.value = errorMessage(e)
  }
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.app { display: flex; flex-direction: column; height: 100vh; }
.topbar {
  display: flex; align-items: center; gap: 20px; padding: 10px 18px;
  background: var(--surface); border-bottom: 1px solid var(--line);
}
.brand { display: flex; align-items: center; gap: 10px; min-width: 240px; }
.logo { font-size: 26px; }
.title { font-weight: 700; font-size: 17px; }
.sub { font-size: 12px; color: var(--muted); }
.domains .soon { margin-left: 6px; font-size: 11px; color: var(--muted); }
.options { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.opt-label { font-size: 12px; color: var(--ink-2); }

.grid {
  flex: 1; min-height: 0;
  display: grid; grid-template-columns: 290px minmax(0, 1fr) 400px; gap: 12px; padding: 12px;
}
.left, .right { min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.block { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 12px; }
.block-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; color: var(--ink-2); }
.date-tags { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
.small { font-size: 12px; }
.hour-row { display: flex; align-items: center; gap: 10px; }
.hour-label { font-weight: 700; font-size: 18px; width: 44px; text-align: right; }

.route-item {
  display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 8px; cursor: pointer;
  border: 1px solid transparent;
}
.route-item:hover { background: #f1f0ec; }
.route-item.active { border-color: var(--accent); background: #eef4fc; }
.dot { width: 12px; height: 12px; border-radius: 50%; flex: none; }
.route-main { flex: 1; min-width: 0; }
.route-side { text-align: right; }
.ellipsis { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.center { min-height: 0; display: flex; flex-direction: column; gap: 12px; }
.map-wrap { position: relative; flex: 1; min-height: 320px; border-radius: 10px; overflow: hidden; border: 1px solid var(--line); }
.map-banner {
  position: absolute; top: 10px; left: 50%; transform: translateX(-50%); z-index: 500;
  background: rgba(255, 255, 255, 0.95); border: 1px solid #f0c9c9; color: #8f1f1f;
  padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; white-space: nowrap;
}
.charts {
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); grid-template-rows: minmax(0, 1fr);
  gap: 12px; height: 290px; flex: none;
}
.chart-card {
  background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px;
  display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden;
}
.chart-title { font-size: 13px; font-weight: 600; color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.loading { flex: 1; display: grid; place-items: center; padding: 40px; }

@media (max-width: 1200px) {
  .grid { grid-template-columns: 1fr; overflow-y: auto; }
  .map-wrap { min-height: 420px; }
  .charts { grid-template-columns: minmax(0, 1fr); grid-template-rows: 290px 290px; height: auto; }
  .topbar { flex-wrap: wrap; }
}
</style>
