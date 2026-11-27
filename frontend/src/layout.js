// 대시보드 패널 배치: 12열 격자, 겹치면 다른 패널을 비켜 놓고 위로 붙임
export const COLS = 12
export const ROW = 52
const KEY = 'dispatch-layout-v1'

export const TITLES = {
  routes: '노선 현황',
  map: '노선 지도',
  forecast: '시간대별 승차',
  stops: '정류장별 혼잡도',
  status: '배차 현황',
  advice: '배차 권고',
  optimize: '배차 최적화',
  whatif: '배차간격 시뮬레이션',
  qa: '규정 Q&A',
  chat: '배차 상담 챗봇',
  metrics: '모델 성능',
  history: '권고 이력',
  backtest: '배차 방식 비교',
  drift: '모델 점검',
  documents: '법규 문서 관리',
}

// 켤 때의 기본 크기 / 줄일 수 있는 최소 크기 [열, 행]
const DEFAULT_SIZE = {
  routes: [3, 7], map: [6, 9], forecast: [3, 6], stops: [3, 6], status: [3, 8], advice: [3, 15], qa: [4, 10], metrics: [4, 9], history: [3, 8],
  optimize: [6, 11], whatif: [3, 11], documents: [6, 12], chat: [4, 12], backtest: [6, 9], drift: [6, 9],
}
export const MIN_SIZE = {
  routes: [2, 4], map: [3, 5], forecast: [3, 5], stops: [3, 5], status: [2, 5], advice: [3, 5], qa: [3, 5], metrics: [3, 5], history: [2, 4],
  optimize: [4, 6], whatif: [3, 6], documents: [4, 6], chat: [3, 6], backtest: [4, 6], drift: [4, 6],
}

export const PRESETS = [
  { id: 'ops', name: '관제', panels: { routes: [0, 0, 3, 7], status: [0, 7, 3, 8], map: [3, 0, 6, 9], forecast: [3, 9, 3, 6], stops: [6, 9, 3, 6], advice: [9, 0, 3, 15] } },
  { id: 'plan', name: '배차 계획', panels: { optimize: [0, 0, 6, 12], whatif: [6, 0, 3, 12], routes: [9, 0, 3, 6], status: [9, 6, 3, 8] } },
  { id: 'ana', name: '분석', panels: { forecast: [0, 0, 6, 7], stops: [6, 0, 6, 7], metrics: [0, 7, 5, 9], routes: [5, 7, 4, 9], status: [9, 7, 3, 9], backtest: [0, 16, 6, 9], drift: [6, 16, 6, 9] } },
  { id: 'demo', name: '발표', panels: { map: [0, 0, 8, 15], status: [8, 0, 4, 6], metrics: [8, 6, 4, 9] } },
  { id: 'rule', name: '규정 검토', panels: { advice: [0, 0, 5, 15], qa: [5, 0, 4, 15], history: [9, 0, 3, 15] } },
  { id: 'chat', name: '상담', panels: { chat: [0, 0, 5, 15], map: [5, 0, 7, 9], forecast: [5, 9, 4, 6], status: [9, 9, 3, 6] } },
]

export function makeLayout(presetId) {
  const preset = PRESETS.find((p) => p.id === presetId) || PRESETS[0]
  const out = {}
  for (const id of Object.keys(TITLES)) {
    const q = preset.panels[id]
    out[id] = q
      ? { x: q[0], y: q[1], w: q[2], h: q[3], on: true }
      : { x: 0, y: 999, w: DEFAULT_SIZE[id][0], h: DEFAULT_SIZE[id][1], on: false }
  }
  return out
}

const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

