<template>
  <div>
    <p class="note">AI 권고는 보조 자료입니다. 모든 건 담당자 검토가 필요합니다.</p>
    <div class="ctl">
      <n-switch v-model:value="useLlm" size="small" />
      <span class="sm">규정 요약 (LLM, 유형당 10~30초)</span>
      <n-button type="primary" size="small" :loading="loading" class="go" @click="run">{{ hour }}시 전체 노선 권고</n-button>
    </div>

    <BatchAdvice
      v-if="batch"
      :batch="batch"
      :selected-route="route?.route"
      :db-available="dbAvailable"
      @select-route="(r) => emit('select-route', r)"
      @reviewed="emit('changed')"
    />
    <p v-else-if="!loading" class="empty-note">'{{ hour }}시 전체 노선 권고'를 누르면 노선마다 증차·유지·감차 권고가 만들어집니다.</p>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { NButton, NSwitch, useMessage } from 'naive-ui'
import { api, errorMessage } from '../api'
import BatchAdvice from './BatchAdvice.vue'

const props = defineProps({
  route: { type: Object, default: null },
  date: { type: String, default: null },
  hour: { type: Number, default: 0 },
  llm: { type: String, default: 'qwen' },
  embedding: { type: String, default: 'bge' },
  dbAvailable: { type: Boolean, default: false },
})
const emit = defineEmits(['select-route', 'changed'])
const message = useMessage()
const useLlm = ref(true)
const batch = ref(null)
const loading = ref(false)

const contextKey = () => `${props.date}|${props.hour}`

// 선택한 날짜·시간대의 전체 노선 권고 (증차 필요 → 별도 대책 → 감차 검토 → 유지 순)
async function run() {
  loading.value = true
  const key = contextKey()
  try {
    const result = await api.adviseAll({
      date: props.date, hour: props.hour, llm: props.llm, embedding: props.embedding, use_llm: useLlm.value,
    })
    // LLM 응답을 기다리는 사이 날짜·시간을 바꿨다면 지난 결과는 표시하지 않음
    if (key === contextKey()) {
      batch.value = result
      const top = result.items.find((a) => a.status === 'ok')
      if (top && !result.items.some((a) => a.route === props.route?.route && a.status === 'ok')) emit('select-route', top.route)
    }
    emit('changed')
  } catch (e) {
    message.error(errorMessage(e))
  } finally {
    loading.value = false
  }
}

watch(() => [props.date, props.hour], () => (batch.value = null))
</script>

<style scoped>
.note { margin: 0 0 8px; padding-bottom: 7px; border-bottom: 1px solid var(--line); font-size: 11px; color: var(--ink2); }
.ctl { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.go { margin-left: auto; }
</style>
