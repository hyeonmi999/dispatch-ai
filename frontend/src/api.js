// 백엔드 API 호출 (Vite 프록시: /api → http://127.0.0.1:8000)
import axios from 'axios'
import { palette } from './theme'

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
  adviseAll: (body) => http.post('/bus/advise/all', body).then((r) => r.data),
  advisoryBatches: (domain) => http.get('/advisories/batches', { params: { domain, limit: 30 } }).then((r) => r.data),
  advisoryBatch: (id) => http.get(`/advisories/batches/${id}`).then((r) => r.data),
  advisory: (id) => http.get(`/advisories/${id}`).then((r) => r.data),
  advisories: (domain) => http.get('/advisories', { params: { domain, limit: 20 } }).then((r) => r.data),
  review: (id, body) => http.post(`/advisories/${id}/review`, body).then((r) => r.data),
}

// 혼잡도 단계 색 (색 + 라벨을 항상 함께 표시). 차트·지도는 levelColor, 화면 요소는 levelClass
const LEVEL_INDEX = { 여유: 0, 보통: 1, 혼잡: 2, 매우혼잡: 3 }
const levelIndex = (level) => LEVEL_INDEX[level] ?? 4
export const levelColor = (level) => palette.value[`c${levelIndex(level)}`]
export const levelClass = (level) => `b${levelIndex(level)}`

// 혼잡도 표시: 반올림하면 0.798이 "0.80 보통"처럼 단계와 어긋나 보여서 소수 둘째 자리에서 버림
export const fmtCongestion = (v) => (v == null ? '-' : (Math.floor(v * 100) / 100).toFixed(2))
