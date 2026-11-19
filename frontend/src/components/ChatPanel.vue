<template>
  <div class="chat">
    <div ref="listEl" class="list" aria-live="polite">
      <!-- 빈 상태 -->
      <div v-if="!turns.length && !loading" class="empty">
        <p class="empty-note">노선·날짜·시간을 넣어 물어보세요. 예측·배차 권고·운수 규정 중 맞는 기능을 불러 답합니다.</p>
        <button v-for="q in examples" :key="q" class="ex" @click="send(q)">{{ q }}</button>
      </div>

      <div v-for="(t, i) in turns" :key="i" class="turn" :class="t.role">
        <div v-if="t.role === 'user'" class="bubble">{{ t.content }}</div>

        <!-- 에러 상태 -->
        <div v-else-if="t.error" class="notice warn">답변을 받지 못했습니다: {{ t.error }}</div>

        <template v-else>
          <!-- 답변은 텍스트로만 그림 (v-html 사용 안 함). 버스 아이콘은 직접 그린 SVG -->
          <div class="row">
            <BusIcon class="bus" :size="26" label="배차 상담 챗봇" />
            <div class="answer">{{ t.content }}</div>
          </div>
          <div class="tags">
            <span v-for="tool in t.tools ?? []" :key="tool.name" class="tag" :class="{ bad: !tool.ok }">
              부른 기능 · {{ TOOL_NAMES[tool.name] ?? tool.name }}{{ tool.ok ? '' : ' (오류)' }}
            </span>
            <span v-if="t.intent === 'clarify'" class="tag">되묻기</span>
            <span v-if="t.intent === 'out_of_scope'" class="tag">범위 밖</span>
            <span v-if="t.numbers_verified === true" class="tag ok">숫자 검증 통과</span>
            <span v-if="t.numbers_verified === false" class="tag bad">숫자 검증 실패</span>
            <span v-if="(t.tools ?? []).length" class="tag">담당자 검토 필요</span>
            <span v-if="t.llm_rejected" class="tag">LLM 문장 버림 · 문장 틀 사용</span>
            <button v-if="canGoto(t)" class="link" @click="emit('goto', gotoTarget(t))">이 노선·시간 보기</button>
            <button v-if="t.data?.options" class="mapbtn" @click="openGuide(t)"><BusIcon :size="13" /> 지도로 보기</button>
            <button v-if="isRouteInfo(t)" class="link" @click="emit('select-route', t.slots.route)">대시보드에서 이 노선 보기</button>
          </div>
          <SourceList v-if="(t.sources ?? []).length" :sources="t.sources" />
        </template>
      </div>

      <!-- 로딩 상태 -->
      <div v-if="loading" class="turn assistant"><n-spin size="small" /> <span class="sm">답변을 만드는 중</span></div>
    </div>

    <div class="input">
      <n-input
        v-model:value="draft"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 3 }"
        :maxlength="MESSAGE_MAX"
        placeholder="예) 160번 내일 오후 3시에 붐벼?"
        @keydown.enter.exact.prevent="send()"
      />
      <n-button type="primary" size="small" :loading="loading" :disabled="draft.trim().length < 2" @click="send()">보내기</n-button>
    </div>
    <GuideModal v-model:show="guideOpen" :guide="guideData" :geometries="geometries" />

    <div class="opts">
      <label><input v-model="useLlm" type="checkbox" />{{ llm === 'qwen' ? '로컬 모델' : 'GPT-4o-mini' }}로 문장 다듬기 (느림)</label>
      <label :title="locationNote"><input ref="hereBox" type="checkbox" :checked="!!here" @change="toggleHere" />현재 위치 사용 (길안내)</label>
      <button v-if="turns.length" class="link" @click="turns = []">대화 지우기</button>
    </div>
    <!-- 위치를 못 가져온 이유는 마우스를 올리지 않아도 보이게 -->
    <div v-if="locationError" class="locerr" role="status">{{ locationError }}</div>
  </div>
</template>

