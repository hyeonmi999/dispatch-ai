<template>
  <div class="app">
    <header class="bar">
      <div class="brand">
        <AppLogo />
        <div>
          <h1>통합 배차 AI Agent</h1>
          <p>1차 스프린트 · 버스 배차 최적화 도우미 · Oh!gimbak</p>
        </div>
      </div>

      <nav class="tabs" aria-label="배차 도메인">
        <button v-for="d in domains" :key="d.name" class="tab" :class="{ on: domain === d.name }" :aria-pressed="domain === d.name" @click="domain = d.name">
          {{ d.title }}<small v-if="d.status !== 'ready'">준비 중</small>
        </button>
      </nav>

      <div class="bar-right">
        <label v-if="domain === 'bus'" class="document-role">
          문서 열람 역할 (시연)
          <select v-model="documentRole">
            <option value="citizen">시민</option>
            <option value="staff">배차 담당자</option>
            <option value="admin">관리자</option>
          </select>
        </label>
        <span v-if="meta" class="bstat">{{ meta.data_mode === 'real' ? '실데이터' : '합성 데이터' }}</span>
        <span class="bstat">DB {{ health?.db?.available ? '연결' : '미연결' }}</span>
        <n-popover trigger="click" placement="bottom-end">
          <template #trigger><button class="bbtn">모델 설정</button></template>
          <div class="settings">
            <label>LLM</label>
            <n-select v-model:value="llm" :options="llmOptions" size="small" style="width: 190px" />
            <label>임베딩</label>
            <n-select v-model:value="embedding" :options="embeddingOptions" size="small" style="width: 190px" />
          </div>
        </n-popover>
        <button class="bbtn" @click="toggleTheme">{{ isNight ? '주간 화면' : '야간 화면' }}</button>
        <div class="accent-pick">
          <n-popover v-model:show="accentOpen" trigger="click" placement="bottom-end">
            <template #trigger>
              <button class="accent-btn" aria-haspopup="dialog" :aria-expanded="accentOpen" :title="`포인트색 바꾸기 (지금: ${accentInfo.label})`">
                <span class="accent-label">포인트색</span>
                <span class="swatch" :style="{ '--sw': accentInfo[isNight ? 'night' : 'day'].fill }" aria-hidden="true"></span>
                <span class="sr-only">지금 {{ accentInfo.label }}, 눌러서 바꾸기</span>
              </button>
            </template>
            <div class="accent-pop" role="dialog" aria-label="포인트색 고르기">
              <p class="ap-title">포인트색</p>
              <div class="ap-grid" role="radiogroup" aria-label="미리 정한 색">
                <button v-for="(a, key) in ACCENTS" :key="key" class="swatch" role="radio" :aria-checked="accent === key"
                        :title="a.label" :aria-label="a.label" :style="{ '--sw': a[isNight ? 'night' : 'day'].fill }" @click="setAccent(key)"></button>
              </div>
              <label class="ap-custom">
                <input type="color" :value="accentInfo.day.fill" @input="setAccent($event.target.value)" />
                직접 고르기
              </label>
              <p class="ap-now">지금: {{ accentInfo.label }}</p>
              <p v-if="accentInfo.warn" class="ap-warn">{{ accentInfo.warn }}</p>
              <p class="ap-note">글자가 잘 보이도록 낮·야간에 맞춰 진하기를 자동으로 조절합니다. 이 브라우저에만 저장됩니다.</p>
            </div>
          </n-popover>
        </div>
      </div>
    </header>

    <ComingSoon v-if="domain !== 'bus'" :domain="currentDomain" />

    <template v-else>
      <div class="tools">
        <div class="date">
          <button class="ibtn" :disabled="!prevDate" aria-label="이전 날짜" @click="date = prevDate">
            <svg viewBox="0 0 14 14" aria-hidden="true"><path d="M9 2L4 7L9 12" fill="none" stroke="currentColor" stroke-width="1.8" /></svg>
          </button>
          <span class="lbl">날짜</span>
          <n-date-picker
            v-model:formatted-value="date"
            value-format="yyyy-MM-dd"
            type="date"
            size="small"
            :is-date-disabled="isDateDisabled"
            :disabled="!meta"
            input-readonly
            style="width: 136px"
          />
          <button class="ibtn" :disabled="!nextDate" aria-label="다음 날짜" @click="date = nextDate">
            <svg viewBox="0 0 14 14" aria-hidden="true"><path d="M5 2L10 7L5 12" fill="none" stroke="currentColor" stroke-width="1.8" /></svg>
          </button>
          <button class="tbtn" :disabled="!canGoNow" :title="canGoNow ? '오늘 날짜·현재 시간의 예측으로 이동' : '오늘은 예측 범위 밖입니다'" @click="goNow">지금</button>
          <span class="dlab">{{ dayLabel }}</span>
          <span v-if="dayFlag" class="flag" :class="{ fc: isForecastDate }">{{ dayFlag }}</span>
          <span
            v-if="meta"
            class="sm fresh"
            title="서울시 승객 데이터는 주 단위로 8일 뒤 공개되어, 실측 마지막 날 + 21일까지 예측합니다"
          >실측 ~{{ meta.dates.max_date.slice(5) }} · 예측 ~{{ meta.dates.max_forecast_date.slice(5) }}</span>
        </div>

        <div class="time">
          <button class="ibtn" :aria-label="playing ? '시간 재생 정지' : '시간 재생'" @click="togglePlay">
            <svg v-if="playing" viewBox="0 0 14 14" aria-hidden="true"><rect x="3" y="2" width="3" height="10" fill="currentColor" /><rect x="8" y="2" width="3" height="10" fill="currentColor" /></svg>
            <svg v-else viewBox="0 0 14 14" aria-hidden="true"><path d="M3 2L12 7L3 12Z" fill="currentColor" /></svg>
          </button>
          <span class="lbl">시간</span>
          <n-slider v-model:value="hour" :min="0" :max="23" :step="1" :format-tooltip="(v) => `${v}시`" style="flex: 1; min-width: 0" />
          <span class="hr num">{{ String(hour).padStart(2, '0') }}시</span>
        </div>

        <div class="seg-g">
          <span class="lbl">화면</span>
          <div class="segb" role="group" aria-label="화면 구성">
            <button v-for="p in PRESETS" :key="p.id" :class="{ on: preset === p.id }" :aria-pressed="preset === p.id" @click="applyPreset(p.id)">{{ p.name }}</button>
            <button v-for="t in customTabs" :key="t.id" class="mine" :class="{ on: preset === t.id }" :aria-pressed="preset === t.id"
                    :title="`내가 만든 화면: ${t.name}`" @click="applyCustomTab(t)">{{ t.name }}</button>
          </div>
          <!-- 내 화면 탭 만들기·관리 (Task P-6): 이 브라우저에만 저장 -->
          <n-popover v-model:show="tabMenuOpen" trigger="click" placement="bottom-end">
            <template #trigger>
              <button class="tbtn add" aria-label="내 화면 탭 만들기·관리" title="지금 화면으로 내 탭 만들기">＋</button>
            </template>
            <form class="tab-menu" @submit.prevent="createCustomTab">
              <p class="tm-h">지금 화면으로 새 탭 만들기</p>
              <label class="tm-name">이름
                <input v-model.trim="newTabName" :maxlength="CUSTOM_TAB_NAME_MAX" placeholder="예) 아침 점검" />
              </label>
              <fieldset class="tm-panels">
                <legend>넣을 패널 <small>(켜진 패널은 지금 자리 그대로)</small></legend>
                <label v-for="id in panelIds" :key="id"><input v-model="newTabPanels" type="checkbox" :value="id" />{{ TITLES[id] }}</label>
              </fieldset>
              <p v-if="tabError" class="tm-err" role="alert">{{ tabError }}</p>
              <button type="submit" class="tm-save" :disabled="customTabs.length >= CUSTOM_TAB_MAX">
                {{ customTabs.length >= CUSTOM_TAB_MAX ? `최대 ${CUSTOM_TAB_MAX}개까지` : '저장' }}
              </button>
              <template v-if="customTabs.length">
                <p class="tm-h">내 탭 {{ customTabs.length }}/{{ CUSTOM_TAB_MAX }}</p>
                <ul class="tm-list">
                  <li v-for="(t, i) in customTabs" :key="t.id" :class="{ cur: preset === t.id }">
                    <span class="tm-order">
                      <button type="button" class="tm-arrow" :disabled="i === 0" :aria-label="`${t.name} 탭을 앞으로`" @click="moveCustomTab(t, -1)">▲</button>
                      <button type="button" class="tm-arrow" :disabled="i === customTabs.length - 1" :aria-label="`${t.name} 탭을 뒤로`" @click="moveCustomTab(t, 1)">▼</button>
                    </span>
                    <input v-if="renamingId === t.id" v-model="renameValue" class="tm-rename" :maxlength="CUSTOM_TAB_NAME_MAX"
                           :aria-label="`${t.name} 탭의 새 이름`" @keydown.enter.prevent="finishRename(t)" @keydown.esc.prevent="renamingId = null"
                           @blur="finishRename(t)" />
                    <span v-else class="tm-tname">{{ t.name }}</span>
                    <button v-if="renamingId !== t.id" type="button" class="tm-link" @click="startRename(t)">이름</button>
                    <button type="button" class="tm-link" @click="overwriteCustomTab(t)">지금 화면으로</button>
                    <button type="button" class="tm-link del" @click="deleteCustomTab(t)">삭제</button>
                  </li>
                </ul>
              </template>
              <p class="tm-note">이 브라우저에만 저장됩니다. 내 탭을 연 채로 패널을 옮기거나 켜고 끄면 그 탭에 바로 저장됩니다.</p>
            </form>
          </n-popover>
          <n-popover trigger="click" placement="bottom-end">
            <template #trigger><button class="tbtn">패널 {{ visibleCount }}/{{ panelIds.length }}</button></template>
            <div class="panel-menu">
              <label v-for="id in panelIds" :key="id">
                <input type="checkbox" :checked="layout[id].on" @change="togglePanel(id)" />{{ TITLES[id] }}
              </label>
            </div>
          </n-popover>
        </div>

        <div v-if="excludedNote" class="notice warn excl">실측 제외: {{ excludedNote }}. 그래프에 실제 승차가 표시되지 않습니다.</div>
      </div>

      <PanelGrid v-if="meta" v-model:layout="layout" :metas="metas" :allowed-panels="panelIds" @commit="commitLayout" @close="togglePanel">
        <template #routes>
          <div class="routes">
            <button v-for="r in routeRows" :key="r.route" class="rt" :class="{ on: r.route === selected }" :aria-pressed="r.route === selected" @click="selected = r.route">
              <span class="rno" :class="[`r${r.route}`, { on: r.route === selected }]">{{ r.route }}</span>
              <span class="ell"><span class="sm">{{ r.route_type }}</span><br />{{ r.start }} ↔ {{ r.end }}</span>
              <span class="v num">{{ fmtCongestion(r.max_congestion) }}</span>
              <span class="lv"><i class="chip" :class="levelClass(r.level)"></i>{{ r.level || '-' }}</span>
            </button>
          </div>
        </template>

        <template #map>
          <MapView :geometries="geometries" :overview="overview" :selected="selected" @select="(r) => (selected = r)" />
        </template>

        <template #forecast>
          <ForecastChart :hours="timeseries?.hours || []" :hour="hour" @hour="(h) => (hour = h)" />
          <div class="chart-foot">
            <template v-if="hourRow">
              <b>{{ hour }}시</b> · 모델 <b class="num">{{ fmtNum(hourRow.pred) }}</b>명 · 작년 같은 요일 {{ fmtNum(hourRow.baseline) }}명 ·
              실제 {{ hourRow.actual != null ? fmtNum(hourRow.actual) + '명' : isForecastDate ? '없음 (향후 예측)' : '없음 (원천 결측·이상일)' }}
            </template>
            <span v-else class="muted">{{ hour }}시는 운행 데이터가 없습니다</span>
          </div>
        </template>

        <template #stops>
          <StopChart :stops="stops?.stops || []" />
        </template>

        <template #status>
          <StatusPanel :route="selectedMeta" :hour="hour" :hour-row="hourRow" />
        </template>

        <template #advice>
          <AdvicePanel
            :route="selectedMeta"
            :date="date"
            :hour="hour"
            :llm="llm"
            :embedding="embedding"
            :db-available="!!health?.db?.available"
            @select-route="(r) => (selected = r)"
            @changed="historyTick++"
          />
        </template>

        <template #optimize>
          <OptimizePanel :date="date" :hour="hour" :selected-route="selected" :routes="meta.routes.map((r) => r.route)" @goto="goto" />
        </template>

        <template #whatif>
          <WhatIfPanel :route="selectedMeta" :date="date" :hour="hour" />
        </template>

        <template #qa>
          <AskPanel :key="documentRole" domain="bus" :llm="llm" :embedding="embedding" :role="documentRole" />
        </template>

        <template #chat>
          <ChatPanel domain="bus" :llm="llm" :embedding="embedding" :geometries="geometries" :role="documentRole" @goto="goto" @select-route="(r) => (selected = r)" />
        </template>

        <template #metrics>
          <MetricsCard :metrics="meta.metrics" />
        </template>

        <template #backtest>
          <BacktestPanel />
        </template>

        <template #drift>
          <DriftPanel />
        </template>

        <template #traffic>
          <TrafficPanel :selected-route="selected" @select-route="(r) => (selected = r)" />
        </template>

        <template #documents>
          <DocumentAdminPanel v-if="documentRole === 'admin'" :role="documentRole" />
        </template>

        <template #history>
          <HistoryPanel
            :route="selectedMeta"
            :db-available="!!health?.db?.available"
            :refresh="historyTick"
            @select-route="(r) => (selected = r)"
            @goto="goto"
          />
        </template>
      </PanelGrid>

      <div v-else class="loading">
        <n-spin v-if="!loadError" description="모델·데이터 불러오는 중" />
        <div v-else class="notice warn">백엔드 연결 실패: {{ loadError }}<br />FastAPI 서버(포트 8000)가 실행 중인지 확인하세요.</div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NDatePicker, NPopover, NSelect, NSlider, NSpin, useMessage } from 'naive-ui'
