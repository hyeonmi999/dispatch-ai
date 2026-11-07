<template>
  <div class="wrap">
    <div class="card">
      <h2>{{ domain?.title }} <span class="flag">준비 중</span></h2>
      <p class="ink2">{{ domain?.description }}</p>
      <p class="ink2 small">
        공통 계층(수요 예측 · 규정 RAG · 배차 권고)과 API 경로는 연결되어 있고,
        도메인 어댑터의 인터페이스·입력 스키마만 정의된 상태입니다. 2차 스프린트 대상입니다.
      </p>

      <div v-if="schema" class="schema">
        <div class="sec">예측 입력 스키마 <code class="num">/api/{{ domain.name }}/predict</code></div>
        <div v-for="(f, k) in schema.schemas.predict?.properties" :key="k" class="field">
          <code>{{ k }}</code> <span class="muted">{{ f.description || f.type || f.format }}</span>
        </div>
      </div>

      <n-button size="small" :loading="loading" @click="tryCall">API 호출해 보기</n-button>
      <div v-if="response" class="notice result">
        <b class="num">HTTP {{ response.status }}</b><br />{{ response.body.detail }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import axios from 'axios'
import { NButton } from 'naive-ui'
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
.wrap { flex: 1; display: grid; place-items: start center; padding: 32px 16px; }
.card { width: 100%; max-width: 560px; background: var(--panel); border: 1px solid var(--line); padding: 24px; }
h2 { display: flex; align-items: center; gap: 10px; margin: 0 0 8px; font-size: 18px; }
p { margin: 6px 0; }
.flag { font-size: 11px; font-weight: 500; border: 1px solid var(--accent); color: var(--accent); padding: 1px 6px; }
.small { font-size: 12px; }
.schema { background: var(--panel2); padding: 10px 12px; margin: 14px 0; }
.sec { font-size: 12px; font-weight: 600; margin-bottom: 6px; color: var(--ink2); }
.field { font-size: 13px; line-height: 1.8; }
code { font-family: var(--mono); font-size: 12px; }
.result { margin-top: 12px; }
</style>
