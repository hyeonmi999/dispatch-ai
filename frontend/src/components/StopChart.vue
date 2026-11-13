<template>
  <!-- 차트 아래 칸은 높이 제한 + 스크롤 (목록이 길어져도 차트가 찌그러지지 않게) -->
  <div class="chart-box">
    <v-chart v-if="stops.length" class="chart" :option="option" autoresize />
    <div v-else class="empty muted">이 시간대에 기점에서 출발하는 버스가 없습니다</div>
  </div>
  <div v-if="stops.length" class="chart-foot">
    <template v-if="runs.length">
      <div class="sm">혼잡 구간 {{ runs.length }}곳 (혼잡도 0.8 이상, 높은 순)</div>
      <div v-for="(r, i) in runs" :key="i" class="row">
        <b class="num">{{ i + 1 }}</b>
        <span class="ell">{{ r.from }} → {{ r.to }}</span>
        <span class="num">{{ fmtCongestion(r.peak) }}</span>
      </div>
    </template>
    <template v-else>
      <div class="sm">혼잡 구간 없음. 혼잡도 상위 3곳:</div>
      <div v-for="s in topStops" :key="s.seq" class="row">
        <i class="chip" :class="levelClass(s.level)"></i>
        <span class="ell">{{ s.name }}</span>
        <span class="num">{{ fmtCongestion(s.congestion) }}</span>
        <span class="sm">{{ s.level }}</span>
      </div>
    </template>
  </div>
</template>

<script setup>
// 정류장 순서별 구간 혼잡도
// - x축: 정류장 순서 번호, 회차 지점은 점선
// - 혼잡도 0.8 이상이 이어지는 구간은 음영 + 번호, 아래에 "시작역 → 끝역"
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { fmtCongestion, levelClass, levelColor } from '../api'
import { turnSeq } from '../routeShape'
import { FONT_MONO, FONT_SANS, palette } from '../theme'

use([BarChart, GridComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent, SVGRenderer])

const props = defineProps({ stops: { type: Array, default: () => [] } })
const HIGH = 0.8

const turnIndex = computed(() => {
  const seq = turnSeq(props.stops)
  const i = props.stops.findIndex((s) => s.seq === seq)
  return i >= 0 ? i : props.stops.length - 1
})

// 혼잡도 0.8 이상이 연속된 구간 (구간 i는 정류장 i → i+1)
const runs = computed(() => {
  const out = []
  let cur = null
  props.stops.forEach((s, i) => {
    const hot = s.congestion != null && s.congestion >= HIGH
    if (hot && !cur) cur = { start: i, end: i, peak: s.congestion }
    else if (hot) Object.assign(cur, { end: i, peak: Math.max(cur.peak, s.congestion) })
    if ((!hot || i === props.stops.length - 1) && cur) {
      const endStop = props.stops[cur.end + 1] || props.stops[cur.end]
      out.push({ ...cur, from: props.stops[cur.start].name, to: endStop.name })
      cur = null
    }
  })
  return out.sort((a, b) => b.peak - a.peak)
})

const topStops = computed(() =>
  props.stops.filter((s) => s.congestion != null).sort((a, b) => b.congestion - a.congestion).slice(0, 3),
)

const option = computed(() => {
  const t = turnIndex.value
  const stops = props.stops
  const p = palette.value
  return {
    animationDuration: 300,
    textStyle: { fontFamily: FONT_SANS },
    grid: { left: 34, right: 12, top: 18, bottom: 24 },
    tooltip: {
      // 패널이 overflow: hidden이라 툴팁은 body에 띄워 잘리지 않게
      appendTo: 'body',
      extraCssText: 'z-index: 2000; max-width: 320px; white-space: normal; border-radius: 0; box-shadow: none;',
      backgroundColor: p.panel,
      borderColor: p.ink,
      textStyle: { color: p.ink, fontSize: 12, fontFamily: FONT_SANS },
      trigger: 'item',
      formatter: (x) => {
        const s = stops[x.dataIndex]
        const next = stops[x.dataIndex + 1]
        return `<b>${s.name}${next ? ' → ' + next.name : ''}</b><br/>` +
          `${s.direction ? '→ ' + s.direction + ' 방면 · ' : ''}혼잡도 ${fmtCongestion(s.congestion)} (${s.level || '-'})` +
          `<br/>예측 승차 ${s.pred ?? '-'}명` + (s.actual != null ? ` · 실제 ${s.actual}명` : '')
      },
    },
    xAxis: {
      type: 'category',
      data: stops.map((s) => s.seq),
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
        type: 'bar',
        barCategoryGap: '12%',
        data: stops.map((s) => ({ value: s.congestion, itemStyle: { color: levelColor(s.level) } })),
        markLine: {
          silent: true,
          symbol: 'none',
          data: [
            { yAxis: HIGH, lineStyle: { color: p.accent, type: 'dashed' }, label: { formatter: '증차 기준 0.8', position: 'insideEndTop', color: p.accent, fontSize: 11 } },
            { xAxis: t, lineStyle: { color: p.ink3, type: 'dotted' }, label: { formatter: '회차', position: 'end', color: p.ink2, fontSize: 11 } },
          ],
        },
        markArea: {
          silent: true,
          itemStyle: { color: p.accent, opacity: 0.1 },
          data: runs.value.map((r, i) => [
            { xAxis: r.start, name: String(i + 1) },
            { xAxis: Math.min(r.end + 1, stops.length - 1) },
          ]),
          label: { position: 'insideTop', color: p.accent, fontWeight: 700, fontFamily: FONT_MONO },
        },
      },
    ],
  }
})
</script>

<style scoped>
.chart-box { position: relative; flex: 1; min-height: 0; min-width: 0; }
.chart { position: absolute; inset: 0; }
.empty { position: absolute; inset: 0; display: grid; place-items: center; font-size: 13px; }
.row { display: flex; align-items: center; gap: 7px; }
.row b { color: var(--accent); font-weight: 600; }
.ell { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