import { api, errorMessage, fmtCongestion, levelClass } from '../api'
import {
  CUSTOM_TAB_MAX, CUSTOM_TAB_NAME_MAX, PRESETS, TITLES, fillLayout, layoutWith, loadCustomTabs, loadSaved, makeLayout, placePanel,
  resolve, saveCustomTabs, saveLayout,
} from '../layout'
import { ACCENTS, accent, accentInfo, isNight, setAccent, toggleTheme } from '../theme'
import DocumentAdminPanel from './DocumentAdminPanel.vue'
import AdvicePanel from './AdvicePanel.vue'
import AppLogo from './AppLogo.vue'
import AskPanel from './AskPanel.vue'
import BacktestPanel from './BacktestPanel.vue'
import DriftPanel from './DriftPanel.vue'
import ChatPanel from './ChatPanel.vue'
import ComingSoon from './ComingSoon.vue'
import ForecastChart from './ForecastChart.vue'
import HistoryPanel from './HistoryPanel.vue'
import MapView from './MapView.vue'
import MetricsCard from './MetricsCard.vue'
import OptimizePanel from './OptimizePanel.vue'
import PanelGrid from './PanelGrid.vue'
import StatusPanel from './StatusPanel.vue'
import TrafficPanel from './TrafficPanel.vue'
import StopChart from './StopChart.vue'
import WhatIfPanel from './WhatIfPanel.vue'