<script setup>
import { nextTick, ref } from 'vue'
import { NButton, NInput, NSpin } from 'naive-ui'
import { api, errorMessage } from '../api'
import BusIcon from './BusIcon.vue'
import GuideModal from './GuideModal.vue'
import SourceList from './SourceList.vue'

const props = defineProps({
  domain: { type: String, default: 'bus' },
  llm: { type: String, default: 'qwen' },
  embedding: { type: String, default: 'bge' },
  geometries: { type: Object, default: () => ({}) },     // 길안내 팝업 지도용 노선 좌표
  role: { type: String, default: 'citizen' },            // 문서 열람 역할 (규정 답변에 쓸 수 있는 문서 등급)
})
// goto: 답변의 노선·날짜·시간으로 대시보드 이동 / select-route: 그 노선을 대시보드에서 선택
const emit = defineEmits(['goto', 'select-route'])

// 서버 상한과 같은 값 (app/config.py CHAT_MESSAGE_MAX · CHAT_HISTORY_MAX · CHAT_HISTORY_ITEM_MAX)
const MESSAGE_MAX = 500
const HISTORY_MAX = 10
const HISTORY_ITEM_MAX = 2000
const TOOL_NAMES = { predict: '승객 예측', advise: '배차 권고', ask: '규정 질의응답', guide: '길안내', route_info: '노선 정보', simulate: '배차간격 시뮬레이션', optimize: '전 노선 최적화' }
const examples = ['160번 버스 노선 알려줘', '143번 내일 오전 8시에 붐벼?', '143번 내일 8시 증차해야 해?', '운행횟수를 늘리려면 어떤 절차가 필요해?', '정릉우체국앞에서 종로3가역 어떻게 가?']

const turns = ref([])
const draft = ref('')
const loading = ref(false)
const useLlm = ref(false)
const listEl = ref(null)

// 현재 위치: 켜 두면 길안내 질문("여기서 ○○ 어떻게 가?")의 출발지로 보냄. 서버에 저장하지 않음
const here = ref(null)
const locationNote = ref('브라우저의 위치 권한이 필요합니다')
const locationError = ref('')
const hereBox = ref(null)
// 체크 표시는 위치를 실제로 얻었을 때만 켜짐 (누르는 순간 브라우저가 먼저 켜 버리므로 되돌림)
const syncBox = () => {
  if (hereBox.value) hereBox.value.checked = !!here.value
}
function failHere(text) {
  locationError.value = text
  locationNote.value = text
  syncBox()
}
// 현재 위치를 얻어 here에 넣고 돌려줌. 못 얻으면 이유를 화면에 보이고 null
function locate() {
  locationError.value = ''
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      failHere('이 브라우저는 현재 위치를 지원하지 않습니다. 출발 정류장 이름을 적어 주세요.')
      resolve(null)
      return
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        here.value = { lat: pos.coords.latitude, lng: pos.coords.longitude }
        locationNote.value = `현재 위치 ${here.value.lat.toFixed(4)}, ${here.value.lng.toFixed(4)}`
        syncBox()
        resolve(here.value)
      },
      () => {
        failHere('현재 위치를 가져오지 못했습니다. 브라우저의 위치 권한을 허용하거나 출발 정류장 이름을 적어 주세요.')
        resolve(null)
      },
      { enableHighAccuracy: true, timeout: 10000 },
    )
  })
}
function toggleHere() {
  if (here.value) {
    locationError.value = ''
    here.value = null
    locationNote.value = '브라우저의 위치 권한이 필요합니다'
    syncBox()
    return
  }
  syncBox()
  locate()
}
// 길안내는 답변으로 설명하고, '지도로 보기'를 누를 때만 팝업으로 경로를 보여 줌
const guideOpen = ref(false)
const guideData = ref(null)
function openGuide(t) {
  guideData.value = { ...t.data, user: t.user ?? null }
  guideOpen.value = true
}

