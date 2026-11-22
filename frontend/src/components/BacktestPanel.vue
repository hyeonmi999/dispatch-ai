<template>
  <!-- 배차 방식 성능 비교 (F-14): 지난 기록으로 현행·규칙·그리디·최적화를 실제 승객 수로 채점한 결과 -->
  <div>
    <div v-if="error" class="notice warn" role="alert">{{ error }}</div>
    <p v-else-if="empty" class="empty-note">{{ empty }}</p>
    <p v-else-if="!data" class="empty-note">불러오는 중</p>

    <template v-else>
      <p class="sm head">
        평가 {{ data.eval_window.start }} ~ {{ data.eval_window.end }} ({{ data.eval_window.days }}일) ·
        붐빔 = 실제 승객 기준 혼잡도 {{ data.crowded_threshold }} 이상
      </p>
      <div class="scroll">
        <table>
          <caption class="sm">방식별 결과 · 붐비는 비율이 낮고 운행·변경이 적을수록 좋음</caption>
          <thead>
            <tr>
              <th scope="col">방식</th>
              <th scope="col">붐비는 노선·시간</th>
              <th scope="col" class="r">총 운행</th>
              <th scope="col" class="r">바뀐 곳</th>
              <th scope="col" class="r">새 인가대수 초과</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in data.methods" :key="m.name" :class="{ ref: m.reference, best: m.name === best }">
              <th scope="row">
                {{ m.name }}<small v-if="m.reference"> 참고</small>
                <small v-if="m.weights" class="w num">{{ weightText(m.weights) }}</small>
              </th>
              <td>
                <span class="bar" aria-hidden="true"><i :style="{ width: barPct(m.overcrowded_pct) }"></i></span>
                <b class="num">{{ m.overcrowded_pct.toFixed(2) }}%</b>
                <small class="num">{{ m.overcrowded_cells.toLocaleString() }}/{{ m.cells.toLocaleString() }}</small>
              </td>
              <td class="r num">{{ signedPct(m.trips_vs_current_pct) }}</td>
              <td class="r num">{{ m.changed_cells.toLocaleString() }}</td>
              <td class="r num" :class="{ warn: m.fleet_over_new_days > 0 }">
                {{ m.fleet_over_new_days }}<small>노선·일</small>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sm legend">가중치는 과밀 · 운행 · 변경 순. 총 운행은 현행 대비 증감입니다.</p>

      <div class="notice caution">{{ data.caution }}</div>

      <details class="more">
        <summary class="sm">채점 방법</summary>
        <ul>
          <li v-for="n in data.notes" :key="n">{{ n }}</li>
          <li v-if="data.pick_order?.length">최적화 가중치는 튜닝 구간({{ data.tune_window.start }} ~ {{ data.tune_window.end }})에서 {{ data.pick_order.join(' → ') }} 순으로 골랐습니다.</li>
          <li v-for="m in data.methods.filter((x) => x.why)" :key="`why-${m.name}`">{{ m.label }}: {{ m.why }}</li>
        </ul>
      </details>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, errorMessage } from '../api'

const data = ref(null)
const error = ref('')
const empty = ref('')

const weightText = (w) => [w?.overload, w?.trips, w?.change].map((v) => v ?? '-').join('·')
const signedPct = (v) => `${v > 0 ? '+' : v < 0 ? '−' : '±'}${Math.abs(v ?? 0).toFixed(1)}%`
const maxPct = computed(() => Math.max(1, ...(data.value?.methods ?? []).map((m) => m.overcrowded_pct)))
const barPct = (v) => `${Math.min(100, (v / maxPct.value) * 100)}%`
// 참고 행을 뺀 방식 중 새 인가대수 초과가 없고 붐비는 비율이 가장 낮은 것
const best = computed(() => {
  const ok = (data.value?.methods ?? []).filter((m) => !m.reference && m.fleet_over_new_days === 0)
  return ok.length ? ok.reduce((a, b) => (b.overcrowded_pct < a.overcrowded_pct ? b : a)).name : null
})

onMounted(async () => {
  try {
    data.value = await api.backtest()
  } catch (e) {
    // 404: 결과 파일이 아직 없음 (오류가 아니라 빈 상태)
    if (e?.response?.status === 404) empty.value = errorMessage(e)
    else error.value = errorMessage(e)
  }
})
</script>

<style scoped>
.head { margin: 0 0 6px; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
caption { text-align: left; caption-side: top; padding-bottom: 3px; }
th, td { padding: 5px 6px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
thead th { font-size: 11px; font-weight: 500; color: var(--ink3); background: var(--panel2); }
tbody th { font-weight: 500; }
.r { text-align: right; }
small { font-size: 10px; color: var(--ink3); margin-left: 3px; }
small.w { display: block; margin-left: 0; }
td b { font-weight: 600; margin-left: 4px; }
.bar { display: inline-block; vertical-align: middle; width: 60px; height: 6px; background: var(--panel2); }
.bar i { display: block; height: 100%; background: var(--ink2); }
tr.best .bar i { background: var(--accent); }
tr.best th { box-shadow: inset 2px 0 0 var(--accent); }
tr.ref th, tr.ref td { color: var(--ink2); font-style: italic; }
td.warn { color: var(--c3); font-weight: 600; }
.legend { margin: 4px 0 8px; }
.caution { font-size: 11px; }
.more { margin-top: 8px; }
.more summary { cursor: pointer; min-height: 26px; display: flex; align-items: center; }
.more ul { margin: 4px 0 0; padding-left: 16px; font-size: 11px; color: var(--ink2); }
@media (max-width: 820px) {
  .more summary { min-height: 44px; }
}
</style>