const accentOpen = ref(false)     // 포인트색 팔레트 열림

const message = useMessage()

// 서버 응답 전이나 백엔드가 꺼져 있어도 버튼 3개를 항상 표시 (응답이 오면 서버 값으로 교체)
const domains = ref([
  { name: 'bus', title: '버스 배차', status: 'ready' },
  { name: 'logistics', title: '물류 배차', description: '거점·권역별 화물량 예측과 차량 배차 권고 (준비 중)', status: 'not_implemented' },
  { name: 'field_service', title: '서비스 출동 배차', description: '권역별 출동 요청 예측과 기사 배치 권고 (준비 중)', status: 'not_implemented' },
])
const domain = ref('bus')
const documentRole = ref('citizen')
const llm = ref('qwen')
const embedding = ref('bge')
// 로컬 모델 이름은 서버 설정(.env LOCAL_MODEL_NAME)을 따름
const llmOptions = computed(() => {
  const local = health.value?.llm?.qwen
  const name = local?.model?.split('/').pop() ?? 'Qwen'
  return [
    { label: `${name}${local?.adapter ? ' + LoRA' : ''} (로컬)`, value: 'qwen' },
    { label: 'OpenAI gpt-4o-mini', value: 'openai' },
  ]
})
const embeddingOptions = [
  { label: 'bge-m3 (로컬)', value: 'bge' },
  { label: 'OpenAI 3-small', value: 'openai' },
]

