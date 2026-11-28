// 주간·야간 테마: <html> 클래스(CSS 변수)와 차트·지도용 색 값을 함께 관리
import { computed, ref, watchEffect } from 'vue'

const KEY = 'dispatch-theme'

function load() {
  try {
    return localStorage.getItem(KEY) === 'night' ? 'night' : 'day'
  } catch {
    return 'day'
  }
}

export const theme = ref(load())

// 디자인은 무채색 시안(c)으로 통일 (이슈 #38, 2026-11-28 팀 결정). 바탕·글자는 style.css의 .skin-b·.skin-c, 포인트색은 사용자가 고름

// 포인트색 (사용자가 야간 화면 버튼 옆 동그라미 → 팔레트에서 고름, 이 브라우저에 저장). 쓰임별로 나눔:
//   accent = 글자·테두리 (바탕 대비 4.5:1 이상), line = 그래프 선, fill = 눌린 버튼·선택 탭 바탕, on = fill 위 글자
//   색마다 낮·야간을 따로 맞춤: 연한 색은 낮 글자에 진한 색을, 진한 색은 야간에 밝힌 색을 씀.
//   그래프 선은 '실제' 선(낮 검정·야간 흰색)과 헷갈리지 않게 낮에는 흰 바탕 대비 3~4.5:1, 야간에는 검정 바탕 대비 5~8:1
//   grayBaseline: 포인트색이 보라에 가까우면 '작년 같은 요일'(보라 점선) 선을 회색 점선으로
const tone = (fill, on, accent, line, hover = line) => ({ fill, on, accent, line, hover, pressed: accent })

// ---- 아무 색이나 받아 낮·야간 쓰임별 색을 만듦 (직접 고른 색, 추가 후보) ----
const DAY_GROUND = '#F2F2F3'
const NIGHT_PANEL2 = '#1F1F1F'
const NIGHT_PANEL = '#141414'
const hexToRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
const lum = (h) => {
  const [r, g, b] = hexToRgb(h).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
export const contrast = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m)
  return (x + 0.05) / (y + 0.05)
}
function toHsl(h) {
  const [r, g, b] = hexToRgb(h)
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  const hue = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4
  return [hue * 60, s, l]
}
function fromHsl(hue, s, l) {
  const k = (n) => (n + hue / 30) % 12
  const a = s * Math.min(l, 1 - l)
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))
  return '#' + [f(0), f(8), f(4)].map((v) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, '0')).join('').toUpperCase()
}
// 밝기(L)만 조금씩 바꿔 조건을 맞춤. dir -1 = 어둡게, +1 = 밝게
function shift(hex, dir, ok) {
  const [h, s, l0] = toHsl(hex)
  for (let l = l0; l >= 0 && l <= 1; l += dir * 0.01) {
    const c = fromHsl(h, s, l)
    if (ok(c)) return c
  }
  return dir < 0 ? '#000000' : '#FFFFFF'
}
const CONGESTION_HUES = [[0, 60], [335, 360]]          // 혼잡 단계 색(빨강·주황·노랑)과 겹치는 색상
export function deriveAccent(hex, label) {
  hex = hex.toUpperCase()
  const [hue, sat] = toHsl(hex)
  const dayAccent = contrast(hex, DAY_GROUND) >= 4.6 ? hex : shift(hex, -1, (c) => contrast(c, DAY_GROUND) >= 4.6)
  let dayLine = hex
  if (contrast(hex, '#FFFFFF') < 3) dayLine = shift(hex, -1, (c) => contrast(c, '#FFFFFF') >= 3.1)
  else if (contrast(hex, '#FFFFFF') > 4.6) dayLine = shift(hex, 1, (c) => contrast(c, '#FFFFFF') <= 4.5)
  // 눌린 버튼 바탕: 그 위 글자(검정·흰색 중 대비가 큰 쪽)가 4.5:1이 안 되면 조금 어둡게
  const onBest = (c) => Math.max(contrast(c, '#18181B'), contrast(c, '#FFFFFF'))
  const dayFill = onBest(hex) >= 4.5 ? hex : shift(hex, -1, (c) => onBest(c) >= 4.5)
  const dayOn = contrast(dayFill, '#18181B') >= contrast(dayFill, '#FFFFFF') ? '#18181B' : '#FFFFFF'
  const nightAccent = contrast(hex, NIGHT_PANEL2) >= 5 ? hex : shift(hex, 1, (c) => contrast(c, NIGHT_PANEL2) >= 5)
  let nightLine = contrast(hex, NIGHT_PANEL) >= 5 ? hex : shift(hex, 1, (c) => contrast(c, NIGHT_PANEL) >= 5)
  if (contrast(nightLine, NIGHT_PANEL) > 8.5) nightLine = shift(nightLine, -1, (c) => contrast(c, NIGHT_PANEL) <= 8)
  const warn = sat > 0.35 && CONGESTION_HUES.some(([a, b]) => hue >= a && hue <= b)
    ? '혼잡 단계 색(빨강·주황·노랑)과 비슷해 혼잡도 표시와 헷갈릴 수 있어요' : ''
  return {
    label: label ?? `직접 고른 색 ${hex}`,
    day: tone(dayFill, dayOn, dayAccent, dayLine),
    night: tone(nightAccent, '#0A0A0A', nightAccent, nightLine),
    grayBaseline: sat > 0.25 && hue >= 245 && hue <= 320,
    warn,
  }
}

