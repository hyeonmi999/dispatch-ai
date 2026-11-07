<template>
  <!-- 한 시간대의 전체 노선 권고 목록 (배차 권고·권고 이력 패널 공용) -->
  <div>
    <div class="batch-head">
      <b class="num">{{ batch.date }} {{ batch.hour }}시</b>
      <span class="sm">{{ summaryText }}</span>
    </div>
    <div v-for="a in batch.items" :key="a.route" class="adv">
      <button class="brow" :class="{ active: a.route === selectedRoute }" :disabled="a.status !== 'ok'" @click="emit('select-route', a.route)">
        <span class="rno" :class="`r${a.route}`">{{ a.route }}</span>
        <template v-if="a.status === 'ok'">
          <span class="act" :class="{ up: a.recommendation.action === 'increase' || a.recommendation.action === 'increase_limited' }">{{ a.recommendation.label }}</span>
          <span v-if="a.review_status && a.review_status !== 'pending'" class="rev">{{ REVIEW[a.review_status] }}</span>
          <span class="line">
            <i class="chip" :class="levelClass(a.congestion.level)"></i>
            <span class="num">{{ fmtCongestion(a.congestion.ratio) }} {{ a.congestion.level }}</span>
            <span class="num">{{ a.current.interval_min }}→{{ a.recommendation.recommended_interval_min }}분</span>
            <span v-if="a.recommendation.extra_buses" class="num">{{ a.recommendation.extra_buses > 0 ? '+' : '' }}{{ a.recommendation.extra_buses }}대</span>
          </span>
        </template>
        <span v-else class="sm">운행 없음</span>
      </button>
      <AdviceCard
        v-if="a.status === 'ok' && a.route === selectedRoute"
        :key="a.advisory_id ?? a.route"
        :advice="a"
        :db-available="dbAvailable"
        @reviewed="(s) => onReviewed(a, s)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtCongestion, levelClass } from '../api'
import AdviceCard from './AdviceCard.vue'

const props = defineProps({
  batch: { type: Object, required: true },
  selectedRoute: { type: String, default: null },
  dbAvailable: { type: Boolean, default: false },
})
const emit = defineEmits(['select-route', 'reviewed'])

const ACTIONS = { increase: '증차', increase_limited: '별도 대책', decrease: '감차 검토', keep: '유지' }
const REVIEW = { approved: '승인됨', held: '보류됨', rejected: '반려됨' }
const summaryText = computed(() =>
  Object.keys(ACTIONS).filter((k) => props.batch.summary[k]).map((k) => `${ACTIONS[k]} ${props.batch.summary[k]}`).join(' · '),
)

function onReviewed(advice, status) {
  advice.review_status = status
  emit('reviewed')
}
</script>

<style scoped>
.batch-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 2px; }
.batch-head b { font-weight: 500; }
.adv { border-bottom: 1px solid var(--line); }
.adv:last-child { border-bottom: 0; }
.brow { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; width: 100%; text-align: left; padding: 8px 2px; }
.brow:hover:not(:disabled) { background: var(--panel2); }
.brow:disabled { cursor: default; opacity: 0.6; }
.brow .rno { font-size: 14px; }
.brow.active .rno { background: var(--rc); color: var(--on); }
.rev { font-size: 11px; color: var(--ink2); }
.line { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 8px; flex-basis: 100%; font-size: 12px; color: var(--ink2); }
</style>
