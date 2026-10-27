<template>
  <section class="block">
    <div class="block-title">예측 모델 성능 (검증 구간)</div>
    <div class="muted small">{{ metrics.valid_period?.join(' ~ ') }} · 시간 순서 분할</div>
    <div class="kpis">
      <div class="kpi">
        <div class="kpi-label">모델 MAE</div>
        <div class="kpi-value num">{{ fmt(m.model_mae) }}<span>명</span></div>
      </div>
      <div class="kpi">
        <div class="kpi-label">작년 같은 요일</div>
        <div class="kpi-value num ink2">{{ fmt(m.baseline_mae) }}<span>명</span></div>
      </div>
      <div class="kpi">
        <div class="kpi-label">개선율</div>
        <div class="kpi-value num good">{{ m.improvement_pct?.toFixed(1) }}<span>%</span></div>
      </div>
    </div>
    <div class="muted small">정류장·시간대 단위 · {{ metrics.horizon_days || 21 }}일 앞 예측 · 베이스라인 = 작년 같은 요일·시간</div>
    <div v-if="metrics.reference_last_week" class="muted small ref">
      참고: 전주 같은 요일 값으로 맞히면 {{ metrics.reference_last_week.stop_hour_mae.toFixed(2) }}명 — 데이터가 8일 늦게 공개돼 실제 운영에서는 쓸 수 없는 값
    </div>
    <table class="tbl">
      <thead><tr><th>노선</th><th>모델</th><th>작년 같은 요일</th><th>개선</th></tr></thead>
      <tbody>
        <tr v-for="(x, r) in metrics.by_route" :key="r">
          <td>{{ r }}</td>
          <td class="num">{{ Math.round(x.model_mae) }}</td>
          <td class="num">{{ Math.round(x.baseline_mae) }}</td>
          <td class="num">{{ x.improvement_pct.toFixed(0) }}%</td>
        </tr>
      </tbody>
    </table>
    <div class="muted small">노선 표: 노선 전체 × 시간대 합계 MAE (명/시간)</div>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ metrics: { type: Object, required: true } })
const m = computed(() => props.metrics.stop_hour || {})
const fmt = (v) => (v == null ? '-' : v.toFixed(2))
</script>

<style scoped>
.block { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 12px; }
.block-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; color: var(--ink-2); }
.small { font-size: 12px; }
.kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin: 10px 0 6px; }
.kpi-label { font-size: 11px; color: var(--muted); }
.kpi-value { font-size: 22px; font-weight: 700; }
.kpi-value span { font-size: 12px; font-weight: 500; margin-left: 2px; color: var(--muted); }
.good { color: #006300; }
.ref { margin-top: 4px; }
.tbl { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }
.tbl th { text-align: right; font-weight: 500; color: var(--muted); border-bottom: 1px solid var(--line); padding: 3px 4px; }
.tbl td { text-align: right; padding: 3px 4px; border-bottom: 1px solid #f0efe9; }
.tbl th:first-child, .tbl td:first-child { text-align: left; }
</style>
