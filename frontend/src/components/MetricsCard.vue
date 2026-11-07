<template>
  <div>
    <div class="big3">
      <div><span>모델 MAE</span><b class="num">{{ fmt(m.model_mae) }}명</b></div>
      <div><span>작년 같은 요일</span><b class="num">{{ fmt(m.baseline_mae) }}명</b></div>
      <div><span>개선율</span><b class="num">{{ m.improvement_pct?.toFixed(1) }}%</b></div>
    </div>
    <table>
      <thead><tr><th scope="col">노선·시간 MAE</th><th scope="col">모델</th><th scope="col">작년</th><th scope="col">개선</th></tr></thead>
      <tbody>
        <tr v-for="(x, r) in metrics.by_route" :key="r">
          <td class="num">{{ r }}</td>
          <td class="num">{{ x.model_mae.toFixed(1) }}</td>
          <td class="num">{{ x.baseline_mae.toFixed(1) }}</td>
          <td class="num">{{ x.improvement_pct.toFixed(1) }}%</td>
        </tr>
      </tbody>
    </table>
    <p class="foot">
      검증 {{ metrics.valid_period?.join('~') }}, 정류장·시간 단위(명), {{ metrics.horizon_days || 21 }}일 앞 예측.
      실제 운행횟수를 입력한 값입니다. 운행횟수를 과거 값으로 넣으면 10/25까지 데이터로 한 시뮬레이션 기준 4.13명, 노선·시간 97명입니다.
    </p>
    <p v-if="metrics.reference_last_week" class="foot">
      참고: 전주 같은 요일 값으로 맞히면 {{ metrics.reference_last_week.stop_hour_mae.toFixed(2) }}명. 데이터가 8일 늦게 공개돼 실제 운영에서는 쓸 수 없는 값입니다.
    </p>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ metrics: { type: Object, required: true } })
const m = computed(() => props.metrics.stop_hour || {})
const fmt = (v) => (v == null ? '-' : v.toFixed(2))
</script>

<style scoped>
.big3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: 8px; border: 1px solid var(--line); }
.big3 div { padding: 6px 8px; border-right: 1px solid var(--line); min-width: 0; }
.big3 div:last-child { border-right: 0; }
.big3 span { display: block; font-size: 11px; color: var(--ink3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.big3 b { font-size: 18px; font-weight: 500; white-space: nowrap; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { font-weight: 500; color: var(--ink3); font-size: 11px; text-align: right; padding: 3px 4px; border-bottom: 1px solid var(--line); }
td { padding: 4px; text-align: right; border-bottom: 1px solid var(--line); }
th:first-child, td:first-child { text-align: left; }
.foot { font-size: 11px; color: var(--ink2); margin: 8px 0 0; }
</style>
