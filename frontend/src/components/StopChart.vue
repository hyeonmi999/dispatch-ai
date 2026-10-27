<template>
  <v-chart v-if="stops.length" class="chart" :option="option" autoresize />
  <div v-else class="empty muted">이 시간대 운행 데이터가 없습니다</div>
</template>

<script setup>
// 정류장 순서별 구간 혼잡도 (막대 색 = 혼잡 단계, 0.8 증차 기준선)
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { levelColor } from '../api'

use([BarChart, GridComponent, MarkLineComponent, TooltipComponent, SVGRenderer])

const props = defineProps({ stops: { type: Array, default: () => [] } })
const INK = { muted: '#898781', grid: '#e1e0d9', axis: '#c3c2b7' }

const option = computed(() => ({
  animationDuration: 300,
  grid: { left: 40, right: 16, top: 24, bottom: 26 },
  tooltip: {
    trigger: 'item',
    formatter: (p) => {
      const s = props.stops[p.dataIndex]
      return `<b>${s.seq}. ${s.name}</b><br/>혼잡도 ${s.congestion != null ? s.congestion.toFixed(2) : '-'} (${s.level || '-'})<br/>예측 승차 ${s.pred ?? '-'}명` +
        (s.actual != null ? ` · 실제 ${s.actual}명` : '')
    },
  },
  xAxis: {
    type: 'category',
    data: props.stops.map((s) => s.seq),
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
      data: props.stops.map((s) => ({
        value: s.congestion,
        itemStyle: { color: levelColor(s.level), borderRadius: [2, 2, 0, 0] },
      })),
      markLine: {
        silent: true,
        symbol: 'none',
        data: [{ yAxis: 0.8 }],
        lineStyle: { color: '#d03b3b', type: 'dashed' },
        label: { formatter: '증차 기준 0.8', position: 'insideEndTop', color: '#8f1f1f' },
      },
    },
  ],
}))
</script>

<style scoped>
.chart { flex: 1; min-height: 0; }
.empty { flex: 1; display: grid; place-items: center; font-size: 13px; }
</style>