// 패널 배치
const saved = loadSaved()
const panelIds = computed(() => Object.keys(TITLES).filter((id) => id !== 'documents' || documentRole.value === 'admin'))
const layout = ref(saved?.layout ?? makeLayout('ops'))
const preset = ref(saved ? saved.preset : 'ops')
const visibleCount = computed(() => panelIds.value.filter((id) => layout.value[id].on).length)
const historyTick = ref(0)

// 배치를 바꾸면(끌기·크기·패널 켜고 끄기) 내 탭을 연 상태에서는 그 탭에 바로 저장 (Task P-6 자동 저장)
function commitLayout(next) {
  layout.value = next
  const mine = customTabs.value.find((t) => t.id === preset.value)
  if (mine) {
    persistTabs(customTabs.value.map((t) => (t.id === mine.id ? { ...t, layout: next } : t)))
  } else {
    preset.value = null
  }
  saveLayout(next, preset.value)
}
function applyPreset(id) {
  layout.value = resolve(makeLayout(id))
  preset.value = id
  saveLayout(layout.value, id)
}
function togglePanel(id) {
  // 새로 켠 패널은 위에서부터 첫 빈칸에, 끈 패널의 자리는 나머지가 채움
  commitLayout(layout.value[id].on
    ? resolve({ ...layout.value, [id]: { ...layout.value[id], on: false } })
    : placePanel(layout.value, id))
}

