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

// 디자인 시안 비교용: 'a' = 기존, 'b' = 새 시안. 주소에 ?design=a|b 를 붙이면 그 창에서만 그 디자인으로 열림
// (두 창을 나란히 놓고 비교할 수 있게 저장하지 않음). 버튼으로 바꾼 값은 저장함
const DESIGN_KEY = 'dispatch-design'
function loadDesign() {
  try {
    const q = new URLSearchParams(window.location.search).get('design')
    if (['a', 'b', 'c'].includes(q)) return q
    const saved = localStorage.getItem(DESIGN_KEY)
    return ['b', 'c'].includes(saved) ? saved : 'a'
  } catch {
    return 'a'
  }
}
export const design = ref(loadDesign())

// 무채색 시안(c)의 포인트색 후보 (팀 비교용). 쓰임별로 나눔:
//   accent = 글자·테두리 (흰 바탕 대비 4.5:1 이상), line = 그래프 선 (3:1 이상), fill = 눌린 버튼·선택 탭 바탕, on = fill 위 글자
//   파스텔 색은 흰 바탕에서 글자로 쓰면 안 보여서(1.5~2.2:1) 낮에는 같은 색상의 진한 색을 글자·선에 씀. 야간에는 파스텔 그대로
//   grayBaseline: 포인트색이 보라 계열이면 '작년 같은 요일' 선을 회색 점선으로 (보라 점선과 겹치지 않게)
const solid = (accent, hover, pressed, on) => ({ accent, hover, pressed, fill: accent, on, line: accent })
const pastel = (fill, text, line, hover) => ({ accent: text, hover, pressed: text, fill, on: '#18181B', line })
const pastelNight = (fill) => ({ accent: fill, hover: fill, pressed: fill, fill, on: '#0A0A0A', line: fill })
export const ACCENTS = {
  blue: { label: '파랑', day: solid('#2563EB', '#3B82F6', '#1D4ED8', '#FFFFFF'), night: solid('#60A5FA', '#93C5FD', '#3B82F6', '#0A0A0A') },
  indigo: { label: '인디고', day: solid('#4F46E5', '#6366F1', '#4338CA', '#FFFFFF'), night: solid('#818CF8', '#A5B4FC', '#6366F1', '#0A0A0A'), grayBaseline: true },
  olive: { label: '올리브 #637545', day: solid('#637545', '#738653', '#4F5E37', '#FFFFFF'), night: solid('#9DB07D', '#B0C192', '#87996A', '#0A0A0A') },
  green: { label: '연두 #B7D894', day: pastel('#B7D894', '#518020', '#66A128', '#66A128'), night: pastelNight('#B7D894') },
  beige: { label: '베이지 #DED6C1', day: pastel('#DED6C1', '#87723A', '#A98E49', '#A98E49'), night: pastelNight('#DED6C1') },
  taupe: { label: '토프 #B7B09E', day: pastel('#B7B09E', '#7F7456', '#9E9170', '#9E9170'), night: pastelNight('#B7B09E') },
  mist: { label: '하늘회색 #AEC4D4', day: pastel('#AEC4D4', '#457AA1', '#6597BD', '#6597BD'), night: pastelNight('#AEC4D4') },
}
const ACCENT_KEY = 'dispatch-accent'
function loadAccent() {
  try {
    const q = new URLSearchParams(window.location.search).get('accent')
    if (q && ACCENTS[q]) return q
    const saved = localStorage.getItem(ACCENT_KEY)
    return ACCENTS[saved] ? saved : 'blue'
  } catch {
    return 'blue'
  }
}
export const accent = ref(loadAccent())
export function setAccent(name) {
  if (!ACCENTS[name]) return
  accent.value = name
  try {
    localStorage.setItem(ACCENT_KEY, name)
  } catch {
    /* 저장소를 못 써도 현재 화면에는 적용됨 */
  }
}
export const isNight = computed(() => theme.value === 'night')

watchEffect(() => {
  const root = document.documentElement.classList
  root.toggle('night', theme.value === 'night')
  root.toggle('day', theme.value !== 'night')
  root.toggle('skin-b', design.value !== 'a')          // c는 b의 모양을 그대로 쓰고 바탕색만 바꿈
  root.toggle('skin-c', design.value === 'c')
  // 포인트색은 CSS 변수로 바로 덮어씀 (c에서만)
  const style = document.documentElement.style
  const a = ACCENTS[accent.value]?.[theme.value === 'night' ? 'night' : 'day']
  for (const [name, key] of [['--accent', 'accent'], ['--accent-fill', 'fill'], ['--on-accent', 'on']]) {
    if (design.value === 'c' && a) style.setProperty(name, a[key])
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

export function toggleDesign() {
  design.value = { a: 'b', b: 'c', c: 'a' }[design.value] ?? 'a'     // 기존 → 새 시안 → 새 시안(무채색 바탕)
  try {
    localStorage.setItem(DESIGN_KEY, design.value)
  } catch {
    /* 저장소를 못 써도 현재 화면에는 적용됨 */
  }
}

// style.css의 .day / .night 변수와 같은 값
const DAY = {
  panel: '#F8F8F4', panel2: '#ECEDE7', line: '#BFC2BA', ink: '#15181A', ink2: '#464B4B', ink3: '#5D6362', accent: '#B93A08',
  accentHover: '#CC4A16', accentPressed: '#962E05', baseline: '#1668A8', c0: '#6C9DB3', c1: '#D79A12', c2: '#E2600F', c3: '#8E1616', c4: '#B9BBB4',
}
const NIGHT = {
  panel: '#14181B', panel2: '#1E2428', line: '#333B41', ink: '#ECEEE9', ink2: '#B7BDBB', ink3: '#969D9B', accent: '#FF8A4C',
  accentHover: '#FF9F6B', accentPressed: '#E97434', baseline: '#6FC0F2', c0: '#7FB4CC', c1: '#E9B233', c2: '#F2752B', c3: '#E5534B', c4: '#4C5358',
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
// 새 시안 c: b와 같고 바탕·선·글자만 무채색 (낮은 흰색, 야간은 남색 대신 검정)
const DAY_C = { ...DAY_B, panel: '#FFFFFF', panel2: '#F4F4F5', line: '#E4E4E7', ink: '#18181B', ink2: '#3F3F46', ink3: '#63636B' }
const NIGHT_C = { ...NIGHT_B, panel: '#141414', panel2: '#1F1F1F', line: '#333333', ink: '#EDEDED', ink2: '#BDBDBD', ink3: '#9A9A9A' }
export const palette = computed(() => {
  const night = theme.value === 'night'
  if (design.value === 'c') {
    const base = night ? NIGHT_C : DAY_C
    const pick = ACCENTS[accent.value] ?? ACCENTS.blue
    const a = pick[night ? 'night' : 'day']
    const baseline = pick.grayBaseline ? (night ? '#A1A1AA' : '#71717A') : base.baseline
    return { ...base, accent: a.accent, accentHover: a.hover, accentPressed: a.pressed, accentLine: a.line, baseline }
  }
  return design.value === 'b' ? (night ? NIGHT_B : DAY_B) : night ? NIGHT : DAY
})

export const FONT_SANS = "'IBM Plex Sans KR', -apple-system, 'Apple SD Gothic Neo', sans-serif"
export const FONT_MONO = "'IBM Plex Mono', ui-monospace, monospace"
