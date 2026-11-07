<template>
  <div class="card">
    <dl class="flow">
      <div>
        <dt>배차간격</dt>
        <dd class="num">{{ a.current.interval_min }} → <b>{{ a.recommendation.recommended_interval_min }}</b><small>분</small></dd>
      </div>
      <div>
        <dt>운행횟수</dt>
        <dd class="num">{{ a.current.trips_per_hour }} → <b>{{ a.recommendation.recommended_trips_per_hour }}</b><small>회/h</small></dd>
      </div>
      <div>
        <dt>혼잡도 · {{ a.congestion.level }} → {{ levelOf(a.recommendation.expected_congestion) }}</dt>
        <dd class="num">{{ fmtCongestion(a.congestion.ratio) }} → <b>{{ fmtCongestion(a.recommendation.expected_congestion) }}</b></dd>
      </div>
      <div>
        <dt>추가 차량 (권고 간격 유지 시)</dt>
        <dd class="num"><b>{{ a.recommendation.extra_buses > 0 ? '+' : '' }}{{ a.recommendation.extra_buses }}</b><small>대</small></dd>
      </div>
    </dl>

    <div class="sec">판단 근거</div>
    <ul class="rationale">
      <li v-for="(r, i) in a.rationale" :key="i">{{ r }}</li>
    </ul>

    <div class="sec">규정·정책 근거 <span class="sm">{{ a.evidence.embedding }}{{ a.evidence.llm ? ' · ' + a.evidence.llm : '' }}</span></div>
    <div v-if="!a.evidence.found" class="notice warn">
      {{ a.evidence.answer || '근거 문서를 찾을 수 없음' }} <span class="sm">{{ a.evidence.note }}</span>
    </div>
    <template v-else>
      <div v-if="a.evidence.answer" class="answer">{{ a.evidence.answer }}</div>
      <SourceList :sources="a.evidence.sources" />
    </template>

    <div class="foot">
      <div class="conf">
        <span class="sm">신뢰도</span>
        <span class="meter conf-bar"><i :style="{ width: `${Math.round(a.confidence * 100)}%`, background: 'var(--ink2)' }"></i></span>
        <span class="num sm">{{ Math.round(a.confidence * 100) }}%</span>
      </div>
      <span v-if="reviewed" class="done">{{ STATUS[reviewed] }} 완료</span>
      <div v-else class="rv" role="group" :aria-label="`${a.route}번 권고 검토`">
        <button v-for="(label, key) in STATUS" :key="key" :disabled="!canReview" @click="review(key)">{{ label }}</button>
      </div>
    </div>
    <div class="sm meta">
      권고 #{{ a.advisory_id ?? '-' }} · {{ a.db_saved ? 'MariaDB 저장됨' : 'DB 미저장' }} · {{ a.model_version }}{{ a.is_forecast ? ' · 향후 예측' : '' }}
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { api, errorMessage, fmtCongestion } from '../api'
import SourceList from './SourceList.vue'

const props = defineProps({
  advice: { type: Object, required: true },
  dbAvailable: { type: Boolean, default: false },
})
const emit = defineEmits(['reviewed'])
const message = useMessage()
const a = computed(() => props.advice)
// 저장된 권고를 다시 열면 기존 검토 결과를 그대로 표시
const reviewed = ref(props.advice.review_status && props.advice.review_status !== 'pending' ? props.advice.review_status : null)
const canReview = computed(() => props.dbAvailable && a.value.advisory_id != null)
const STATUS = { approved: '승인', held: '보류', rejected: '반려' }

function levelOf(c) {
  if (c < 0.45) return '여유'
  if (c < 0.8) return '보통'
  if (c < 1) return '혼잡'
  return '매우혼잡'
}

async function review(status) {
  try {
    await api.review(a.value.advisory_id, { status, reviewer: '배차 담당자', note: '' })
    reviewed.value = status
    message.success(`권고 #${a.value.advisory_id} ${STATUS[status]} 처리됨`)
    emit('reviewed', status)
  } catch (e) {
    message.error(errorMessage(e))
  }
}
</script>

<style scoped>
.card { padding: 2px 2px 10px; }
.flow { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; margin: 0; }
.flow > div { padding: 5px 0; border-bottom: 1px solid var(--line); min-width: 0; }
dt { font-size: 11px; color: var(--ink3); }
dd { margin: 0; font-size: 15px; white-space: nowrap; }
dd b { font-size: 17px; font-weight: 600; }
dd small { font-size: 11px; color: var(--ink3); margin-left: 2px; }
.sec { font-size: 12px; font-weight: 600; color: var(--ink2); margin: 10px 0 4px; }
.rationale { margin: 0; padding-left: 15px; font-size: 12px; line-height: 1.55; color: var(--ink2); }
.answer { border: 1px solid var(--line); padding: 8px 10px; font-size: 13px; line-height: 1.6; white-space: pre-wrap; margin-bottom: 6px; }
.foot { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; gap: 8px; flex-wrap: wrap; }
.conf { display: flex; align-items: center; gap: 6px; }
.conf-bar { width: 70px; margin: 0; }
.done { font-size: 12px; font-weight: 600; }
.rv { display: inline-flex; border: 1px solid var(--line); border-radius: 2px; overflow: hidden; }
.rv button { font-size: 12px; padding: 3px 10px; border-right: 1px solid var(--line); min-height: 28px; }
.rv button:last-child { border-right: 0; }
.rv button:hover:not(:disabled) { background: var(--ink); color: var(--panel); }
.rv button:disabled { opacity: 0.45; cursor: not-allowed; }
.meta { text-align: right; margin-top: 6px; overflow-wrap: anywhere; }
</style>