// 내가 만든 화면 탭 (Task P-6)
const customTabs = ref(loadCustomTabs())
const tabMenuOpen = ref(false)
const newTabName = ref('')
const newTabPanels = ref([])
const tabError = ref('')
watch(tabMenuOpen, (open) => {
  renamingId.value = null
  if (!open) return
  newTabName.value = ''
  tabError.value = ''
  newTabPanels.value = panelIds.value.filter((id) => layout.value[id].on)   // 처음에는 지금 켜진 패널
})
function persistTabs(list) {
  customTabs.value = list
  if (!saveCustomTabs(list)) message.warning('브라우저 저장소를 쓸 수 없어 이번 화면에만 적용됩니다')
}
function showTab(id, next) {
  layout.value = next
  preset.value = id
  saveLayout(next, id)
}
function nameProblem(name, exceptId = null) {
  const taken = [...PRESETS.map((p) => p.name), ...customTabs.value.filter((t) => t.id !== exceptId).map((t) => t.name)]
  return !name ? '탭 이름을 적어 주세요' : taken.includes(name) ? '같은 이름의 탭이 있습니다' : ''
}
function createCustomTab() {
  const name = newTabName.value.slice(0, CUSTOM_TAB_NAME_MAX)
  tabError.value = nameProblem(name)
    || (!newTabPanels.value.length ? '패널을 하나 이상 골라 주세요' : '')
    || (customTabs.value.length >= CUSTOM_TAB_MAX ? `내 탭은 ${CUSTOM_TAB_MAX}개까지입니다. 하나를 지운 뒤 다시 만드세요` : '')
  if (tabError.value) return
  const next = layoutWith(layout.value, newTabPanels.value)
  const tab = { id: `u-${Date.now().toString(36)}`, name, layout: next }
  persistTabs([...customTabs.value, tab])
  showTab(tab.id, next)
  tabMenuOpen.value = false
  message.success(`'${name}' 탭을 저장했습니다`)
}
function applyCustomTab(tab) {
  showTab(tab.id, resolve(fillLayout(tab.layout)))
}
function overwriteCustomTab(tab) {
  const next = resolve(layout.value)
  persistTabs(customTabs.value.map((t) => (t.id === tab.id ? { ...t, layout: next } : t)))
  showTab(tab.id, next)
  message.success(`'${tab.name}' 탭을 지금 화면으로 바꿨습니다`)
}
// 이름 바꾸기 (같은 팝업 안에서)
const renamingId = ref(null)
const renameValue = ref('')
function startRename(tab) {
  renamingId.value = tab.id
  renameValue.value = tab.name
  tabError.value = ''
}
function finishRename(tab) {
  if (renamingId.value !== tab.id) return
  const name = renameValue.value.trim().slice(0, CUSTOM_TAB_NAME_MAX)
  if (name === tab.name) {
    renamingId.value = null
    return
  }
  tabError.value = nameProblem(name, tab.id)
  if (tabError.value) return
  persistTabs(customTabs.value.map((t) => (t.id === tab.id ? { ...t, name } : t)))
  renamingId.value = null
}
// 순서 바꾸기: 위·아래로 한 칸
function moveCustomTab(tab, step) {
  const list = [...customTabs.value]
  const i = list.findIndex((t) => t.id === tab.id)
  const j = i + step
  if (i < 0 || j < 0 || j >= list.length) return
  ;[list[i], list[j]] = [list[j], list[i]]
  persistTabs(list)
}
function deleteCustomTab(tab) {
  persistTabs(customTabs.value.filter((t) => t.id !== tab.id))
  if (preset.value === tab.id) {
    preset.value = null
    saveLayout(layout.value, null)
  }
  message.success(`'${tab.name}' 탭을 지웠습니다`)
}

const meta = ref(null)
const health = ref(null)
const loadError = ref('')
const geometries = ref({})
const overview = ref(null)
const timeseries = ref(null)
const stops = ref(null)

const date = ref(null)
const hour = ref(7)
const selected = ref(null)
const playing = ref(false)
let timer = null

const missingDates = computed(() => new Set(meta.value?.dates.missing_dates || []))
// 원천 이상으로 실측을 뺀 날 (예: 승차 집계 누락, 파업 추정)
const excludedNote = computed(() => {
  const ex = meta.value?.dates.excluded?.[date.value]
  if (!ex) return ''
  const reasons = [...new Set(Object.values(ex))]
  return reasons.map((r) => `${r} (${Object.keys(ex).filter((k) => ex[k] === r).join(', ')}번)`).join(' · ')
})
// 권고 이력에서 저장된 권고를 열면 그 권고의 노선·날짜·시간으로 이동
function goto({ route, date: d, hour: h }) {
  if (playing.value) togglePlay()
  selected.value = route
  date.value = d
  hour.value = h
}

