<template>
  <!-- 고른 시간대의 전 노선 운행횟수 권고 (F-13 최적화): 노선 × 시간 표, 버스가 모자란 곳 강조 -->
  <div>
    <p class="note">AI 권고는 보조 자료입니다. 모든 건 담당자 검토가 필요합니다.</p>

    <div class="ctl">
      <span class="lbl">시간대</span>
      <n-slider
        :value="range"
        range
        :min="0"
        :max="23"
        :step="1"
        :format-tooltip="(v) => `${v}시`"
        style="flex: 1; min-width: 80px"
        @update:value="onRange"
      />
      <b class="num rg">{{ range[0] }}~{{ range[1] }}시</b>
      <div class="segb" role="group" aria-label="시간대 빠른 선택">
        <button v-for="q in QUICK" :key="q.name" :class="{ on: isRange(q.range) }" :aria-pressed="isRange(q.range)" @click="onRange(q.range)">{{ q.name }}</button>
      </div>
      <n-spin v-if="loading" :size="14" />
    </div>

    <div v-if="error" class="notice warn" role="alert">{{ error }}</div>
    <p v-else-if="empty" class="empty-note">{{ empty }}</p>
    <p v-else-if="!result" class="empty-note">불러오는 중</p>

    <template v-else>
      <dl class="kpi">
        <div>
          <dt>붐비는 곳 (혼잡도 0.8 이상)</dt>
          <dd class="num">{{ result.kpi.overcrowded_before }} → <b>{{ result.kpi.overcrowded_after }}</b><small>곳</small></dd>
        </div>
        <div>
          <dt>늘리는 곳 / 줄이는 곳</dt>
          <dd class="num">{{ result.kpi.increased }} / {{ result.kpi.decreased }}<small>곳</small></dd>
        </div>
        <div>
          <dt>총 운행횟수</dt>
          <dd class="num">{{ result.kpi.total_trips_before }} → <b>{{ result.kpi.total_trips_after }}</b><small>회 ({{ signed(result.kpi.extra_trips) }})</small></dd>
        </div>
      </dl>

      <div class="scroll">
        <table class="plan">
          <caption class="sm">시간당 운행횟수 · 지금 → 권고</caption>
          <thead>
            <tr>
              <th scope="col">노선</th>
              <th v-for="h in hours" :key="h" scope="col" class="num" :class="{ cur: h === hour }">{{ h }}시</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in routes" :key="r">
              <th scope="row"><span class="rno" :class="[`r${r}`, { on: r === selectedRoute }]">{{ r }}</span></th>
              <td v-for="h in hours" :key="h">
                <button
                  v-if="cell(r, h)"
                  class="cell"
                  :class="[cell(r, h).action, { lim: cell(r, h).limited_by, sel: r === selectedRoute && h === hour }]"
                  :aria-label="cellLabel(cell(r, h))"
                  :title="cellLabel(cell(r, h))"
                  @click="emit('goto', { route: r, date, hour: h })"
                >
                  <span class="num">{{ cellText(cell(r, h)) }}</span>
                  <span class="mark" aria-hidden="true">{{ MARK[cell(r, h).action] }}{{ cell(r, h).limited_by ? '!' : '' }}</span>
                </button>
                <span v-else class="none" title="운행 데이터가 없는 시간대">–</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="legend sm">
        <span><b class="up">▲</b> 늘림</span><span><b>▼</b> 줄임</span><span><b class="warn">!</b> 붐비는데 더 못 늘림</span><span>칸을 누르면 그 노선·시간으로 이동</span>
      </div>

      <div v-if="picked" class="detail">
        <div class="dhead">
          <span class="rno on" :class="`r${picked.route}`">{{ picked.route }}</span>
          <b class="num">{{ picked.hour }}시</b>
          <span class="act" :class="{ up: picked.action === 'increase' }">{{ ACTION[picked.action] }}</span>
          <span v-if="picked.limited_by" class="limit">{{ picked.limited_by }}</span>
        </div>
        <dl class="kv">
          <div>
            <dt>시간당 운행</dt>
            <dd class="num">{{ picked.current_trips }} → {{ picked.recommended_trips }}<small>회</small></dd>
          </div>
          <div>
            <dt>배차간격</dt>
            <dd class="num">{{ (60 / picked.current_trips).toFixed(1) }} → {{ picked.interval.toFixed(1) }}<small>분</small></dd>
          </div>
          <div>
            <dt>최대 혼잡도 (예측 승객 기준)</dt>
            <dd class="num">{{ fmtCongestion(picked.max_congestion_before) }} → {{ fmtCongestion(picked.max_congestion_after) }}</dd>
          </div>
          <div>
            <dt>많이 몰릴 때 (10번 중 9번은 이보다 덜 붐빔)</dt>
            <dd class="num">
              {{ fmtCongestion(picked.upper_congestion_before) }} → {{ fmtCongestion(picked.upper_congestion_after) }}
              <small><i class="chip" :class="levelClass(picked.level_after)"></i> {{ picked.level_after }}</small>
            </dd>
          </div>
          <div>
            <dt>도로 위 버스 / 인가대수{{ picked.fleet_estimated ? ' (추정값)' : '' }}</dt>
            <dd class="num">{{ picked.buses_needed }} / {{ picked.fleet }}<small>대</small></dd>
            <span class="meter"><i :class="picked.buses_needed >= picked.fleet ? 'b3' : 'b0'" :style="{ width: fleetPct }"></i></span>
          </div>
        </dl>
      </div>
      <p v-else class="sm pick-hint">표에서 칸을 누르면 그 노선·시간의 상세 수치를 볼 수 있습니다.</p>

      <div v-if="result.cut_notice" class="notice warn cut">{{ result.cut_notice }}</div>

      <div v-if="result.binding?.length" class="bind">
        <div class="sm">붐비는데 더 늘리지 못한 곳 {{ result.binding.length }}곳</div>
        <ul>
          <li v-for="b in result.binding.slice(0, BIND_MAX)" :key="b">{{ b }}</li>
        </ul>
        <div v-if="result.binding.length > BIND_MAX" class="sm">외 {{ result.binding.length - BIND_MAX }}곳</div>
      </div>

      <p class="foot sm">{{ result.assumption }} · {{ result.solve_seconds }}초</p>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NSlider, NSpin } from 'naive-ui'
