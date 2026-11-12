<template>
  <!-- 배차간격을 바꿔 보면 혼잡도가 어떻게 되는지 (F-12 시뮬레이션) -->
  <div v-if="!route" class="empty-note">노선을 고르면 배차간격을 바꿔 볼 수 있습니다.</div>
  <div v-else>
    <div class="head">
      <span class="rno on" :class="`r${route.route}`">{{ route.route }}</span>
      <span class="sm">{{ date }} {{ hour }}시 · 최대 혼잡 구간 기준</span>
      <n-spin v-if="loading" :size="14" class="spin" />
    </div>

    <div v-if="error" class="notice warn" role="alert">{{ error }}</div>
    <p v-else-if="empty" class="empty-note">{{ empty }}</p>
    <p v-else-if="!result" class="empty-note">불러오는 중</p>

    <template v-else>
      <div class="ctl">
        <span class="lbl">배차간격</span>
        <n-slider
          :value="interval"
          :min="result.bounds.min_interval"
          :max="result.bounds.max_interval"
          :step="0.1"
          :format-tooltip="(v) => `${v}분`"
          style="flex: 1; min-width: 0"
          @update:value="onSlide"
        />
        <b class="num iv">{{ interval?.toFixed(1) }}<small>분</small></b>
      </div>
      <div class="sub">
        <span class="sm">노선 허용 {{ result.bounds.min_interval }}~{{ result.bounds.max_interval }}분</span>
        <button class="reset" :disabled="isNow" @click="reset">지금 간격으로</button>
      </div>

      <div class="cmp">
        <div>
          <div class="cap">지금</div>
          <div class="big num">{{ fmtCongestion(result.current.max_congestion) }}</div>
          <div class="lv"><i class="chip" :class="levelClass(result.current.level)"></i>{{ result.current.level }}</div>
          <span class="meter"><i :class="levelClass(result.current.level)" :style="{ width: pct(result.current.max_congestion) }"></i></span>
          <div class="sm num">{{ result.current.interval.toFixed(1) }}분 · 시간당 {{ trips(result.current.trips_per_hour) }}회</div>
        </div>
        <div class="arrow" aria-hidden="true">→</div>
        <div>
          <div class="cap">바꾼 뒤 <span class="delta num" :class="{ up: delta > 0 }">{{ deltaText }}</span></div>
          <div class="big num">{{ fmtCongestion(after.max_congestion) }}</div>
          <div class="lv"><i class="chip" :class="levelClass(after.level)"></i>{{ after.level }}</div>
          <span class="meter"><i :class="levelClass(after.level)" :style="{ width: pct(after.max_congestion) }"></i></span>
          <div class="sm num">{{ after.interval.toFixed(1) }}분 · 시간당 {{ trips(after.trips_per_hour) }}회</div>
        </div>
      </div>

      <div class="sm seg-title">구간별 혼잡도 · 가장 붐비는 곳: {{ result.peak_segment.from_name }}</div>
      <div class="chart-box">
        <v-chart class="chart" :option="option" autoresize />
      </div>

      <p class="note">{{ result.assumption }}. 계산 결과는 보조 자료이며 담당자 검토가 필요합니다.</p>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NSlider, NSpin } from 'naive-ui'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { api, errorMessage, fmtCongestion, levelClass, levelColor } from '../api'
import { FONT_MONO, FONT_SANS, palette } from '../theme'

use([BarChart, LineChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, SVGRenderer])

const props = defineProps({
  route: { type: Object, default: null },
  date: { type: String, default: null },
  hour: { type: Number, default: 0 },
})

const HIGH = 0.8
const result = ref(null)
const interval = ref(null)
const loading = ref(false)
const error = ref('')
const empty = ref('')
let latest = 0
let timer = null

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
const round1 = (v) => Math.round(v * 10) / 10
const pct = (c) => `${Math.min(100, (c ?? 0) * 100)}%`
const trips = (v) => (Number.isInteger(v) ? v : v?.toFixed(1))

// 지금 간격을 슬라이더 범위 안의 값으로 (지금 간격이 허용 범위 밖인 날도 있음)
function nowInterval(data) {
  return round1(clamp(data?.current?.interval ?? 0, data?.bounds?.min_interval ?? 0, data?.bounds?.max_interval ?? 0))
}
const nowInBounds = (data) =>
  data.current.interval >= data.bounds.min_interval && data.current.interval <= data.bounds.max_interval
// 슬라이더가 지금 간격에 있으면 '바꾼 뒤'는 지금 값 그대로 (슬라이더가 0.1분 단위라 반올림 차이가 보이지 않게)
const isNow = computed(() => !!result.value && nowInBounds(result.value) && interval.value === nowInterval(result.value))
const after = computed(() => (isNow.value ? result.value.current : result.value.simulated))
const segments = computed(() => {
  const segs = result.value?.segments ?? []
  return isNow.value ? segs.map((s) => ({ ...s, congestion: s.congestion_before, level: s.level_before })) : segs
})
const delta = computed(() => (result.value ? after.value.max_congestion - result.value.current.max_congestion : 0))
const deltaText = computed(() => {
  const d = Math.round(delta.value * 100) / 100
  return d === 0 ? '변화 없음' : `${d > 0 ? '+' : '−'}${Math.abs(d).toFixed(2)}`
})

