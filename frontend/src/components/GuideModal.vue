<template>
  <!-- 챗봇 길안내 답변의 '지도로 보기'를 누르면 뜨는 팝업 -->
  <n-modal :show="show" preset="card" :title="title" style="width: min(880px, 94vw)" :bordered="false" @update:show="(v) => emit('update:show', v)">
    <div v-if="guide?.origin?.far" class="notice warn">
      현재 위치에서 가장 가까운 정류장({{ guide.origin.stop }})까지 약 {{ (guide.origin.walk_m / 1000).toFixed(1) }}km입니다. 그 정류장에서 타는 경로입니다.
    </div>

    <div class="mapbox">
      <MapView v-if="show" :geometries="geometries" :guide="view" />
    </div>

    <p v-if="!options.length" class="empty-note">두 정류장을 잇는 경로를 찾지 못했습니다 (직행·환승 한 번 기준).</p>

    <button v-for="(o, i) in options" :key="i" class="opt" :class="{ on: i === index }" :aria-pressed="i === index" @click="index = i">
      <span class="top">
        <b>{{ i + 1 }}안 {{ o.kind === 'direct' ? '직행' : '환승' }}</b>
        <span class="num">약 {{ o.total_min }}분</span>
        <span v-if="o.recommended" class="act up">추천</span>
        <span v-if="o.fastest" class="act">가장 빠름</span>
        <span v-if="o.least_crowded && !o.fastest" class="act">덜 붐빔</span>
      </span>
      <span v-for="(leg, k) in o.legs ?? []" :key="k" class="leg">
        <span class="rno" :class="`r${leg.route}`"><BusIcon :size="13" /> {{ leg.route }}</span>
        <span class="path">
          {{ leg.board?.name }} → {{ leg.alight?.name }}
          <span class="sm">
            <template v-if="leg.walk_m">걸어서 {{ leg.walk_m }}m · </template>정류장 {{ leg.stops }}개 · 약 {{ leg.ride_min }}분 · 배차간격 약 {{ leg.interval_min }}분
          </span>
        </span>
        <span class="lv"><i class="chip" :class="levelClass(leg.level)"></i>{{ leg.level || '-' }}</span>
      </span>
    </button>
    <div class="sm note">{{ guide?.assumption }}</div>
  </n-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { NModal } from 'naive-ui'
import { levelClass } from '../api'
import BusIcon from './BusIcon.vue'
import MapView from './MapView.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  // 길안내 결과 (/api/bus/guide 응답과 같은 형식) + user: 현재 위치 {lat, lng} | null
  guide: { type: Object, default: null },
  geometries: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:show'])

const index = ref(0)
const options = computed(() => props.guide?.options ?? [])
const title = computed(() => `${props.guide?.origin?.stop ?? ''} → ${props.guide?.destination?.stop ?? ''} · ${props.guide?.date ?? ''} ${props.guide?.hour ?? ''}시`)
const view = computed(() => (props.guide ? { ...props.guide, index: index.value } : null))

// 팝업을 열 때마다 추천 경로부터 보여 줌
watch(() => [props.show, props.guide], () => {
  if (props.show) index.value = props.guide?.recommendation?.index ?? 0
})
</script>

<style scoped>
.mapbox { height: 380px; display: flex; flex-direction: column; border: 1px solid var(--line); margin-bottom: 8px; }
.notice { margin-bottom: 8px; }
.opt { display: flex; flex-direction: column; gap: 5px; width: 100%; text-align: left; border: 1px solid var(--line); padding: 7px 8px; margin-bottom: 6px; }
.opt:hover { background: var(--panel2); }
.opt.on { border-color: var(--ink); background: var(--panel2); }
.top { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 13px; }
.leg { display: grid; grid-template-columns: auto 1fr auto; gap: 0 8px; align-items: center; font-size: 12.5px; }
.rno { display: inline-flex; align-items: center; gap: 3px; color: var(--rc, var(--ink)); }
.path { min-width: 0; overflow-wrap: anywhere; line-height: 1.45; }
.path .sm { display: block; }
.lv { display: flex; align-items: center; gap: 5px; font-size: 12px; white-space: nowrap; }
.note { line-height: 1.5; }
</style>
