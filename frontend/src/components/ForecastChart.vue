<template>
  <!-- 차트는 박스 안에 절대 위치: SVG 고정 크기가 레이아웃을 다시 키우는 피드백 방지 -->
  <div ref="boxRef" class="chart-box" @click="onPlotClick">
    <v-chart ref="chartRef" class="chart" :option="option" autoresize />
  </div>
</template>

<script setup>
// 24시간 노선 전체 승차: 실제 vs 모델 예측 vs 베이스라인(작년 같은 요일·시간)
import { computed, ref } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { FONT_MONO, FONT_SANS, palette } from '../theme'

use([LineChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, SVGRenderer])

const props = defineProps({
  hours: { type: Array, default: () => [] },
  hour: { type: Number, default: 0 },
})
const emit = defineEmits(['hour'])

const byHour = computed(() => {
  const m = new Map(props.hours.map((h) => [h.hour, h]))
  return Array.from({ length: 24 }, (_, i) => m.get(i) || {})
})

const option = computed(() => {
  const p = palette.value
  return {
    animationDuration: 300,
    textStyle: { fontFamily: FONT_SANS },
    grid: { left: 44, right: 12, top: 30, bottom: 24 },
    legend: { top: 0, left: 0, icon: 'rect', itemWidth: 14, itemHeight: 2, itemGap: 10, textStyle: { color: p.ink2, fontSize: 11 } },
    tooltip: {
      // 패널이 overflow: hidden이라 툴팁은 body에 띄워 잘리지 않게
      appendTo: 'body',
      extraCssText: 'z-index: 2000; max-width: 320px; white-space: normal; border-radius: 0; box-shadow: none;',
      backgroundColor: p.panel,
      borderColor: p.ink,
      textStyle: { color: p.ink, fontSize: 12, fontFamily: FONT_SANS },
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: p.ink3 } },
      valueFormatter: (v) => (v == null ? '-' : `${Math.round(v).toLocaleString()}명`),
    },
    xAxis: {
      type: 'category',
      data: Array.from({ length: 24 }, (_, i) => `${i}시`),
      axisLine: { lineStyle: { color: p.line } },
      axisTick: { show: false },
      axisLabel: { color: p.ink3, interval: 5, fontFamily: FONT_MONO, fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: p.line } },
      axisLabel: { color: p.ink3, fontFamily: FONT_MONO, fontSize: 11, formatter: (v) => v.toLocaleString() },
    },
    series: [
      {
        name: '모델 예측',
        type: 'line',
        data: byHour.value.map((h) => h.pred ?? null),
        lineStyle: { width: 2.5, color: p.accent },
        itemStyle: { color: p.accent },
        showSymbol: false,
        // 선택 시간 표시선은 항상 값이 있는 예측 계열에 붙임 (향후 예측 날짜는 실제값이 없음)
        markLine: {
          silent: true,
          symbol: 'none',
          data: [{ xAxis: props.hour }],
          lineStyle: { color: p.ink, type: 'dotted', width: 1 },
          label: { show: false },
        },
      },
      {
        name: '실제',
        type: 'line',
        data: byHour.value.map((h) => h.actual ?? null),
        lineStyle: { width: 1.75, color: p.ink },
        itemStyle: { color: p.ink },
        symbol: 'circle',
        symbolSize: 4,
        showSymbol: false,
      },
      {
        name: '작년 같은 요일',
        type: 'line',
        data: byHour.value.map((h) => h.baseline ?? null),
        lineStyle: { width: 1.5, color: p.ink3, type: 'dashed' },
        itemStyle: { color: p.ink3 },
        showSymbol: false,
      },
    ],
  }
})

// 선 위가 아니어도 그래프 영역 어디를 클릭하든 가장 가까운 시간으로 이동
const chartRef = ref(null)
const boxRef = ref(null)
function onPlotClick(e) {
  const chart = chartRef.value
  const r = boxRef.value?.getBoundingClientRect()
  if (!chart || !r) return
  const px = [e.clientX - r.left, e.clientY - r.top]
  if (!chart.containPixel('grid', px)) return
  const [idx] = chart.convertFromPixel({ gridIndex: 0 }, px)
  if (idx >= 0 && idx <= 23) emit('hour', Math.round(idx))
}
</script>

<style scoped>
.chart-box { position: relative; flex: 1; min-height: 0; min-width: 0; }
.chart { position: absolute; inset: 0; cursor: pointer; }
</style>
