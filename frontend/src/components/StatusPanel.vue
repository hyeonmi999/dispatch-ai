<template>
  <div v-if="route">
    <div class="head">
      <span class="rno on" :class="`r${route.route}`">{{ route.route }}</span>
      <span class="sm">{{ route.route_type }} · {{ route.start }} ↔ {{ route.end }} · {{ route.company }}</span>
    </div>
    <dl class="kv">
      <div>
        <dt>예측 승차</dt>
        <dd class="num">{{ n(hourRow?.pred) }}<small>명/h</small></dd>
      </div>
      <div>
        <dt>실제 승차</dt>
        <dd class="num">{{ hourRow?.actual != null ? n(hourRow.actual) : '없음' }}<small v-if="hourRow?.actual != null">명/h</small></dd>
      </div>
      <div>
        <dt>운행</dt>
        <dd class="num">{{ hourRow?.trips ?? '-' }}<small>회/h</small></dd>
      </div>
      <div>
        <dt>배차 간격 (기준 {{ route.min_interval }}~{{ route.max_interval }}분)</dt>
        <dd class="num">{{ interval }}<small>분</small></dd>
      </div>
      <div>
        <dt>최대 혼잡도 · {{ hourRow?.level || '-' }}</dt>
        <dd class="num">{{ fmtCongestion(hourRow?.max_congestion) }}</dd>
        <span class="meter"><i :class="levelClass(hourRow?.level)" :style="{ width: `${pct}%` }"></i></span>
      </div>
      <div>
        <dt>인가 대수 · {{ route.first_bus }}~{{ route.last_bus }}</dt>
        <dd class="num">{{ route.authorized_buses ?? '-' }}<small>대</small></dd>
      </div>
    </dl>
    <p class="need"><b :class="{ hi: need.high }">추가 배차 필요성: {{ need.label }}</b><br />{{ need.text }}</p>
    <p class="sm base">작년 같은 요일 {{ n(hourRow?.baseline) }}명/h · 왕복 {{ route.run_minutes }}분 · {{ hour }}시 기준</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtCongestion, levelClass } from '../api'

const props = defineProps({
  route: { type: Object, default: null },
  hour: { type: Number, default: 0 },
  hourRow: { type: Object, default: null },
})

const interval = computed(() => (props.hourRow?.trips ? (60 / props.hourRow.trips).toFixed(1) : '-'))
const pct = computed(() => Math.min(100, (props.hourRow?.max_congestion || 0) * 100))
const n = (v) => (v == null ? '-' : Math.round(v).toLocaleString())

// 혼잡도 기준 간단 판정 (상세 수치는 배차 권고에서)
const need = computed(() => {
  const c = props.hourRow?.max_congestion
  if (c == null) return { label: '정보 없음', text: '이 시간대에 기점에서 출발하는 버스가 없습니다.' }
  if (c >= 0.8) return { high: true, label: '높음', text: `최대 혼잡도 ${fmtCongestion(c)}, 배차간격 단축(증차) 검토 대상입니다.` }
  if (c < 0.25) return { label: '낮음 (감차 검토)', text: `최대 혼잡도 ${fmtCongestion(c)}, 수요 대비 운행이 많습니다.` }
  return { label: '보통', text: `최대 혼잡도 ${fmtCongestion(c)}, 현행 배차로 수용 가능합니다.` }
})
</script>

<style scoped>
.head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; flex-wrap: wrap; }
.head .rno { font-size: 20px; }
.kv { display: grid; grid-template-columns: 1fr 1fr; gap: 0 18px; margin: 0; }
.kv > div { padding: 6px 0; border-bottom: 1px solid var(--line); display: flex; flex-direction: column; min-width: 0; }
dt { font-size: 11px; color: var(--ink3); }
dd { margin: 0; font-size: 19px; font-weight: 500; white-space: nowrap; }
dd small { font-size: 11px; font-weight: 400; color: var(--ink3); margin-left: 3px; }
.need { margin: 8px 0 4px; font-size: 12px; color: var(--ink2); }
.need b { color: var(--ink); font-weight: 600; }
.need b.hi { color: var(--accent); }
.base { margin: 0; }
</style>
