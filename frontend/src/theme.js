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
//   색마다 낮·야간을 따로 맞춤: 연한 색(연두)은 낮 글자에 진한 색을, 진한 색(숲색·남색)은 야간에 밝힌 색을 씀.
//   그래프 선은 '실제' 선(낮 검정·야간 흰색)과 헷갈리지 않게 낮에는 흰 바탕 대비 3~4:1의 밝은 쪽, 야간에는 검정 바탕 대비 5~7:1의 채도 높은 쪽을 씀
//   grayBaseline: 포인트색이 보라에 가까우면 '작년 같은 요일' 선을 회색 점선으로
const tone = (fill, on, accent, line, hover = line) => ({ fill, on, accent, line, hover, pressed: accent })
export const ACCENTS = {
  green: { label: '연두 #B7D894', day: tone('#B7D894', '#18181B', '#4C771E', '#66A128'), night: tone('#B7D894', '#0A0A0A', '#B7D894', '#7DB442') },
  teal: { label: '청록 #34908B', day: tone('#34908B', '#18181B', '#2B7773', '#34908B'), night: tone('#40B1AB', '#0A0A0A', '#40B1AB', '#35938E') },
  forest: { label: '숲색 #2B5748', day: tone('#2B5748', '#FFFFFF', '#2B5748', '#448A72'), night: tone('#5CAF92', '#0A0A0A', '#5CAF92', '#49937A') },
  navy: { label: '남색 #121358', day: tone('#121358', '#FFFFFF', '#121358', '#6D6FE1'), night: tone('#9899EA', '#0A0A0A', '#9899EA', '#797BE4'), grayBaseline: true },
}
const ACCENT_KEY = 'dispatch-accent'
function loadAccent() {
  try {
    const q = new URLSearchParams(window.location.search).get('accent')
    if (q && ACCENTS[q]) return q
    const saved = localStorage.getItem(ACCENT_KEY)
    return ACCENTS[saved] ? saved : 'green'
  } catch {
    return 'green'
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
    const pick = ACCENTS[accent.value] ?? ACCENTS.green
    const a = pick[night ? 'night' : 'day']
    const baseline = pick.grayBaseline ? (night ? '#A1A1AA' : '#71717A') : base.baseline
    return { ...base, accent: a.accent, accentHover: a.hover, accentPressed: a.pressed, accentLine: a.line, baseline }
  }
  return design.value === 'b' ? (night ? NIGHT_B : DAY_B) : night ? NIGHT : DAY
})

export const FONT_SANS = "'IBM Plex Sans KR', -apple-system, 'Apple SD Gothic Neo', sans-serif"
export const FONT_MONO = "'IBM Plex Mono', ui-monospace, monospace"
