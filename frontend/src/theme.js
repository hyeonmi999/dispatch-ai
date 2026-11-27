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
    if (q === 'a' || q === 'b') return q
    return localStorage.getItem(DESIGN_KEY) === 'b' ? 'b' : 'a'
  } catch {
    return 'a'
  }
}
export const design = ref(loadDesign())
export const isNight = computed(() => theme.value === 'night')

watchEffect(() => {
  const root = document.documentElement.classList
  root.toggle('night', theme.value === 'night')
  root.toggle('day', theme.value !== 'night')
  root.toggle('skin-b', design.value === 'b')
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
  design.value = design.value === 'b' ? 'a' : 'b'
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
  accentHover: '#3B82F6', accentPressed: '#1D4ED8', baseline: '#0E7490', c0: '#16A34A', c1: '#EAB308', c2: '#F97316', c3: '#DC2626', c4: '#CBD5E1',
}
const NIGHT_B = {
  panel: '#111A2E', panel2: '#1A2540', line: '#2A3654', ink: '#E6EDF7', ink2: '#B4C0D4', ink3: '#93A1B8', accent: '#60A5FA',
  accentHover: '#93C5FD', accentPressed: '#3B82F6', baseline: '#22D3EE', c0: '#34D399', c1: '#FBBF24', c2: '#FB923C', c3: '#F87171', c4: '#475569',
}
export const palette = computed(() => {
  const night = theme.value === 'night'
  return design.value === 'b' ? (night ? NIGHT_B : DAY_B) : night ? NIGHT : DAY
})

export const FONT_SANS = "'IBM Plex Sans KR', -apple-system, 'Apple SD Gothic Neo', sans-serif"
export const FONT_MONO = "'IBM Plex Mono', ui-monospace, monospace"
