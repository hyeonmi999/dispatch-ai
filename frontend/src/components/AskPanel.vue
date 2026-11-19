<template>
  <div>
    <n-input
      v-model:value="question"
      type="textarea"
      :autosize="{ minRows: 2, maxRows: 4 }"
      placeholder="예) 혼잡 시간대에 운행횟수를 늘리려면 어떤 절차가 필요한가요?"
      @keydown.enter.exact.prevent="send"
    />
    <div class="examples">
      <button v-for="q in examples" :key="q" class="ex" @click="question = q">{{ q }}</button>
    </div>
    <n-button type="primary" size="small" block :loading="loading" @click="send">질문하기 ({{ llm === 'qwen' ? '로컬 모델' : 'GPT-4o-mini' }})</n-button>

    <p v-if="loading" class="sm" role="status">{{ pendingNotice }}</p>

    <div v-if="result" class="result">
      <div v-if="!result.found" class="notice warn">
        <b>답변 거절 · 문서에 근거 없음</b><br />{{ result.answer }}<div class="sm">{{ result.note }}</div>
      </div>
      <template v-else>
        <div class="answer">{{ result.answer }}</div>
        <div class="sm">출처 {{ result.sources.length }}건 · {{ result.elapsed_sec }}초</div>
        <SourceList :sources="result.sources" />
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { NButton, NInput, useMessage } from 'naive-ui'
import { api, errorMessage } from '../api'
import SourceList from './SourceList.vue'

const props = defineProps({
  domain: { type: String, default: 'bus' },
  llm: { type: String, default: 'qwen' },
  embedding: { type: String, default: 'bge' },
  role: { type: String, default: 'citizen' },
})
const message = useMessage()
const question = ref('')
const loading = ref(false)
const pendingNotice = ref('문서 검색 상태를 확인하는 중입니다.')
const result = ref(null)
const examples = [
  '노선버스 운행횟수를 늘리는 사업계획 변경 절차는?',
  '서울시 버스 혼잡도는 어떻게 구분하나요?',
  '시내버스 준공영제에서 시의 역할은?',
]

async function send() {
  if (question.value.trim().length < 2) return
  pendingNotice.value = '문서 검색 상태를 확인하는 중입니다.'
  loading.value = true
  try {
    const status = await api.knowledgeStatus(props.domain, props.embedding)
    pendingNotice.value = status?.rebuild_required
      ? '문서가 변경되어 검색 색인을 갱신한 후 답변을 생성합니다. 시간이 더 걸릴 수 있습니다.'
      : '문서를 검색하고 답변을 생성하는 중입니다.'
    result.value = await api.ask(props.domain, { question: question.value, llm: props.llm, embedding: props.embedding, role: props.role })
  } catch (e) {
    message.error(errorMessage(e))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.examples { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; margin: 6px 0; }
.ex { font-size: 11px; color: var(--ink2); text-align: left; text-decoration: underline; text-underline-offset: 2px; padding: 2px 0; }
.ex:hover { color: var(--ink); }
.result { margin-top: 10px; }
.answer { border: 1px solid var(--line); padding: 8px 10px; font-size: 13px; line-height: 1.6; white-space: pre-wrap; margin-bottom: 6px; }
</style>
