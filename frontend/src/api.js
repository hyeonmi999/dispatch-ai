// 백엔드 API 호출 (Vite 프록시: /api → http://127.0.0.1:8000)
import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 180000 })

// 오류 응답의 detail을 사람이 읽을 수 있는 문장으로
export function errorMessage(err) {
  const d = err?.response?.data
  if (!d) return err?.message || '서버에 연결할 수 없습니다.'
  if (typeof d.detail === 'string') return d.detail
  if (Array.isArray(d.detail)) return d.detail.map((x) => x.msg).join(', ')
  return JSON.stringify(d)
}

export const api = {
  domains: () => http.get('/domains').then((r) => r.data),
  health: () => http.get('/health').then((r) => r.data),
  schema: (domain) => http.get(`/${domain}/schema`).then((r) => r.data),
  busMeta: () => http.get('/bus/meta').then((r) => r.data),
  geometry: (route) => http.get(`/bus/routes/${route}/geometry`).then((r) => r.data),
  timeseries: (route, date) => http.get(`/bus/routes/${route}/timeseries`, { params: { date } }).then((r) => r.data),
  stops: (route, date, hour) => http.get(`/bus/routes/${route}/stops`, { params: { date, hour } }).then((r) => r.data),
  overview: (date, hour) => http.get('/bus/overview', { params: { date, hour } }).then((r) => r.data),
  predict: (domain, body) => http.post(`/${domain}/predict`, body).then((r) => r.data),
  ask: (domain, body) => http.post(`/${domain}/ask`, body).then((r) => r.data),
  advise: (domain, body) => http.post(`/${domain}/advise`, body).then((r) => r.data),
  advisories: (domain) => http.get('/advisories', { params: { domain, limit: 20 } }).then((r) => r.data),
  review: (id, body) => http.post(`/advisories/${id}/review`, body).then((r) => r.data),
}

// 혼잡도 단계 색 (상태 팔레트: 색 + 라벨을 항상 함께 표시)
export const LEVEL_COLORS = {
  여유: '#0ca30c',
  보통: '#fab219',
  혼잡: '#ec835a',
  매우혼잡: '#d03b3b',
  정보없음: '#b5b3ab',
  운행없음: '#b5b3ab',
}
export const levelColor = (level) => LEVEL_COLORS[level] || '#b5b3ab'

export const ACTION_TYPES = {
  increase: 'error',
  increase_limited: 'warning',
  decrease: 'info',
  keep: 'success',
}