// active 패널은 그 자리에 두고, 나머지는 위에서부터 빈자리에 채움
export function resolve(layout, active = null) {
  const out = {}
  for (const [id, it] of Object.entries(layout)) out[id] = { ...it }
  const rest = Object.keys(out)
    .filter((id) => out[id].on && id !== active)
    .sort((a, b) => out[a].y - out[b].y || out[a].x - out[b].x)
  const placed = active && out[active] ? [out[active]] : []
  for (const id of rest) {
    const it = out[id]
    let y = 0
    while (placed.some((p) => overlaps({ ...it, y }, p))) y += 1
    it.y = y
    placed.push(it)
  }
  return out
}

// 나중에 추가된 패널은 저장된 배치에 없으므로 꺼진 상태로 채움 (쓰던 배치는 그대로 유지)
export function fillLayout(saved) {
  const blank = makeLayout(null)
  const out = {}
  for (const id of Object.keys(TITLES)) out[id] = saved?.[id] ? { ...saved[id] } : { ...blank[id], y: 999, on: false }
  return out
}

export function loadSaved() {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || 'null')
    if (!s?.layout) return null
    s.layout = fillLayout(s.layout)
    return s
  } catch {
    /* 저장소를 못 쓰면 기본 배치 */
  }
  return null
}

export function saveLayout(layout, preset) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ layout, preset }))
  } catch {
    /* 저장소를 못 써도 현재 화면에는 적용됨 */
  }
}

// ==================================================
// 사용자가 만든 화면 탭 (Task P-6): 이 브라우저에만 저장 (개인 화면 설정이라 DB를 쓰지 않음)
// ==================================================
const CUSTOM_KEY = 'dispatch-custom-tabs-v1'
export const CUSTOM_TAB_MAX = 5
export const CUSTOM_TAB_NAME_MAX = 10

export function loadCustomTabs() {
  try {
    const list = JSON.parse(localStorage.getItem(CUSTOM_KEY) || '[]')
    if (!Array.isArray(list)) return []
    return list
      .filter((t) => t && typeof t.id === 'string' && typeof t.name === 'string' && t.layout)
      .slice(0, CUSTOM_TAB_MAX)
      .map((t) => ({ id: t.id, name: t.name.slice(0, CUSTOM_TAB_NAME_MAX), layout: fillLayout(t.layout) }))
  } catch {
    return []        // 저장소를 못 쓰면 사용자 탭 없이 시작
  }
}

// 저장에 실패하면 false (이번 화면에는 그대로 쓸 수 있음)
export function saveCustomTabs(list) {
  try {
    localStorage.setItem(CUSTOM_KEY, JSON.stringify(list))
    return true
  } catch {
    return false
  }
}

// 새로 켜는 패널의 자리: 위에서부터, 같은 줄이면 왼쪽부터 다른 켜진 패널과 겹치지 않는 첫 빈칸 (맨 아래에 쌓지 않음)
export function findSpot(layout, id) {
  const it = layout[id]
  const w = Math.min(it.w, COLS)
  const others = Object.entries(layout).filter(([k, v]) => k !== id && v.on).map(([, v]) => v)
  const bottom = others.reduce((m, o) => Math.max(m, o.y + o.h), 0)
  for (let y = 0; y <= bottom; y += 1) {
    for (let x = 0; x + w <= COLS; x += 1) {
      if (!others.some((o) => overlaps({ x, y, w, h: it.h }, o))) return { x, y, w }
    }
  }
  return { x: 0, y: bottom, w }
}

// 패널 하나를 켜서 빈칸에 넣은 배치
export function placePanel(layout, id) {
  const next = { ...layout, [id]: { ...layout[id], on: true } }
  next[id] = { ...next[id], ...findSpot(next, id) }
  return resolve(next, id)
}

// 지금 배치에서 고른 패널만 켠 배치: 이미 켜진 패널은 그 자리, 새로 고른 패널은 빈칸부터 채움
export function layoutWith(layout, panels) {
  const keep = new Set(panels)
  let next = {}
  for (const [id, it] of Object.entries(layout)) next[id] = { ...it, on: it.on && keep.has(id) }
  next = resolve(next)
  for (const id of panels) if (next[id] && !next[id].on) next = placePanel(next, id)
  return next
}