// 미리 정한 후보. 앞의 넷은 팀 비교(이슈 #38) 때 낮·야간을 하나씩 맞춘 값, 나머지는 위 계산으로 만듦.
// 혼잡 단계 색(빨강·주황·노랑)과 겹치는 색은 후보에서 뺌
export const ACCENTS = {
  green: { label: '연두 #B7D894', day: tone('#B7D894', '#18181B', '#4C771E', '#66A128'), night: tone('#B7D894', '#0A0A0A', '#B7D894', '#7DB442') },
  teal: { label: '청록 #34908B', day: tone('#34908B', '#18181B', '#2B7773', '#34908B'), night: tone('#40B1AB', '#0A0A0A', '#40B1AB', '#35938E') },
  forest: { label: '숲색 #2B5748', day: tone('#2B5748', '#FFFFFF', '#2B5748', '#448A72'), night: tone('#5CAF92', '#0A0A0A', '#5CAF92', '#49937A') },
  navy: { label: '남색 #121358', day: tone('#121358', '#FFFFFF', '#121358', '#6D6FE1'), night: tone('#9899EA', '#0A0A0A', '#9899EA', '#797BE4'), grayBaseline: true },
  blue: deriveAccent('#2563EB', '파랑 #2563EB'),
  sky: deriveAccent('#0284C7', '하늘 #0284C7'),
  indigo: deriveAccent('#4F46E5', '인디고 #4F46E5'),
  violet: deriveAccent('#7C3AED', '보라 #7C3AED'),
  plum: deriveAccent('#A21CAF', '자주 #A21CAF'),
  darkblue: deriveAccent('#1E3A8A', '감청 #1E3A8A'),
  slate: deriveAccent('#475569', '청회색 #475569'),
  graphite: deriveAccent('#3F3F46', '흑연 #3F3F46'),
}
const HEX_RE = /^#[0-9A-Fa-f]{6}$/
const isAccent = (v) => Boolean(ACCENTS[v]) || HEX_RE.test(v ?? '')
export function accentOf(v) {
  return ACCENTS[v] ?? (HEX_RE.test(v ?? '') ? deriveAccent(v) : ACCENTS.green)
}
const ACCENT_KEY = 'dispatch-accent'
function loadAccent() {
  try {
    const q = new URLSearchParams(window.location.search).get('accent')
    if (isAccent(q)) return q
    const saved = localStorage.getItem(ACCENT_KEY)
    return isAccent(saved) ? saved : 'green'
  } catch {
    return 'green'
  }
}
// 값은 후보 이름(green 등) 또는 직접 고른 색 '#RRGGBB'
export const accent = ref(loadAccent())
export const accentInfo = computed(() => accentOf(accent.value))
export function setAccent(value) {
  if (!isAccent(value)) return
  accent.value = ACCENTS[value] ? value : value.toUpperCase()
  try {
    localStorage.setItem(ACCENT_KEY, accent.value)
  } catch {
    /* 저장소를 못 써도 현재 화면에는 적용됨 */
  }
}
export const isNight = computed(() => theme.value === 'night')

