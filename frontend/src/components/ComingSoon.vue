<template>
  <div class="wrap">
    <div class="card">
      <div class="icon">{{ domain?.name === 'logistics' ? '🚚' : '🛠️' }}</div>
      <h2>{{ domain?.title }} <n-tag type="warning" round>준비 중</n-tag></h2>
      <p class="ink2">{{ domain?.description }}</p>
      <p class="muted small">
        공통 계층(수요 예측 · 규정 RAG · 배차 권고)과 API 경로는 이미 연결되어 있고,
        도메인 어댑터의 인터페이스·입력 스키마만 정의된 상태입니다. (2차 스프린트)
      </p>

      <div v-if="schema" class="schema">
        <div class="sec">예측 입력 스키마 (/api/{{ domain.name }}/predict)</div>
        <div v-for="(f, k) in schema.schemas.predict?.properties" :key="k" class="field">
          <code>{{ k }}</code> <span class="muted">{{ f.description || f.type || f.format }}</span>
        </div>
      </div>

      <n-button @click="tryCall" :loading="loading">API 호출해 보기</n-button>
      <n-alert v-if="response" :type="response.status === 501 ? 'info' : 'error'" style="margin-top: 12px" :title="`HTTP ${response.status}`">
        {{ response.body.detail }}
      </n-alert>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import axios from 'axios'
import { NAlert, NButton, NTag } from 'naive-ui'
import { api } from '../api'

const props = defineProps({ domain: { type: Object, default: null } })
const schema = ref(null)
const response = ref(null)
const loading = ref(false)

async function tryCall() {
  loading.value = true
  try {
    const r = await axios.post(`/api/${props.domain.name}/predict`, {}, { validateStatus: () => true })
    response.value = { status: r.status, body: r.data }
  } finally {
    loading.value = false
  }
}

watch(
  () => props.domain?.name,
  async (name) => {
    response.value = null
    schema.value = name ? await api.schema(name).catch(() => null) : null
  },
  { immediate: true },
)
</script>

<style scoped>
.wrap { flex: 1; display: grid; place-items: center; padding: 24px; }
.card { max-width: 560px; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 28px; }
.icon { font-size: 40px; }
h2 { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
.small { font-size: 12px; }
.schema { background: #f7f6f2; border-radius: 8px; padding: 10px 12px; margin: 14px 0; }
.sec { font-size: 12px; font-weight: 700; margin-bottom: 6px; color: var(--ink-2); }
.field { font-size: 13px; line-height: 1.8; }
code { background: #fff; padding: 1px 6px; border-radius: 4px; border: 1px solid var(--line); }
</style>
