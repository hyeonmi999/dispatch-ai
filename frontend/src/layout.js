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
  metrics: '모델 성능',
  history: '권고 이력',
}

// 켤 때의 기본 크기 / 줄일 수 있는 최소 크기 [열, 행]
const DEFAULT_SIZE = {
  routes: [3, 7], map: [6, 9], forecast: [3, 6], stops: [3, 6], status: [3, 8], advice: [3, 15], qa: [4, 10], metrics: [4, 9], history: [3, 8],
  optimize: [6, 11], whatif: [3, 11],
}
export const MIN_SIZE = {
  routes: [2, 4], map: [3, 5], forecast: [3, 5], stops: [3, 5], status: [2, 5], advice: [3, 5], qa: [3, 5], metrics: [3, 5], history: [2, 4],
  optimize: [4, 6], whatif: [3, 6],
}

export const PRESETS = [
  { id: 'ops', name: '관제', panels: { routes: [0, 0, 3, 7], status: [0, 7, 3, 8], map: [3, 0, 6, 9], forecast: [3, 9, 3, 6], stops: [6, 9, 3, 6], advice: [9, 0, 3, 15] } },
  { id: 'plan', name: '배차 계획', panels: { optimize: [0, 0, 6, 12], whatif: [6, 0, 3, 12], routes: [9, 0, 3, 6], status: [9, 6, 3, 8] } },
  { id: 'ana', name: '분석', panels: { forecast: [0, 0, 6, 7], stops: [6, 0, 6, 7], metrics: [0, 7, 5, 9], routes: [5, 7, 4, 9], status: [9, 7, 3, 9] } },
  { id: 'demo', name: '발표', panels: { map: [0, 0, 8, 15], status: [8, 0, 4, 6], metrics: [8, 6, 4, 9] } },
  { id: 'rule', name: '규정 검토', panels: { advice: [0, 0, 5, 15], qa: [5, 0, 4, 15], history: [9, 0, 3, 15] } },
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

export function loadSaved() {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || 'null')
    if (!s?.layout) return null
    // 나중에 추가된 패널은 저장된 배치에 없으므로 꺼진 상태로 채움 (쓰던 배치는 그대로 유지)
    const blank = makeLayout(null)
    for (const id of Object.keys(TITLES)) {
      if (!s.layout[id]) s.layout[id] = { ...blank[id], y: 999, on: false }
    }
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