watchEffect(() => {
  const root = document.documentElement.classList
  root.toggle('night', theme.value === 'night')
  root.toggle('day', theme.value !== 'night')
  root.add('skin-b', 'skin-c')          // c는 b의 모양을 그대로 쓰고 바탕색만 무채색으로 바꿈
  // 포인트색은 CSS 변수로 바로 덮어씀
  const style = document.documentElement.style
  const a = accentInfo.value[theme.value === 'night' ? 'night' : 'day']
  for (const [name, key] of [['--accent', 'accent'], ['--accent-fill', 'fill'], ['--on-accent', 'on']]) {
    if (a) style.setProperty(name, a[key])
    else style.removeProperty(name)
  }
  try {
    localStorage.setItem(KEY, theme.value)
  } catch {
    /* 저장소를 못 써도 현재 화면에는 적용됨 */
  }
})

export function toggleTheme() {
  theme.value = theme.value === 'night' ? 'day' : 'night'
}

// 새 시안(style.css의 .skin-b 변수와 같은 값): 흰 바탕 + 교통 파랑, 혼잡 단계는 초록·노랑·주황·빨강
const DAY_B = {
  panel: '#FFFFFF', panel2: '#F1F5F9', line: '#DCE3EC', ink: '#0F172A', ink2: '#334155', ink3: '#5B6B82', accent: '#2563EB',
  accentHover: '#3B82F6', accentPressed: '#1D4ED8', baseline: '#9333EA', c0: '#16A34A', c1: '#EAB308', c2: '#F97316', c3: '#DC2626', c4: '#CBD5E1',
}
const NIGHT_B = {
  panel: '#111A2E', panel2: '#1A2540', line: '#2A3654', ink: '#E6EDF7', ink2: '#B4C0D4', ink3: '#93A1B8', accent: '#60A5FA',
  accentHover: '#93C5FD', accentPressed: '#3B82F6', baseline: '#C084FC', c0: '#34D399', c1: '#FBBF24', c2: '#FB923C', c3: '#F87171', c4: '#475569',
}
// 무채색 시안 c: b와 같고 바탕·선·글자만 무채색 (낮은 흰색, 야간은 남색 대신 검정)
const DAY_C = { ...DAY_B, panel: '#FFFFFF', panel2: '#F4F4F5', line: '#E4E4E7', ink: '#18181B', ink2: '#3F3F46', ink3: '#63636B' }
const NIGHT_C = { ...NIGHT_B, panel: '#141414', panel2: '#1F1F1F', line: '#333333', ink: '#EDEDED', ink2: '#BDBDBD', ink3: '#9A9A9A' }
export const palette = computed(() => {
  const night = theme.value === 'night'
  const base = night ? NIGHT_C : DAY_C
  const pick = accentInfo.value
  const a = pick[night ? 'night' : 'day']
  const baseline = pick.grayBaseline ? (night ? '#A1A1AA' : '#71717A') : base.baseline
  return { ...base, accent: a.accent, accentHover: a.hover, accentPressed: a.pressed, accentLine: a.line, baseline }
})

export const FONT_SANS = "'IBM Plex Sans KR', -apple-system, 'Apple SD Gothic Neo', sans-serif"
export const FONT_MONO = "'IBM Plex Mono', ui-monospace, monospace"