const fmtDay = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`
const today = fmtDay(new Date())

function isDayAvailable(s) {
  if (!meta.value) return false
  return s >= meta.value.dates.min_date && s <= meta.value.dates.max_forecast_date && !missingDates.value.has(s)
}
const isDateDisabled = (ts) => !isDayAvailable(fmtDay(new Date(ts)))

// 이전·다음 날짜: 결측일은 건너뜀
function stepDate(n) {
  if (!date.value || !meta.value) return null
  const d = new Date(date.value + 'T00:00:00')
  for (let i = 0; i < 60; i += 1) {
    d.setDate(d.getDate() + n)
    const s = fmtDay(d)
    if (s < meta.value.dates.min_date || s > meta.value.dates.max_forecast_date) return null
    if (isDayAvailable(s)) return s
  }
  return null
}
const prevDate = computed(() => stepDate(-1))
const nextDate = computed(() => stepDate(1))

// '지금' 버튼: 오늘 날짜 + 현재 시각으로 이동 (예측 범위 안일 때)
const canGoNow = computed(() => isDayAvailable(today))
function goNow() {
  if (playing.value) togglePlay()
  date.value = today
  hour.value = new Date().getHours()
}
const fmtNum = (v) => (v == null ? '-' : Math.round(v).toLocaleString())
const currentDomain = computed(() => domains.value.find((d) => d.name === domain.value))
const selectedMeta = computed(() => meta.value?.routes.find((r) => r.route === selected.value))
const hourRow = computed(() => timeseries.value?.hours.find((h) => h.hour === hour.value) || null)
const isForecastDate = computed(() => !!meta.value && !!date.value && date.value > meta.value.dates.max_date)
const isValidDate = computed(() => !!meta.value && date.value >= meta.value.dates.valid_start)
const dayLabel = computed(() => {
  if (!date.value) return ''
  const d = new Date(date.value + 'T00:00:00')
  const name = `${'일월화수목금토'[d.getDay()]}요일`
  const type = timeseries.value?.daytype
  if (type === 'weekday') return `${name} · 평일`
  if (type === 'holiday' && d.getDay() !== 0) return `${name} · 공휴일`
  return name
})
const dayFlag = computed(() => {
  if (!date.value || !meta.value) return ''
  const kind = isForecastDate.value ? '향후 예측 · 실측 없음' : isValidDate.value ? '검증 구간 · 실측 비교' : '학습 구간 · 실측 비교'
  return (date.value === today ? '오늘 · ' : '') + kind
})

const routeRows = computed(() =>
  (meta.value?.routes || []).map((r) => {
    const o = overview.value?.routes.find((x) => x.route === r.route) || {}
    return { ...r, level: o.level, max_congestion: o.max_congestion }
  }),
)
const hotspotCount = computed(() =>
  (overview.value?.routes || []).reduce((n, r) => n + r.segments.filter((s) => s.congestion >= 0.8).length, 0),
)
const metas = computed(() => ({
  routes: `${hour.value}시 출발 버스 · 최대 혼잡도`,
  map: `${hour.value}시 출발 버스 · 혼잡 구간 ${hotspotCount.value}곳`,
  forecast: `${selected.value}번 노선 전체`,
  stops: `${selected.value}번 ${hour.value}시 출발 버스`,
  status: `${date.value} ${hour.value}시`,
  advice: `${date.value} ${hour.value}시`,
  optimize: `${date.value} 전 노선`,
  whatif: `${selected.value}번 ${hour.value}시`,
  metrics: '검증 구간',
  backtest: '지난 기록으로 채점',
  drift: '예측 vs 나중 실측 · 결정은 담당자',
  traffic: 'TOPIS 실시간 · 배차는 바꾸지 않음',
}))

// 기본 날짜: 오늘(예측 범위 안이면), 아니면 실측 마지막 날 이전의 가장 최근 화요일
function defaultDate(maxDate) {
  if (isDayAvailable(today)) return today
  const d = new Date(maxDate + 'T00:00:00')
  while (d.getDay() !== 2 || missingDates.value.has(fmtDay(d))) d.setDate(d.getDate() - 1)
  return fmtDay(d)
}

// 노선·시간을 빠르게 바꾸면 응답 순서가 뒤바뀔 수 있어, 가장 마지막 요청의 응답만 반영
const latest = { overview: 0, timeseries: 0, stops: 0 }
async function fetchLatest(key, target, request, onError) {
  const id = ++latest[key]
  try {
    const data = await request()
    if (id === latest[key]) target.value = data
  } catch (e) {
    if (id === latest[key]) {
      target.value = null
      onError?.(e)
    }
  }
}
function loadOverview() {
  if (!date.value) return
  fetchLatest('overview', overview, () => api.overview(date.value, hour.value), (e) => message.warning(errorMessage(e)))
}
function loadTimeseries() {
  if (!date.value || !selected.value) return
  fetchLatest('timeseries', timeseries, () => api.timeseries(selected.value, date.value), (e) => message.warning(errorMessage(e)))
}
function loadStops() {
  if (!date.value || !selected.value) return
  fetchLatest('stops', stops, () => api.stops(selected.value, date.value, hour.value))
}

function togglePlay() {
  playing.value = !playing.value
  if (playing.value) {
    timer = setInterval(() => {
      hour.value = hour.value >= 23 ? 4 : hour.value + 1
    }, 1800)
  } else {
    clearInterval(timer)
  }
}

watch([date, hour], loadOverview)
watch([date, hour, selected], loadStops)
watch([date, selected], loadTimeseries)

onMounted(async () => {
  try {
    const [ds, h] = await Promise.all([api.domains(), api.health()])
    domains.value = ds
    health.value = h
    meta.value = await api.busMeta()
    const geos = await Promise.all(meta.value.routes.map((r) => api.geometry(r.route).catch(() => null)))
    geometries.value = Object.fromEntries(geos.filter(Boolean).map((g) => [g.route, g]))
    selected.value = meta.value.routes[0]?.route
    date.value = defaultDate(meta.value.dates.max_date)
  } catch (e) {
    loadError.value = errorMessage(e)
  }
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.document-role { display: flex; align-items: center; gap: 6px; font-size: 12px; }
/* 헤더 막대는 화면 테마와 상관없이 항상 어두움 → 선택칸도 헤더 버튼(.bbtn)과 같은 어두운 바탕·밝은 글자 */
.document-role select { font-size: 12px; color: #e4e6e1; background: #15181a; border: 1px solid #4c5358; border-radius: 2px; padding: 4px 8px; min-height: 30px; }
.document-role select:hover { border-color: #9aa09e; }
.document-role select:focus-visible { outline: 2px solid #ff8a4c; outline-offset: 1px; }
.document-role option { color: #e4e6e1; background: #15181a; }
.app { min-height: 100vh; display: flex; flex-direction: column; background: var(--ground); }
.bar { background: #15181a; color: #f1f1ec; display: flex; flex-wrap: wrap; align-items: stretch; gap: 0 28px; padding: 0 16px; }
.brand { display: flex; align-items: center; gap: 10px; padding: 9px 0; }
.brand h1 { margin: 0; font-size: 15px; font-weight: 700; letter-spacing: -0.01em; white-space: nowrap; }
.brand p { margin: 0; font-size: 11px; color: #b7bbb6; white-space: nowrap; }
.tabs { display: flex; align-items: stretch; flex-wrap: wrap; }
.tab { padding: 0 14px; min-height: 44px; font-size: 13px; color: #c5c8c3; border-bottom: 3px solid transparent; border-top: 3px solid transparent; white-space: nowrap; }
.tab:hover { color: #fff; }
.tab.on { color: #fff; font-weight: 600; border-bottom-color: #ff8a4c; }
.tab small { font-size: 10px; margin-left: 6px; color: #a6aaa5; }
.bar-right { margin-left: auto; display: flex; align-items: center; gap: 8px; padding: 6px 0; flex-wrap: wrap; }
/* 포인트색: 오른쪽 위 버튼 줄(야간 화면 옆)의 동그라미 하나 → 누르면 팔레트. 동그라미는 지금 테마에서 실제로 칠해지는 색 */
.accent-pick { display: flex; align-items: center; }
.accent-btn { display: inline-flex; align-items: center; gap: 8px; min-height: 30px; padding: 2px 4px 2px 8px; border-radius: 999px; border: 1px solid transparent; }
.accent-btn:hover { border-color: var(--line); }
.accent-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.accent-label { font-size: 12px; color: var(--ink2); white-space: nowrap; }
.swatch { width: 22px; height: 22px; border-radius: 50%; background: var(--sw); border: 1px solid var(--line);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.35); padding: 0; cursor: pointer; flex: none; }
.accent-pop { width: 232px; font-size: 12px; }
.ap-title { margin: 0 0 8px; font-weight: 600; }
.ap-grid { display: grid; grid-template-columns: repeat(6, 26px); gap: 10px; margin-bottom: 10px; }
.ap-grid .swatch { width: 26px; height: 26px; }
.ap-grid .swatch:hover { transform: scale(1.08); }
.ap-grid .swatch[aria-checked='true'] { outline: 2px solid var(--ink); outline-offset: 2px; }
.ap-grid .swatch:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ap-custom { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.ap-custom input { width: 34px; height: 26px; padding: 0; border: 1px solid var(--line); border-radius: 6px; background: none; cursor: pointer; }
.ap-now { margin: 8px 0 0; color: var(--ink2); }
.ap-warn { margin: 4px 0 0; color: var(--c3); }
.ap-note { margin: 6px 0 0; font-size: 11px; color: var(--ink3); line-height: 1.45; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.bstat { font-size: 11px; color: #c5c8c3; white-space: nowrap; }
.bbtn { font-size: 12px; color: #e4e6e1; border: 1px solid #4c5358; padding: 4px 10px; border-radius: 2px; min-height: 30px; white-space: nowrap; }
.bbtn:hover { border-color: #9aa09e; }
.settings { display: grid; grid-template-columns: auto auto; align-items: center; gap: 8px 10px; font-size: 12px; }

.tools { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; background: var(--panel); border-bottom: 1px solid var(--line); padding: 8px 16px; }
.date { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.dlab { white-space: nowrap; margin-left: 4px; }
.flag { font-size: 11px; color: var(--ink2); border: 1px solid var(--line); padding: 1px 6px; border-radius: 2px; white-space: nowrap; }
.flag.fc { border-color: var(--accent); color: var(--accent); }
.fresh { white-space: nowrap; }
.time { display: flex; align-items: center; gap: 8px; flex: 1 1 220px; min-width: 200px; max-width: 460px; }
.hr { font-size: 20px; font-weight: 600; width: 52px; text-align: right; }
.lbl { font-size: 11px; color: var(--ink3); white-space: nowrap; }
.ibtn { width: 30px; height: 30px; display: inline-grid; place-items: center; border: 1px solid var(--line); border-radius: 2px; flex: none; }
.ibtn:hover { background: var(--panel2); }
.ibtn svg { width: 14px; height: 14px; }
.tbtn { font-size: 12px; border: 1px solid var(--line); padding: 4px 10px; border-radius: 2px; min-height: 30px; white-space: nowrap; }
.tbtn:hover { background: var(--panel2); }
.ibtn:disabled, .tbtn:disabled { opacity: 0.4; cursor: not-allowed; background: none; }
.seg-g { display: flex; margin-left: auto; align-items: center; gap: 6px; flex-wrap: wrap; }
.segb { display: inline-flex; border: 1px solid var(--line); border-radius: 2px; overflow: hidden; }
.segb button { padding: 4px 10px; font-size: 12px; border-right: 1px solid var(--line); min-height: 28px; white-space: nowrap; }
.segb button:last-child { border-right: 0; }
.segb button:hover { background: var(--panel2); }
.segb button.on { background: var(--ink); color: var(--panel); font-weight: 600; }
.segb button.mine { color: var(--accent); }
.segb button.mine.on { color: var(--panel); }
.tbtn.add { font-size: 15px; line-height: 1; padding: 4px 9px; }
.tab-menu { display: grid; gap: 8px; width: min(320px, 80vw); font-size: 12px; }
.tm-h { margin: 0; font-weight: 600; }
.tm-name { display: grid; gap: 3px; }
.tm-name input { font: inherit; border: 1px solid var(--line); border-radius: 2px; padding: 5px 8px; background: var(--panel); color: var(--ink); min-height: 30px; }
.tm-name input:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.tm-panels { border: 1px solid var(--line); border-radius: 2px; margin: 0; padding: 6px 8px; display: grid; grid-template-columns: 1fr 1fr; gap: 2px 10px; }
.tm-panels legend { padding: 0 4px; color: var(--ink3); }
.tm-panels legend small { font-size: 10px; }
.tm-panels label { display: flex; align-items: center; gap: 5px; min-height: 26px; }
.tm-err { margin: 0; color: var(--c3); }
/* 공통 .tbtn을 쓰지 않음: 디자인 시안의 .tbtn 규칙(바탕을 패널색으로)이 덮어써 글자가 바탕과 같은 색이 됐음 */
.tm-save { justify-self: start; font-size: 12px; padding: 4px 14px; min-height: 30px; border: 1px solid var(--ink); border-radius: 4px; background: var(--ink); color: var(--panel); }
.tm-save:hover:not(:disabled) { background: var(--ink2); border-color: var(--ink2); }
.tm-save:disabled { opacity: 0.45; cursor: not-allowed; }
.tm-save:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.tm-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
.tm-list li { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tm-list li { gap: 6px; }
.tm-list li.cur .tm-tname { color: var(--accent); }
.tm-tname { font-weight: 500; margin-right: auto; }
.tm-order { display: inline-flex; flex-direction: column; }
.tm-arrow { font-size: 9px; line-height: 1; padding: 1px 4px; color: var(--ink2); }
.tm-arrow:disabled { opacity: 0.3; cursor: not-allowed; }
.tm-rename { font: inherit; flex: 1; min-width: 0; border: 1px solid var(--accent); border-radius: 2px; padding: 3px 6px; background: var(--panel); color: var(--ink); }
.tm-link { font-size: 11px; color: var(--ink2); text-decoration: underline; min-height: 26px; }
.tm-link.del { color: var(--c3); }
.tm-note { margin: 0; color: var(--ink3); font-size: 11px; }
.panel-menu { display: flex; flex-direction: column; min-width: 150px; }
.panel-menu label { display: flex; align-items: center; gap: 8px; padding: 5px 4px; font-size: 13px; cursor: pointer; }
.panel-menu input { accent-color: var(--accent); width: 15px; height: 15px; margin: 0; }
.excl { flex-basis: 100%; }
.loading { flex: 1; display: grid; place-items: center; padding: 40px; }

.routes { margin: -8px -10px; }
.rt { display: grid; grid-template-columns: auto 1fr auto auto; gap: 0 10px; align-items: center; width: 100%; text-align: left; padding: 7px 6px; border-bottom: 1px solid var(--line); }
.rt:last-child { border-bottom: 0; }
.rt:hover { background: var(--panel2); }
.rt.on { background: var(--panel2); font-weight: 600; }
.rt .rno { font-size: 15px; }
.ell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.v { text-align: right; font-size: 14px; font-weight: 500; }
.lv { display: flex; align-items: center; gap: 5px; font-size: 12px; width: 62px; }

@media (max-width: 820px) {
  .bar-right { margin-left: 0; }
  .accent-btn { min-height: 44px; }
  .ap-grid { grid-template-columns: repeat(6, 30px); }
  .ap-grid .swatch { width: 30px; height: 30px; }
  .seg-g { margin-left: 0; }
  .ibtn, .tbtn, .bbtn, .segb button, .document-role select { min-height: 44px; min-width: 44px; }
  .tm-link, .tm-panels label, .tm-name input, .tm-rename, .tm-save { min-height: 44px; }
  .tm-arrow { min-height: 22px; min-width: 44px; }
  .rt { padding: 12px 8px; }
}
</style>