import { api, errorMessage, fmtCongestion, levelClass } from '../api'

const props = defineProps({
  date: { type: String, default: null },
  hour: { type: Number, default: 0 },
  selectedRoute: { type: String, default: null },
  routes: { type: Array, default: () => [] },
})
const emit = defineEmits(['goto'])

const QUICK = [
  { name: '오전', range: [6, 9] },
  { name: '오후', range: [16, 19] },
  { name: '종일', range: [4, 23] },
]
const ACTION = { increase: '증차', decrease: '감차', keep: '유지' }
const MARK = { increase: '▲', decrease: '▼', keep: '' }
const BIND_MAX = 6

const range = ref([6, 9])
const result = ref(null)
const loading = ref(false)
const error = ref('')
const empty = ref('')
let latest = 0
let timer = null

const isRange = (r) => range.value[0] === r[0] && range.value[1] === r[1]
const signed = (v) => `${v > 0 ? '+' : v < 0 ? '−' : '±'}${Math.abs(v ?? 0)}`
const hours = computed(() => {
  const [a, b] = range.value
  return Array.from({ length: b - a + 1 }, (_, i) => a + i)
})

const byKey = computed(() => new Map((result.value?.plan ?? []).map((i) => [`${i.route}|${i.hour}`, i])))
const cell = (route, h) => byKey.value.get(`${route}|${h}`)
const picked = computed(() => cell(props.selectedRoute, props.hour) ?? null)
const fleetPct = computed(() => (picked.value?.fleet ? `${Math.min(100, (picked.value.buses_needed / picked.value.fleet) * 100)}%` : '0%'))

const cellText = (c) => (c.change === 0 ? `${c.current_trips}` : `${c.current_trips}→${c.recommended_trips}`)
function cellLabel(c) {
  const head = `${c.route}번 ${c.hour}시: 시간당 ${c.current_trips}회`
  const body = c.change === 0 ? ' 유지' : ` → ${c.recommended_trips}회 (${ACTION[c.action]})`
  return head + body + (c.limited_by ? `, ${c.limited_by}` : '')
}

async function run() {
  if (!props.date) return
  const id = ++latest
  loading.value = true
  try {
    const data = await api.optimize({ date: props.date, hours: hours.value })
    if (id !== latest) return
    result.value = data
    error.value = ''
    empty.value = ''
  } catch (e) {
    if (id !== latest) return
    result.value = null
    // 422: 고른 시간대에 운행 데이터가 없음 (오류가 아니라 빈 상태)
    if (e?.response?.status === 422) empty.value = errorMessage(e)
    else error.value = errorMessage(e)
  } finally {
    if (id === latest) loading.value = false
  }
}

