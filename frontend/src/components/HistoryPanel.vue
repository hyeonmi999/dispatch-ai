<template>
  <div>
    <p v-if="!dbAvailable" class="empty-note">MariaDB 미연결이라 이력이 저장되지 않습니다.</p>
    <template v-if="saved">
      <div class="saved-head">
        <button class="back" @click="saved = null">목록으로</button>
        <span class="sm">{{ saved.created_at?.slice(0, 16) }} 생성 · 저장된 권고</span>
      </div>
      <BatchAdvice
        :batch="saved"
        :selected-route="route?.route"
        :db-available="dbAvailable"
        @select-route="(r) => emit('select-route', r)"
        @reviewed="load"
      />
    </template>
    <template v-else>
      <button v-for="h in history" :key="h.id" class="hist" @click="open(h)">
        <span class="top">
          <b class="num">{{ h.date }} {{ String(h.hour).padStart(2, '0') }}시</b>
          <span class="sm num">{{ h.created_at.slice(5, 16) }}</span>
        </span>
        <span class="sm">{{ summary(h) }} · 검토 {{ h.count - (h.reviews.pending || 0) }}/{{ h.count }}</span>
      </button>
      <p v-if="dbAvailable && !history.length" class="empty-note">'전체 노선 권고'를 만들면 시간대별로 여기에 쌓입니다.</p>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { api, errorMessage } from '../api'
import BatchAdvice from './BatchAdvice.vue'

const props = defineProps({
  route: { type: Object, default: null },
  dbAvailable: { type: Boolean, default: false },
  refresh: { type: Number, default: 0 },
})
const emit = defineEmits(['select-route', 'goto'])
const message = useMessage()
const history = ref([])
const saved = ref(null)

const ACTIONS = { increase: '증차', increase_limited: '별도 대책', decrease: '감차', keep: '유지' }
const summary = (h) => Object.keys(ACTIONS).filter((k) => h.summary[k]).map((k) => `${ACTIONS[k]} ${h.summary[k]}`).join(' · ')

async function load() {
  if (!props.dbAvailable) return
  try {
    history.value = (await api.advisoryBatches('bus')).items
  } catch {
    history.value = []
  }
}

// 이력 항목(시간대 묶음) 클릭: 그 시간대의 노선별 저장된 권고를 열고, 날짜·시간도 그 시점으로 이동
async function open(h) {
  try {
    saved.value = await api.advisoryBatch(h.id)
    emit('goto', { route: saved.value.items[0]?.route || props.route?.route, date: h.date, hour: h.hour })
  } catch (e) {
    message.error(errorMessage(e))
  }
}

onMounted(load)
watch(() => [props.refresh, props.dbAvailable], load)
</script>

<style scoped>
.hist { display: flex; flex-direction: column; gap: 2px; width: 100%; text-align: left; padding: 7px 4px; border-bottom: 1px solid var(--line); }
.hist:last-child { border-bottom: 0; }
.hist:hover { background: var(--panel2); }
.top { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.top b { font-weight: 500; }
.saved-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.back { font-size: 12px; text-decoration: underline; text-underline-offset: 2px; color: var(--ink2); }
</style>
