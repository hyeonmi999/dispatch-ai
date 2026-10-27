<template>
  <v-chart class="chart" :option="option" autoresize @click="onClick" />
</template>

<script setup>
// 24시간 노선 전체 승차: 실제 vs 모델 예측 vs 베이스라인(전주 같은 요일·시간)
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'

use([LineChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, SVGRenderer])

const props = defineProps({
  hours: { type: Array, default: () => [] },
  hour: { type: Number, default: 0 },
})
const emit = defineEmits(['hour'])

const COLORS = { actual: '#1baf7a', pred: '#2a78d6', baseline: '#eb6834' }
const INK = { muted: '#898781', grid: '#e1e0d9', axis: '#c3c2b7', text: '#52514e' }

const byHour = computed(() => {
  const m = new Map(props.hours.map((h) => [h.hour, h]))
  return Array.from({ length: 24 }, (_, i) => m.get(i) || {})
})

const option = computed(() => ({
  animationDuration: 300,
  grid: { left: 48, right: 16, top: 34, bottom: 26 },
  legend: { top: 0, right: 0, icon: 'roundRect', itemWidth: 14, itemHeight: 3, textStyle: { color: INK.text } },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'line', lineStyle: { color: INK.axis } },
    valueFormatter: (v) => (v == null ? '-' : `${Math.round(v).toLocaleString()}명`),
  },
  xAxis: {
    type: 'category',
    data: Array.from({ length: 24 }, (_, i) => `${i}시`),
    axisLine: { lineStyle: { color: INK.axis } },
    axisTick: { show: false },
    axisLabel: { color: INK.muted, interval: 2 },
  },
  yAxis: {
    type: 'value',
    name: '명/시간',
    nameTextStyle: { color: INK.muted, align: 'right' },
    splitLine: { lineStyle: { color: INK.grid } },
    axisLabel: { color: INK.muted, formatter: (v) => (v >= 1000 ? `${v / 1000}k` : v) },
  },
  series: [
    {
      name: '실제',
      type: 'line',
      data: byHour.value.map((h) => h.actual ?? null),
      lineStyle: { width: 2, color: COLORS.actual },
      itemStyle: { color: COLORS.actual },
      symbol: 'circle',
      symbolSize: 4,
      showSymbol: false,
      markLine: {
        silent: true,
        symbol: 'none',
        data: [{ xAxis: props.hour }],
        lineStyle: { color: '#0b0b0b', type: 'solid', width: 1 },
        label: { formatter: `${props.hour}시`, color: '#0b0b0b' },
      },
    },
    {
      name: '모델 예측',
      type: 'line',
      data: byHour.value.map((h) => h.pred ?? null),
      lineStyle: { width: 2, color: COLORS.pred },
      itemStyle: { color: COLORS.pred },
      showSymbol: false,
    },
    {
      name: '베이스라인',
      type: 'line',
      data: byHour.value.map((h) => h.baseline ?? null),
      lineStyle: { width: 2, color: COLORS.baseline, type: 'dashed' },
      itemStyle: { color: COLORS.baseline },
      showSymbol: false,
    },
  ],
}))

function onClick(p) {
  if (p?.dataIndex != null) emit('hour', p.dataIndex)
}
</script>

<style scoped>
.chart { flex: 1; min-height: 0; }
</style>