async function run(value) {
  if (!props.route || !props.date) return null
  const id = ++latest
  loading.value = true
  try {
    const data = await api.simulate({ route: props.route.route, date: props.date, hour: props.hour, interval: value })
    if (id !== latest) return null
    result.value = data
    error.value = ''
    empty.value = ''
    return data
  } catch (e) {
    if (id !== latest) return null
    result.value = null
    // 422: 그 시간대에 운행이 없음 (오류가 아니라 빈 상태)
    if (e?.response?.status === 422) empty.value = errorMessage(e)
    else error.value = errorMessage(e)
    return null
  } finally {
    if (id === latest) loading.value = false
  }
}

// 노선·날짜·시간이 바뀌면 지금 간격에서 다시 시작
async function start() {
  clearTimeout(timer)
  result.value = null
  error.value = ''
  empty.value = ''
  if (!props.route || !props.date) return
  const lo = props.route.min_interval ?? 1
  const hi = props.route.max_interval ?? 60
  const first = await run(clamp(props.route.interval_weekday ?? lo, lo, hi))
  if (!first) return
  const now = nowInterval(first)
  interval.value = now
  // 지금 간격이 허용 범위 밖이면 가장 가까운 허용 간격으로 계산해서 보여 줌
  if (!nowInBounds(first) && now !== first.simulated.interval) await run(now)
}

function onSlide(value) {
  interval.value = value
  clearTimeout(timer)
  timer = setTimeout(() => run(value), 150)
}
function reset() {
  if (!result.value) return
  clearTimeout(timer)
  interval.value = nowInterval(result.value)
  if (!nowInBounds(result.value)) run(interval.value)
}

watch(() => [props.route?.route, props.date, props.hour], start, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))

const option = computed(() => {
  const p = palette.value
  const segs = segments.value
  return {
    animationDuration: 200,
    textStyle: { fontFamily: FONT_SANS },
    grid: { left: 30, right: 8, top: 24, bottom: 20 },
    legend: { top: 0, left: 0, itemWidth: 14, itemHeight: 3, itemGap: 10, textStyle: { color: p.ink2, fontSize: 11 } },
    tooltip: {
      appendTo: 'body',
      extraCssText: 'z-index: 2000; max-width: 320px; white-space: normal; border-radius: 0; box-shadow: none;',
      backgroundColor: p.panel,
      borderColor: p.ink,
      textStyle: { color: p.ink, fontSize: 12, fontFamily: FONT_SANS },
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: p.ink3 } },
      formatter: (items) => {
        const s = segs[items?.[0]?.dataIndex]
        if (!s) return ''
        return `<b>${s.from_name}</b> (${s.seq}번째)<br/>지금 ${fmtCongestion(s.congestion_before)} → 바꾼 뒤 ${fmtCongestion(s.congestion)} (${s.level})`
      },
    },
    xAxis: {
      type: 'category',
      data: segs.map((s) => s.seq),
      axisLine: { lineStyle: { color: p.line } },
      axisTick: { show: false },
      axisLabel: { color: p.ink3, interval: 19, fontFamily: FONT_MONO, fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: p.line } },
      axisLabel: { color: p.ink3, fontFamily: FONT_MONO, fontSize: 11 },
    },
    series: [
      {
        name: '바꾼 뒤',
        type: 'bar',
        barCategoryGap: '12%',
        itemStyle: { color: p.c1 },
        data: segs.map((s) => ({ value: s.congestion, itemStyle: { color: levelColor(s.level) } })),
        markLine: {
          silent: true,
          symbol: 'none',
          data: [{ yAxis: HIGH, lineStyle: { color: p.accent, type: 'dashed' }, label: { formatter: '0.8', position: 'insideEndTop', color: p.accent, fontSize: 11 } }],
        },
      },
      {
        name: '지금',
        type: 'line',
        step: 'middle',
        showSymbol: false,
        lineStyle: { width: 1.5, color: p.ink },
        itemStyle: { color: p.ink },
        data: segs.map((s) => s.congestion_before),
      },
    ],
  }
})
</script>

<style scoped>
.head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.head .rno { font-size: 17px; }
.spin { margin-left: auto; }
.ctl { display: flex; align-items: center; gap: 10px; }
.lbl { font-size: 11px; color: var(--ink3); white-space: nowrap; }
.iv { font-size: 19px; font-weight: 500; white-space: nowrap; min-width: 62px; text-align: right; }
.iv small { font-size: 11px; font-weight: 400; color: var(--ink3); margin-left: 2px; }
.sub { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin: 2px 0 8px; }
.reset { font-size: 12px; text-decoration: underline; text-underline-offset: 2px; color: var(--ink2); min-height: 24px; }
.reset:disabled { opacity: 0.4; cursor: default; text-decoration: none; }
.cmp { display: grid; grid-template-columns: 1fr auto 1fr; gap: 0 10px; align-items: center; padding: 8px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.cmp > div { min-width: 0; }
.cap { font-size: 11px; color: var(--ink3); display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
.big { font-size: 24px; font-weight: 500; line-height: 1.2; }
.lv { display: flex; align-items: center; gap: 5px; font-size: 12px; }
.arrow { font-size: 16px; color: var(--ink3); }
.delta { color: var(--ink2); font-weight: 600; }
.delta.up { color: var(--accent); }
.meter { margin: 4px 0; }
.seg-title { margin-top: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chart-box { position: relative; height: 150px; min-width: 0; }
.chart { position: absolute; inset: 0; }
.note { margin: 6px 0 0; padding-top: 6px; border-top: 1px solid var(--line); font-size: 11px; color: var(--ink2); }
</style>
