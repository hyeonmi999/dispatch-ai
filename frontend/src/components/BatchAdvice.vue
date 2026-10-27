<template>
  <!-- 한 시간대의 전체 노선 권고 목록 (배차 권고 탭·권고 이력 탭 공용) -->
  <div>
    <div class="batch-head">
      <b>{{ batch.date }} {{ batch.hour }}시</b>
      <n-tag v-if="batch.summary.increase" size="small" type="error" round>증차 {{ batch.summary.increase }}</n-tag>
      <n-tag v-if="batch.summary.increase_limited" size="small" type="warning" round>별도 대책 {{ batch.summary.increase_limited }}</n-tag>
      <n-tag v-if="batch.summary.decrease" size="small" type="info" round>감차 검토 {{ batch.summary.decrease }}</n-tag>
      <n-tag v-if="batch.summary.keep" size="small" type="success" round>유지 {{ batch.summary.keep }}</n-tag>
    </div>
    <div class="muted tiny">노선을 누르면 지도에서 선택되고 상세 근거가 열립니다 · 모두 담당자 검토 필요</div>
    <div v-for="a in batch.items" :key="a.route" class="brow-wrap">
      <div
        class="brow"
        :class="{ active: a.route === selectedRoute, off: a.status !== 'ok' }"
        @click="a.status === 'ok' && emit('select-route', a.route)"
      >
        <span class="dot" :style="{ background: levelColor(a.congestion?.level || '정보없음') }"></span>
        <b class="rno">{{ a.route }}</b>
        <template v-if="a.status === 'ok'">
          <span class="num small">{{ fmtCongestion(a.congestion.ratio) }}</span>
          <span class="num small ink2">{{ a.current.interval_min }}→<b>{{ a.recommendation.recommended_interval_min }}</b>분</span>
          <span v-if="a.recommendation.extra_buses" class="num small ink2">
            {{ a.recommendation.extra_buses > 0 ? '+' : '' }}{{ a.recommendation.extra_buses }}대
          </span>
          <n-tag v-if="a.review_status && a.review_status !== 'pending'" size="tiny" :type="REVIEW[a.review_status]?.[1]">
            {{ REVIEW[a.review_status]?.[0] }}
          </n-tag>
          <n-tag size="small" :type="ACTION_TYPES[a.recommendation.action]" class="act">{{ ACTIONS[a.recommendation.action] }}</n-tag>
        </template>
        <span v-else class="muted small">운행 없음</span>
      </div>
      <AdviceCard
        v-if="a.status === 'ok' && a.route === selectedRoute"
        :key="a.advisory_id ?? a.route"
        :advice="a"
        :db-available="dbAvailable"
        class="inline-card"
        @reviewed="(s) => onReviewed(a, s)"
      />
    </div>
  </div>
</template>

<script setup>
import { NTag } from 'naive-ui'
import { ACTION_TYPES, fmtCongestion, levelColor } from '../api'
import AdviceCard from './AdviceCard.vue'

defineProps({
  batch: { type: Object, required: true },
  selectedRoute: { type: String, default: null },
  dbAvailable: { type: Boolean, default: false },
})
const emit = defineEmits(['select-route', 'reviewed'])

const ACTIONS = { increase: '증차', increase_limited: '별도 대책', decrease: '감차 검토', keep: '현행 유지' }
const REVIEW = { approved: ['승인', 'success'], held: ['보류', 'default'], rejected: ['반려', 'error'] }

function onReviewed(advice, status) {
  advice.review_status = status
  emit('reviewed')
}
</script>

<style scoped>
.small { font-size: 12px; }
.tiny { font-size: 11px; margin-bottom: 4px; }
.batch-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 4px 0; }
.brow { display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 8px; border: 1px solid transparent; cursor: pointer; }
.brow:hover { background: #f4f3ef; }
.brow.active { border-color: var(--accent); background: #eef4fc; }
.brow.off { cursor: default; opacity: 0.6; }
.brow .rno { width: 34px; }
.brow .act { margin-left: auto; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.inline-card { margin: 6px 0 10px; }
</style>
