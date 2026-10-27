<template>
  <div class="card">
    <div class="top">
      <n-tag :type="ACTION_TYPES[a.recommendation.action]" size="medium" strong>{{ a.recommendation.label }}</n-tag>
      <n-tag type="warning" size="small" round>⚠ 담당자 검토 필요 (needs_review)</n-tag>
    </div>

    <div class="flow">
      <div class="cell">
        <div class="lbl">배차간격</div>
        <div class="big num">{{ a.current.interval_min }}<small>분</small> → <b>{{ a.recommendation.recommended_interval_min }}</b><small>분</small></div>
      </div>
      <div class="cell">
        <div class="lbl">운행횟수</div>
        <div class="big num">{{ a.current.trips_per_hour }} → <b>{{ a.recommendation.recommended_trips_per_hour }}</b><small>회/h</small></div>
      </div>
      <div class="cell">
        <div class="lbl">혼잡도</div>
        <div class="big num">
          {{ a.congestion.ratio.toFixed(2) }} → <b>{{ a.recommendation.expected_congestion.toFixed(2) }}</b>
        </div>
        <div class="tiny">{{ a.congestion.level }} → {{ levelOf(a.recommendation.expected_congestion) }}</div>
      </div>
      <div class="cell">
        <div class="lbl">추가 차량</div>
        <div class="big num"><b>{{ a.recommendation.extra_buses > 0 ? '+' : '' }}{{ a.recommendation.extra_buses }}</b><small>대</small></div>
        <div class="tiny">권고 간격 유지 시</div>
      </div>
    </div>

    <div class="sec-title">판단 근거 (예측·혼잡도)</div>
    <ul class="rationale">
      <li v-for="(r, i) in a.rationale" :key="i">{{ r }}</li>
    </ul>

    <div class="sec-title">규정·정책 근거 <span class="muted">({{ a.evidence.embedding }}{{ a.evidence.llm ? ' · ' + a.evidence.llm : '' }})</span></div>
    <n-alert v-if="!a.evidence.found" type="warning" :show-icon="false">
      {{ a.evidence.answer || '근거 문서를 찾을 수 없음' }} <span class="muted">{{ a.evidence.note }}</span>
    </n-alert>
    <template v-else>
      <div v-if="a.evidence.answer" class="answer">{{ a.evidence.answer }}</div>
      <SourceList :sources="a.evidence.sources" />
    </template>

    <div class="foot">
      <div class="conf">
        <span class="lbl">신뢰도</span>
        <n-progress type="line" :percentage="Math.round(a.confidence * 100)" :height="6" :show-indicator="false" color="#2a78d6" style="width: 90px" />
        <span class="num small">{{ Math.round(a.confidence * 100) }}%</span>
      </div>
      <div class="review">
        <template v-if="reviewed">
          <n-tag :type="reviewed === 'approved' ? 'success' : reviewed === 'rejected' ? 'error' : 'default'">{{ STATUS[reviewed] }} 완료</n-tag>
        </template>
        <template v-else>
          <n-button size="small" type="primary" :disabled="!canReview" @click="review('approved')">승인</n-button>
          <n-button size="small" :disabled="!canReview" @click="review('held')">보류</n-button>
          <n-button size="small" type="error" ghost :disabled="!canReview" @click="review('rejected')">반려</n-button>
        </template>
      </div>
    </div>
    <div class="muted tiny right">
      권고 #{{ a.advisory_id ?? '-' }} · {{ a.db_saved ? 'MariaDB 저장됨' : 'DB 미저장' }} · {{ a.model_version }}
      {{ a.is_forecast ? '· 향후 예측' : '' }}
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { NAlert, NButton, NProgress, NTag, useMessage } from 'naive-ui'
import { ACTION_TYPES, api, errorMessage } from '../api'
import SourceList from './SourceList.vue'

const props = defineProps({
  advice: { type: Object, required: true },
  dbAvailable: { type: Boolean, default: false },
})
const emit = defineEmits(['reviewed'])
const message = useMessage()
const a = computed(() => props.advice)
const reviewed = ref(null)
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
    emit('reviewed')
  } catch (e) {
    message.error(errorMessage(e))
  }
}
</script>

<style scoped>
.card { border: 1px solid #e6e4dc; border-radius: 10px; padding: 12px; }
.top { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.flow { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px 0; }
.cell { background: #f7f6f2; border-radius: 8px; padding: 8px 10px; }
.lbl { font-size: 11px; color: var(--muted); }
.big { font-size: 17px; }
.big b { font-size: 20px; }
.big small { font-size: 11px; color: var(--muted); margin-left: 1px; }
.tiny { font-size: 11px; color: var(--muted); }
.small { font-size: 12px; }
.sec-title { font-size: 12px; font-weight: 700; color: var(--ink-2); margin: 10px 0 6px; }
.rationale { margin: 0; padding-left: 18px; font-size: 12.5px; line-height: 1.6; }
.answer { background: #eef4fc; border-radius: 8px; padding: 10px; font-size: 13px; line-height: 1.6; white-space: pre-wrap; margin-bottom: 8px; }
.foot { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; gap: 8px; flex-wrap: wrap; }
.conf { display: flex; align-items: center; gap: 6px; }
.review { display: flex; gap: 6px; }
.right { text-align: right; margin-top: 6px; }
</style>
