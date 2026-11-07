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
export const isNight = computed(() => theme.value === 'night')

watchEffect(() => {
  const root = document.documentElement.classList
  root.toggle('night', theme.value === 'night')
  root.toggle('day', theme.value !== 'night')
  try {
    localStorage.setItem(KEY, theme.value)
  } catch {
    /* 저장소를 못 써도 현재 화면에는 적용됨 */
  }
})

export function toggleTheme() {
  theme.value = theme.value === 'night' ? 'day' : 'night'
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
export const palette = computed(() => (theme.value === 'night' ? NIGHT : DAY))

export const FONT_SANS = "'IBM Plex Sans KR', -apple-system, 'Apple SD Gothic Neo', sans-serif"
export const FONT_MONO = "'IBM Plex Mono', ui-monospace, monospace"
