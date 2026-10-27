<template>
  <!-- 차트 영역은 고정, 혼잡 구간 목록은 아래 고정 높이 칸에서 스크롤 (목록이 길어져도 차트 크기 유지) -->
  <div class="chart-box">
    <v-chart v-if="stops.length" class="chart" :option="option" autoresize />
    <div v-else class="empty muted">이 시간대 운행 데이터가 없습니다</div>
  </div>
  <div class="chart-foot">
    <template v-if="runs.length">
      <div class="muted foot-head">혼잡 구간 {{ runs.length }}곳 (혼잡도 0.8 이상, 높은 순)</div>
      <div v-for="(r, i) in runs" :key="i" class="run">
        <b>{{ BADGES[i] || i + 1 + '.' }}</b> {{ r.from }} → {{ r.to }} <span class="muted num">최대 {{ fmtCongestion(r.peak) }}</span>
      </div>
    </template>
    <span v-else-if="stops.length" class="muted">혼잡 구간(0.8 이상) 없음</span>
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
import { fmtCongestion, levelColor } from '../api'
import { turnSeq } from '../routeShape'

use([BarChart, GridComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent, SVGRenderer])

const props = defineProps({ stops: { type: Array, default: () => [] } })
const INK = { muted: '#898781', grid: '#e1e0d9', axis: '#c3c2b7', text: '#52514e' }
const HIGH = 0.8
const BADGES = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩']

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

const option = computed(() => {
  const t = turnIndex.value
  const stops = props.stops
  return {
    animationDuration: 300,
    grid: { left: 40, right: 16, top: 24, bottom: 30 },
    tooltip: {
      // 차트 카드는 크기 고정을 위해 overflow: hidden → 툴팁은 body에 띄워 잘리지 않게
      appendTo: 'body',
      extraCssText: 'z-index: 2000; max-width: 320px; white-space: normal;',
      trigger: 'item',
      formatter: (p) => {
        const s = stops[p.dataIndex]
        const next = stops[p.dataIndex + 1]
        return `<b>${s.name}${next ? ' → ' + next.name : ''}</b><br/>` +
          `${s.direction ? '→ ' + s.direction + ' 방면 · ' : ''}혼잡도 ${fmtCongestion(s.congestion)} (${s.level || '-'})` +
          `<br/>예측 승차 ${s.pred ?? '-'}명` + (s.actual != null ? ` · 실제 ${s.actual}명` : '')
      },
    },
    xAxis: {
      type: 'category',
      data: stops.map((s) => s.seq),
      axisLine: { lineStyle: { color: INK.axis } },
      axisTick: { show: false },
      axisLabel: { color: INK.muted, interval: 9 },
      name: '정류장 순서',
      nameLocation: 'middle',
      nameGap: 18,
      nameTextStyle: { color: INK.muted, fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      name: '혼잡도',
      nameTextStyle: { color: INK.muted, align: 'right' },
      splitLine: { lineStyle: { color: INK.grid } },
      axisLabel: { color: INK.muted },
    },
    series: [
      {
        type: 'bar',
        barCategoryGap: '15%',
        data: stops.map((s) => ({
          value: s.congestion,
          itemStyle: { color: levelColor(s.level), borderRadius: [2, 2, 0, 0] },
        })),
        markLine: {
          silent: true,
          symbol: 'none',
          data: [
            { yAxis: HIGH, lineStyle: { color: '#d03b3b', type: 'dashed' }, label: { formatter: '증차 기준 0.8', position: 'insideEndTop', color: '#8f1f1f' } },
            { xAxis: t, lineStyle: { color: '#898781', type: 'dotted' }, label: { formatter: '회차', position: 'end', color: INK.text } },
          ],
        },
        markArea: {
          silent: true,
          itemStyle: { color: 'rgba(208, 59, 59, 0.10)' },
          data: runs.value.map((r, i) => [
            { xAxis: r.start, name: BADGES[i] || '' },
            { xAxis: Math.min(r.end + 1, stops.length - 1) },
          ]),
          label: { position: 'insideTop', color: '#8f1f1f', fontWeight: 700 },
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
.foot-head { font-size: 11px; }
.run { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.run b { color: #8f1f1f; }
</style>