// 대시보드에 있는(예측하는) 노선일 때만 '이 노선 보기'를 보임
const isRouteInfo = (t) => (t.tools ?? []).some((x) => x.ok && x.name === 'route_info') && !!props.geometries?.[t.slots?.route]
const canGoto = (t) => !!(t.slots?.route && t.slots?.date && t.slots?.hour != null && (t.tools ?? []).some((x) => x.ok && x.name !== 'ask'))
const gotoTarget = (t) => ({ route: t.slots.route, date: t.slots.date, hour: t.slots.hour })

async function scrollDown() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

async function send(text) {
  const message = (text ?? draft.value).trim()
  if (message.length < 2 || loading.value) return
  // 이어 묻기용 이전 대화 (오류 난 답은 뺌)
  const history = turns.value
    .filter((t) => !t.error)
    .slice(-HISTORY_MAX)
    .map((t) => ({ role: t.role, content: t.content.slice(0, HISTORY_ITEM_MAX) }))
  turns.value.push({ role: 'user', content: message })
  draft.value = ''
  loading.value = true
  scrollDown()
  try {
    let user = here.value
    const body = { message, history, llm: props.llm, embedding: props.embedding, use_llm: useLlm.value, role: props.role }
    if (user) body.location = user
    let r = await api.chat(props.domain, body)
    // "여기서 ○○ 어떻게 가?"인데 현재 위치가 꺼져 있으면, 되묻지 않고 위치를 얻어 한 번 더 물음
    if (r?.intent === 'clarify' && r?.slots?.from_here && !user) {
      user = await locate()
      if (user) r = await api.chat(props.domain, { ...body, location: user })
    }
    const turn = {
      role: 'assistant', content: r?.answer ?? '', intent: r?.intent, slots: r?.slots ?? {},
      tools: r?.tools ?? [], sources: r?.sources ?? [], numbers_verified: r?.numbers_verified ?? null,
      llm_rejected: !!r?.llm_rejected, data: r?.data ?? null, user,
    }
    turns.value.push(turn)
  } catch (e) {
    turns.value.push({ role: 'assistant', content: '', error: errorMessage(e) })
  } finally {
    loading.value = false
    scrollDown()
  }
}
</script>

<style scoped>
.chat { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.list { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 10px; padding-bottom: 6px; }
.empty { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.empty .empty-note { padding: 8px 0; text-align: left; }
.ex { font-size: 12px; color: var(--ink2); text-align: left; text-decoration: underline; text-underline-offset: 2px; padding: 3px 0; }
.ex:hover { color: var(--ink); }
.turn.user { display: flex; justify-content: flex-end; }
.bubble { max-width: 85%; background: var(--ink); color: var(--panel); padding: 6px 10px; font-size: 13px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
.row { display: flex; align-items: flex-start; gap: 6px; }
.bus { margin-top: 2px; color: var(--accent); }
.answer { flex: 1; min-width: 0; border: 1px solid var(--line); padding: 8px 10px; font-size: 13px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.tags { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin: 4px 0; }
.tag { font-size: 11px; color: var(--ink2); border: 1px solid var(--line); padding: 1px 6px; border-radius: 2px; white-space: nowrap; }
.tag.ok { border-color: var(--ink2); }
.tag.bad { border-color: var(--accent); color: var(--accent); }
.link { font-size: 11px; color: var(--ink2); text-decoration: underline; text-underline-offset: 2px; }
.link:hover { color: var(--ink); }
.mapbtn { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 600; color: var(--on); background: var(--accent); padding: 2px 8px; border-radius: 2px; }
.mapbtn:hover { opacity: 0.9; }
.input { display: flex; align-items: flex-end; gap: 6px; padding-top: 6px; border-top: 1px solid var(--line); }
.opts { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 4px 8px; margin-top: 4px; font-size: 11px; color: var(--ink3); }
.opts label { display: flex; align-items: center; gap: 5px; cursor: pointer; }
.opts input { accent-color: var(--accent); margin: 0; }
.locerr { margin-top: 4px; font-size: 11px; color: var(--accent); line-height: 1.4; }
</style>