function onRange(value) {
  const [a, b] = [...value].sort((x, y) => x - y)
  range.value = [a, b]
  clearTimeout(timer)
  timer = setTimeout(run, 200)
}

watch(() => props.date, () => {
  result.value = null
  error.value = ''
  empty.value = ''
  run()
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.note { margin: 0 0 8px; padding-bottom: 7px; border-bottom: 1px solid var(--line); font-size: 11px; color: var(--ink2); }
.ctl { display: flex; align-items: center; gap: 8px 10px; flex-wrap: wrap; margin-bottom: 8px; }
.lbl { font-size: 11px; color: var(--ink3); white-space: nowrap; }
.rg { font-weight: 500; white-space: nowrap; min-width: 62px; }
.segb { display: inline-flex; border: 1px solid var(--line); border-radius: 2px; overflow: hidden; }
.segb button { padding: 3px 9px; font-size: 12px; border-right: 1px solid var(--line); min-height: 26px; white-space: nowrap; }
.segb button:last-child { border-right: 0; }
.segb button:hover { background: var(--panel2); }
.segb button.on { background: var(--ink); color: var(--panel); font-weight: 600; }

.kpi { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0 16px; margin: 0 0 8px; border-top: 1px solid var(--line); }
.kpi > div { padding: 6px 0; border-bottom: 1px solid var(--line); min-width: 0; }
dt { font-size: 11px; color: var(--ink3); }
dd { margin: 0; font-size: 17px; font-weight: 400; white-space: nowrap; }
dd b { font-weight: 600; }
dd small { font-size: 11px; font-weight: 400; color: var(--ink3); margin-left: 3px; }

.scroll { overflow-x: auto; }
.plan { border-collapse: collapse; width: 100%; }
.plan caption { text-align: left; padding-bottom: 3px; caption-side: top; }
.plan th, .plan td { border: 1px solid var(--line); padding: 0; text-align: center; }
.plan thead th { font-size: 11px; font-weight: 500; color: var(--ink2); padding: 3px 4px; background: var(--panel2); white-space: nowrap; }
.plan thead th.cur { color: var(--ink); font-weight: 700; box-shadow: inset 0 -2px 0 var(--accent); }
.plan tbody th { padding: 4px 6px; background: var(--panel2); }
/* 시간대가 많아 옆으로 넘길 때 노선 칸은 고정 */
.plan tbody th, .plan thead th:first-child { position: sticky; left: 0; z-index: 1; box-shadow: 1px 0 0 var(--line); }
.plan .rno { font-size: 13px; }
.cell { display: flex; align-items: center; justify-content: center; gap: 3px; width: 100%; min-width: 52px; min-height: 30px; padding: 3px 4px; font-size: 12px; white-space: nowrap; }
.cell:hover { background: var(--panel2); }
.cell.increase { background: color-mix(in srgb, var(--accent) 14%, transparent); font-weight: 600; }
.cell.increase .mark { color: var(--accent); }
.cell.decrease { background: var(--panel2); }
.cell.keep { color: var(--ink3); }
.cell.lim { box-shadow: inset 0 0 0 2px var(--c3); }
.cell.sel { outline: 2px solid var(--ink); outline-offset: -2px; }
.mark { font-size: 10px; color: var(--ink2); }
.cell.lim .mark { color: var(--c3); font-weight: 700; }
.none { color: var(--ink3); }
.legend { display: flex; flex-wrap: wrap; gap: 2px 12px; margin: 4px 0 8px; }
.legend b { color: var(--ink2); }
.legend b.up { color: var(--accent); }
.legend b.warn { color: var(--c3); }

.detail { border-top: 1px solid var(--line); padding-top: 6px; }
.dhead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dhead .rno { font-size: 15px; }
.dhead b { font-weight: 500; }
.limit { font-size: 11px; color: var(--c3); font-weight: 600; }
.kv { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 0 16px; margin: 4px 0 0; }
.kv > div { padding: 5px 0; border-bottom: 1px solid var(--line); min-width: 0; }
.kv dd { font-size: 15px; }
.kv dd small .chip { margin-right: 2px; }
.pick-hint { margin: 6px 0; }
.cut { margin-top: 8px; }
.bind { margin-top: 8px; }
.bind ul { margin: 2px 0 0; padding-left: 16px; font-size: 12px; color: var(--ink2); }
.foot { margin: 8px 0 0; }

@media (max-width: 820px) {
  .cell { min-height: 44px; }
  .segb button { min-height: 44px; min-width: 44px; }
}
</style>
