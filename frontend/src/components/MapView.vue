<template>
  <div class="mapwrap">
    <div ref="el" class="map"></div>
    <!-- 실시간 표시 켜고 끄기 (실시간 돌발·정체 자료가 있을 때만) -->
    <div v-if="traffic" class="layers">
      <div class="lt">실시간 표시</div>
      <label title="노선 선 아래 띠: 평소 같은 시간 대비 도로 속도"><input v-model="layers.band" type="checkbox" /><i class="band-g" aria-hidden="true"></i>도로 교통</label>
      <label><input v-model="layers.acc" type="checkbox" /><span class="acc-g" aria-hidden="true" v-html="ICON.event"></span>돌발<span class="n">{{ incidents.length }}</span></label>
    </div>
  </div>
  <!-- 범례: 1줄 승객 혼잡, 2줄 도로 교통·돌발 (실시간 표시가 켜졌을 때만) -->
  <div class="legend">
    <div class="lg-row">
      <span class="lg-t">승객 혼잡</span>
      <span><i class="chip b0"></i>여유 ~0.45</span>
      <span><i class="chip b1"></i>보통 ~0.80</span>
      <span><i class="chip b2"></i>혼잡 ~1.00</span>
      <span><i class="chip b3"></i>매우혼잡 1.00~</span>
      <span><i class="lg-dot"></i>혼잡 지점 0.8~</span>
    </div>
    <div v-if="bandShown || accShown" class="lg-row">
      <template v-if="bandShown">
        <span class="lg-t" title="노선 선 아래 띠: 평소 같은 시간 대비 도로 속도">도로 교통</span>
        <span><i class="tband-chip t1"></i>조금 느림 <small>85%↓</small></span>
        <span><i class="tband-chip t2"></i>느림 <small>⅔↓</small></span>
        <span><i class="tband-chip t3"></i>매우 느림 <small>절반↓</small></span>
        <span><small>띠 없음 평소대로</small></span>
      </template>
      <span v-if="bandShown && accShown" class="lg-sep" aria-hidden="true"></span>
      <span v-if="accShown" title="숫자 = 걸친 노선 수"><span class="acc-g" aria-hidden="true" v-html="ICON.event"></span>돌발</span>
    </div>
  </div>
</template>

<script setup>
// 서울 지도 위 노선도: 정류장 구간마다 혼잡도 색, 혼잡 구간은 점 깜빡임
// 실시간 돌발·정체(이슈 #42 방법 2): 노선 선 아래 도로 교통 띠(평소 대비 속도), 경로 위 돌발 표식
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import L from 'leaflet'
import { fmtCongestion, levelColor } from '../api'
import { busSvg } from '../busIcon'
import { routeShape } from '../routeShape'
import { isNight, palette } from '../theme'

const props = defineProps({
  geometries: { type: Object, default: () => ({}) },
  overview: { type: Object, default: null },
  selected: { type: String, default: null },
  // 길안내 팝업(GuideModal)에서 고른 경로: { options, index, user: {lat, lng} | null }
  guide: { type: Object, default: null },
  // 실시간 돌발·정체 응답 (GET /api/bus/traffic/alerts). TrafficPanel이 받아서 넘김. 없으면 지도는 혼잡도만
  traffic: { type: Object, default: null },
  // 패널에서 "지도에서 보기"를 누른 돌발: { id, n } (n은 같은 돌발을 다시 눌러도 반응하게)
  focusIncident: { type: Object, default: null },
})
const emit = defineEmits(['select', 'select-incident'])

const el = ref(null)
let map = null
let routeLayer = null
let bandLayer = null
let hotspotLayer = null
let accLayer = null
let tagLayer = null
let guideLayer = null
let bandRenderer = null
let fitted = false

// ---- 굵기: 노선 선은 가장 얇게, 굵기는 확대 13 기준 두 단계만 (확대·축소 때 선이 출렁이지 않게) ----
const CASING = '#18181B' // 노선 테두리는 주간·야간 모두 검정 (야간 밝은 테두리는 노랑 띠 위에서 노선이 묻힘)
const CASING_W = 5
const LINE_W = 3
const near = () => map && map.getZoom() >= 13
const bandWeight = () => (near() ? 12 : 9)
// 다른 노선이 골라져 있으면 흐리게 (고른 노선은 굵게 하지 않음 → 띠가 가려지지 않음)
const DIM = { casing: 0.4, line: 0.55, band: 0.45 }

// ---- 도로 교통 띠 색: 주간·야간 같은 색. 노랑 → 주황 → 빨강 순으로 밝기도 달라 겹쳐도 구분됨 ----
const BAND = {
  1: { fill: '#FACC15', edge: '#CA8A04', name: '조금 느림' }, // 평소 속도의 85% 아래
  2: { fill: '#F97316', edge: '#C2410C', name: '느림' }, // ⅔ 아래
  3: { fill: '#B91C1C', edge: '#7F1D1D', name: '매우 느림' }, // 절반 아래
}
const speedTier = (r) => (r >= 0.85 ? 0 : r >= 0.67 ? 1 : r >= 0.5 ? 2 : 3)

// ---- 실시간 표시 켜고 끄기 (이 브라우저에 저장) ----
const LAYER_KEY = 'dispatch-traffic-layers'
function loadLayers() {
  try {
    return { band: true, acc: true, ...JSON.parse(localStorage.getItem(LAYER_KEY) || '{}') }
  } catch {
    return { band: true, acc: true }
  }
}
const layers = reactive(loadLayers())
watch(layers, (v) => {
  try {
    localStorage.setItem(LAYER_KEY, JSON.stringify(v))
  } catch {
    /* 저장 못 해도 화면은 그대로 */
  }
})

// 노선별 정류장 구간 → 지금 속도 ÷ 평소 속도
const speeds = computed(() => new Map((props.traffic?.routes ?? []).map((r) => [r.route, new Map((r.segments ?? []).map((s) => [s.seq, s.ratio]))])))
const bandShown = computed(() => !!props.traffic && layers.band && [...speeds.value.values()].some((m) => m.size))
const accShown = computed(() => !!props.traffic && layers.acc && incidents.value.length > 0)

// 같은 돌발이 여러 노선 경로에 걸치므로 돌발 기준으로 묶음
const incidents = computed(() => {
  const byId = new Map()
  for (const r of props.traffic?.routes ?? []) {
    for (const i of r.incidents ?? []) {
      if (!byId.has(i.acc_id)) byId.set(i.acc_id, { ...i, hits: [] })
      byId.get(i.acc_id).hits.push({ route: r.route, stop: i.near_stop, m: i.distance_m })
    }
  }
  return [...byId.values()]
})

// 돌발 종류별 그림 (사고·고장 / 공사 / 행사·통제)
const ICON = {
  crash: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2.5 20h19z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.4" r=".6" fill="currentColor"/></svg>',
  work: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 4h5l4 15h-13z"/><path d="M7.6 11.5h8.8M6.3 16h11.4"/><path d="M3 20h18"/></svg>',
  event: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="7" width="19" height="6" rx="1"/><path d="M7 7l-3 6M13 7l-3 6M19 7l-3 6"/><path d="M5 13v7M19 13v7"/></svg>',
}
// A01 교통사고 · A02 차량고장 · A03 보행사고 / A06·A10 행사·집회 / 그 밖(A04 공사 등)은 공사 그림
const kindOf = (t) => (['A01', 'A02', 'A03'].includes(t) ? 'crash' : ['A06', 'A10'].includes(t) ? 'event' : 'work')

// 노선별 상·하행 구조와 다듬은 선은 좌표가 바뀔 때만 다시 계산
const shapes = new Map()
function shapeOf(route) {
  const geo = props.geometries[route]
  if (shapes.get(route)?.geo !== geo) shapes.set(route, { geo, shape: routeShape(geo), lines: null })
  return shapes.get(route).shape
}

// ---- 노선 모양 다듬기: GPS 흔들림(작은 지그재그) 제거 → 직선은 곧게, 꺾이는 곳만 작게 둥글게 ----
// 노선 전체를 한 번에 다듬고(정류장 이음매에서 꺾이지 않게), 원래 길이 비율대로 정류장 구간으로 다시 자름
const M_LAT = 110540
const M_LNG = 111320 * Math.cos((37.55 * Math.PI) / 180)
const toM = ([a, b]) => [b * M_LNG, a * M_LAT]
const toLL = ([x, y]) => [y / M_LAT, x / M_LNG]
const lenOf = (pts) => {
  let d = 0
  for (let k = 1; k < pts.length; k++) d += Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1])
  return d
}
function simplify(pts, tol) {
  // Douglas–Peucker (미터)
  if (pts.length < 3) return pts
  const keep = new Uint8Array(pts.length)
  keep[0] = keep[pts.length - 1] = 1
  const stack = [[0, pts.length - 1]]
  while (stack.length) {
    const [i, j] = stack.pop()
    const [ax, ay] = pts[i]
    const dx = pts[j][0] - ax
    const dy = pts[j][1] - ay
    const l2 = dx * dx + dy * dy || 1
    let best = -1
    let bd = tol
    for (let k = i + 1; k < j; k++) {
      const t = Math.max(0, Math.min(1, ((pts[k][0] - ax) * dx + (pts[k][1] - ay) * dy) / l2))
      const d = Math.hypot(pts[k][0] - ax - t * dx, pts[k][1] - ay - t * dy)
      if (d > bd) {
        bd = d
        best = k
      }
    }
    if (best > 0) {
      keep[best] = 1
      stack.push([i, best], [best, j])
    }
  }
  return pts.filter((_, k) => keep[k])
}
function roundCorners(pts, r, n = 3) {
  // 꼭짓점마다 최대 r미터만 깎는 Chaikin (긴 직선은 그대로)
  for (let it = 0; it < n && pts.length >= 3; it++) {
    const out = [pts[0]]
    for (let k = 0; k < pts.length - 1; k++) {
      const [ax, ay] = pts[k]
      const [bx, by] = pts[k + 1]
      const f = Math.min(0.25, r / (Math.hypot(bx - ax, by - ay) || 1))
      if (k > 0) out.push([ax + (bx - ax) * f, ay + (by - ay) * f])
      if (k < pts.length - 2) out.push([bx - (bx - ax) * f, by - (by - ay) * f])
    }
    out.push(pts[pts.length - 1])
    pts = out
    r /= 2
  }
  return pts
}
function linesOf(route) {
  const shape = shapeOf(route)
  const entry = shapes.get(route)
  if (entry.lines) return entry.lines
  const parts = shape.outbound.map((o) => o.coords.map(toM))
  const full = []
  for (const pts of parts) {
    for (const p of pts) {
      const q = full[full.length - 1]
      if (!q || q[0] !== p[0] || q[1] !== p[1]) full.push(p)
    }
  }
  const sm = roundCorners(simplify(full, 10), 20)
  const cum = [0]
  for (let k = 1; k < sm.length; k++) cum.push(cum[k - 1] + Math.hypot(sm[k][0] - sm[k - 1][0], sm[k][1] - sm[k - 1][1]))
  const scale = cum[cum.length - 1] / (lenOf(full) || 1)
  const at = (d) => {
    let k = 1
    while (k < sm.length - 1 && cum[k] < d) k++
    const t = Math.max(0, Math.min(1, (d - cum[k - 1]) / (cum[k] - cum[k - 1] || 1)))
    return [sm[k - 1][0] + (sm[k][0] - sm[k - 1][0]) * t, sm[k - 1][1] + (sm[k][1] - sm[k - 1][1]) * t]
  }
  let acc = 0
  let from = 0
  entry.lines = parts.map((pts) => {
    const to = (acc += lenOf(pts)) * scale
    const piece = [at(from)]
    for (let k = 0; k < sm.length; k++) if (cum[k] > from && cum[k] < to) piece.push(sm[k])
    piece.push(at(to))
    from = to
    return piece.map(toLL)
  })
  return entry.lines
}

function segmentLookup(route) {
  const r = props.overview?.routes.find((x) => x.route === route)
  return new Map((r?.segments || []).map((s) => [s.seq, s]))
}

// 툴팁은 HTML 문자열로 들어가므로, 서버에서 온 이름은 글자 그대로 보이게 바꿔서 넣음
const escapeHtml = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
const pct = (x) => (x == null ? '자료 없음' : `평소의 ${Math.round(x * 100)}% 속도`)

function draw() {
  if (!map) return
  routeLayer.clearLayers()
  bandLayer.clearLayers()
  hotspotLayer.clearLayers()
  const bounds = []
  const band = bandShown.value
  const round = { lineCap: 'round', lineJoin: 'round', smoothFactor: 0 }

  // 선택 노선을 마지막에 그려 위로 올림
  const routes = Object.keys(props.geometries).sort((a, b) => (a === props.selected) - (b === props.selected))
  for (const route of routes) {
    const geo = props.geometries[route]
    const active = route === props.selected
    const dim = !!props.selected && !active
    const segs = segmentLookup(route)
    const shape = shapeOf(route)
    const lines = linesOf(route)
    const names = new Map(geo.stops.map((s) => [s.seq, s.name]))
    const sp = speeds.value.get(route)

    // 상행·하행 구간 혼잡도와 소통을 지도에 그리는 상행 구간 하나로 모음
    const merged = new Map(shape.outbound.map((o) => [o.seq, { up: null, down: null, upSpeed: null, downSpeed: null }]))
    for (const seg of geo.segments) {
      const target = merged.get(shape.mapTo.get(seg.seq))
      if (!target) continue
      const side = seg.seq < shape.turn ? 'up' : 'down'
      const s = segs.get(seg.seq)
      if (s && s.congestion != null && (!target[side] || s.congestion > target[side].congestion)) target[side] = s
      const v = sp?.get(seg.seq)
      const key = `${side}Speed`
      if (v != null && (target[key] == null || v < target[key])) target[key] = v
    }
    const worstSpeed = (m) => [m.upSpeed, m.downSpeed].filter((x) => x != null).sort((a, b) => a - b)[0]

    // 도로 교통 띠: 노선 선과 같은 좌표로 그 아래에. 같은 단계가 이어지는 구간은 한 선으로 (이음매 없이)
    if (band && sp?.size) {
      let run = null
      const flush = () => {
        if (!run) return
        const c = BAND[run.tier]
        const opacity = dim ? DIM.band : 1
        L.polyline(run.pts, { pane: 'trafficBand', renderer: bandRenderer, color: c.edge, weight: bandWeight() + 2, opacity, interactive: false, ...round }).addTo(bandLayer)
        L.polyline(run.pts, { pane: 'trafficBand', renderer: bandRenderer, color: c.fill, weight: bandWeight(), opacity, interactive: false, ...round }).addTo(bandLayer)
        run = null
      }
      shape.outbound.forEach((seg, i) => {
        const w = worstSpeed(merged.get(seg.seq))
        const tier = w == null ? 0 : speedTier(w)
        if (!tier) return flush()
        if (run && run.tier !== tier) flush()
        if (!run) run = { tier, pts: [] }
        run.pts.push(...lines[i])
      })
      flush()
    }

    // 바탕 경로 (테두리) - 상행 경로 하나만
    const outPath = lines.flat()
    L.polyline(outPath, { color: CASING, weight: CASING_W, opacity: dim ? DIM.casing : active ? 1 : 0.7, interactive: false, ...round }).addTo(routeLayer)

    shape.outbound.forEach((seg, i) => {
      const m = merged.get(seg.seq)
      const worst = [m.up, m.down].filter(Boolean).sort((a, b) => b.congestion - a.congestion)[0]
      const level = worst?.level || '정보없음'
      const line = L.polyline(lines[i], {
        color: levelColor(level),
        weight: LINE_W,
        opacity: dim ? DIM.line : 1, // 반투명이면 검은 테두리가 비쳐 혼잡도 색이 탁해짐
        ...round,
      })
      const fmt = (x) => (x ? `${fmtCongestion(x.congestion)} (${x.level})` : '-')
      const speedRows = band && sp?.size
        ? `<br/><span class="tt-sub">도로 교통 (평소 같은 시간 대비 속도)</span><br/>→ ${escapeHtml(shape.directions.up || '상행')}: ${pct(m.upSpeed)}<br/>→ ${escapeHtml(shape.directions.down || '하행')}: ${pct(m.downSpeed)}`
        : ''
      line.bindTooltip(
        `<b>${route}번</b> ${escapeHtml(names.get(seg.seq) || '')} 부근<br/>` +
          `→ ${escapeHtml(shape.directions.up || '상행')}: ${fmt(m.up)}<br/>` +
          `→ ${escapeHtml(shape.directions.down || '하행')}: ${fmt(m.down)}` +
          speedRows,
        { sticky: true },
      )
      line.on('click', () => emit('select', route))
      line.addTo(routeLayer)
    })
    outPath.forEach((p) => bounds.push(p))

    if (active) {
      for (const st of geo.stops.filter((s) => s.seq <= shape.turn)) {
        L.circleMarker([st.lat, st.lng], { radius: 3.5, color: palette.value.ink2, weight: 1.5, fillColor: palette.value.panel, fillOpacity: 1 })
          .bindTooltip(`${escapeHtml(st.name)} (${st.seq})`)
          .addTo(routeLayer)
      }
    }
  }

  // 혼잡 지점(혼잡도 0.8 이상) 깜빡임 - 하행 지점도 지도에 그린 상행 선 위로 옮김
  for (const r of props.overview?.routes || []) {
    const geo = props.geometries[r.route]
    if (!geo) continue
    const shape = shapeOf(r.route)
    const lines = linesOf(r.route)
    const drawn = new Map(shape.outbound.map((o, i) => [o.seq, lines[i]]))
    for (const h of r.hotspots) {
      const seg = drawn.get(shape.mapTo.get(h.seq))
      const pos = seg ? seg[Math.floor(seg.length / 2)] : h.lat != null ? [h.lat, h.lng] : null
      if (!pos) continue
      L.marker(pos, {
        icon: L.divIcon({ className: '', html: '<div class="hotspot-pulse"></div>', iconSize: [16, 16], iconAnchor: [8, 8] }),
        zIndexOffset: 1000,
      })
        .bindTooltip(`<b>${r.route}번 혼잡</b><br/>${escapeHtml(h.name)}<br/>혼잡도 ${fmtCongestion(h.congestion)}`)
        .on('click', () => emit('select', r.route))
        .addTo(hotspotLayer)
    }
  }

  routeBounds = bounds
  if (!fitted && bounds.length) {
    fitRoutes()
    fitted = true
  }
  placeTags()
}

// ---- 돌발 표식 ----
const pad = (n) => String(n).padStart(2, '0')
const dayOf = (s) => (s ? s.slice(0, 10) : '')
function when(s) {
  if (!s) return '-'
  const d = new Date(s)
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return dayOf(s) === dayOf(props.traffic?.fetched_at) ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`
}
const cleanInfo = (s) => String(s || '').replace(/^\[[^\]]+\]\s*/, '')

function popupHtml(i) {
  const hits = i.hits
    .map((h) => `<li><span class="rno r${escapeHtml(h.route)}">${escapeHtml(h.route)}</span><span>${h.stop ? `${h.stop.seq}번째 ${escapeHtml(h.stop.name)}` : '경로 위'} <span class="sm">· ${h.m ?? '-'}m</span></span></li>`)
    .join('')
  return `<div class="acc-pop"><h3>${escapeHtml(i.dtype_name || i.type_name)} <span class="sm">· ${i.hits.length}개 노선 경로</span></h3>
    <div>${escapeHtml(cleanInfo(i.info))}</div>
    <div class="when">${when(i.start)} 시작 → <b>${when(i.expected_clear)}</b> 해제 예정</div>
    <ul>${hits}</ul>
    <p class="sm">배차는 바꾸지 않았습니다. 담당자 검토</p></div>`
}

const accMarkers = new Map()
let accOn = null
function drawIncidents() {
  if (!map) return
  accLayer.clearLayers()
  accMarkers.clear()
  if (!props.traffic || !layers.acc) return
  for (const i of incidents.value) {
    const m = L.marker([i.lat, i.lng], {
      pane: 'trafficAcc',
      zIndexOffset: 2000,
      keyboard: true,
      title: `${i.dtype_name || i.type_name} 돌발`,
      icon: L.divIcon({
        className: '',
        iconSize: [34, 40],
        iconAnchor: [17, 41],
        popupAnchor: [0, -40],
        html: `<div class="acc ${accOn === i.acc_id ? 'on' : ''}"><span class="pin">${ICON[kindOf(i.type)]}</span>${i.hits.length > 1 ? `<span class="cnt">${i.hits.length}</span>` : ''}</div>`,
      }),
    })
      .bindPopup(popupHtml(i), { maxWidth: 320, autoPanPadding: [40, 40], className: 'acc-popup' })
      .on('click', () => {
        markIncident(i.acc_id)
        emit('select-incident', i.acc_id)
      })
    m.addTo(accLayer)
    accMarkers.set(i.acc_id, m)
  }
}
function markIncident(id) {
  accOn = id
  accMarkers.forEach((mk, key) => mk.getElement()?.querySelector('.acc')?.classList.toggle('on', key === id))
}

// 패널에서 "지도에서 보기": 그 자리로 옮기고 팝업. 이미 그 자리면 바로 팝업
const FOCUS_ZOOM = 13.5
function focusIncidentOnMap() {
  if (props.focusIncident?.fromMap) return // 지도에서 표식을 누른 것: 이미 팝업이 열림
  const id = props.focusIncident?.id
  const i = incidents.value.find((x) => x.acc_id === id)
  if (!map || !i) return
  if (!layers.acc) layers.acc = true
  drawIncidents()
  markIncident(id)
  const open = () => accMarkers.get(id)?.openPopup()
  const here = map.getCenter().distanceTo([i.lat, i.lng]) < 30 && Math.abs(map.getZoom() - FOCUS_ZOOM) < 0.01
  userMoved = true
  if (here) return open()
  // 움직임 줄이기 설정이면 바로 옮김. 애니메이션이 끝나지 않으면(백그라운드 탭 등) 1초 뒤 바로 옮겨서 팝업
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    map.setView([i.lat, i.lng], FOCUS_ZOOM, { animate: false })
    return open()
  }
  let done = false
  const finish = () => {
    if (done) return
    done = true
    clearTimeout(timer)
    open()
  }
  const timer = setTimeout(() => {
    map.off('moveend', finish)
    map.setView([i.lat, i.lng], FOCUS_ZOOM, { animate: false })
    finish()
  }, 1000)
  map.once('moveend', finish)
  map.flyTo([i.lat, i.lng], FOCUS_ZOOM, { duration: 0.6 })
}

// 노선 전체가 패널에 꽉 차게 맞춤. 사용자가 직접 확대·이동한 뒤에는 그 화면을 유지
let routeBounds = []
let fitting = false
let userMoved = false
function fitRoutes() {
  if (!routeBounds.length) return
  fitting = true
  map.fitBounds(routeBounds, { padding: [34, 34], animate: false })
  fitting = false
}

// 노선 번호표: 처음 맞춘 화면에서 다른 노선·번호표와 가장 떨어진 지점을 노선별 기본 자리로 정함
// 확대·축소·이동해도 기본 자리에 고정. 화면 밖으로 나가거나 다른 번호표와 겹칠 때만 그 번호표를 옮기고,
// 다시 들어갈 수 있게 되면 기본 자리로 돌아옴
const TAG_W = 60
const TAG_H = 24
const tagHomes = new Map()
const tagSpots = new Map()

function samplePath(route) {
  const coords = linesOf(route).flat()
  const step = Math.max(1, Math.floor(coords.length / 80))
  return coords.filter((_, i) => i % step === 0)
}

function placeTags() {
  if (!map || !tagLayer) return
  tagLayer.clearLayers()
  const size = map.getSize()

  const routes = Object.keys(props.geometries).sort()
  const paths = new Map(routes.map((r) => [r, samplePath(r).map((ll) => ({ ll, p: map.latLngToContainerPoint(ll) }))]))

  // 지도 안쪽, 확대 버튼·출처 표기·실시간 표시 상자를 가리지 않는 자리만 후보
  const box = props.traffic ? { w: 140, h: 70 } : { w: 0, h: 0 }
  const usable = (p) =>
    p.x > TAG_W / 2 + 4 && p.x < size.x - TAG_W / 2 - 4 && p.y > TAG_H / 2 + 4 && p.y < size.y - TAG_H / 2 - 24 && !(p.x < 60 && p.y < 90) &&
    !(p.x > size.x - box.w && p.y < box.h)
  // 새 자리를 고를 때는 여유를 두고, 이미 있는 자리는 실제로 겹칠 때만 옮김 (자주 움직이지 않게)
  const clashes = (a, b, gap = 6) => Math.abs(a.x - b.x) < TAG_W + gap && Math.abs(a.y - b.y) < TAG_H + gap
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y)
  const stay = (ll, placedSoFar) => {
    if (!ll) return null
    const p = map.latLngToContainerPoint(ll)
    return usable(p) && !placedSoFar.some((q) => clashes(p, q, 0)) ? { ll, p } : null
  }

  const placed = []
  for (const route of routes) {
    const candidates = paths.get(route).filter((c) => usable(c.p))
    if (!candidates.length) continue // 이 노선이 화면 밖

    let spot = stay(tagHomes.get(route), placed) || stay(tagSpots.get(route), placed)

    if (!spot) {
      const others = routes.filter((r) => r !== route).flatMap((r) => paths.get(r))
      let best = -Infinity
      for (const c of candidates) {
        const fromLines = Math.min(60, ...others.map((o) => dist(c.p, o.p)))
        const fromTags = Math.min(160, ...placed.map((q) => dist(c.p, q)))
        const score = fromLines * 2 + fromTags - (placed.some((q) => clashes(c.p, q)) ? 10000 : 0)
        if (score > best) {
          best = score
          spot = c
        }
      }
    }

    if (!tagHomes.has(route)) tagHomes.set(route, spot.ll)
    tagSpots.set(route, spot.ll)
    placed.push(spot.p)
    const active = route === props.selected
    L.marker(spot.ll, {
      icon: L.divIcon({
        className: '',
        html: `<div class="rno route-tag r${route} ${active ? 'on' : ''}">${busSvg(14)}${route}</div>`,
        iconSize: [TAG_W, TAG_H],
        iconAnchor: [TAG_W / 2, TAG_H / 2],
      }),
      zIndexOffset: active ? 900 : 800,
    })
      .on('click', () => emit('select', route))
      .addTo(tagLayer)
  }
}

// 길안내 경로: 타는 구간을 점선으로, 승차·환승·도착 지점과 현재 위치를 표시
function drawGuide() {
  if (!map || !guideLayer) return
  guideLayer.clearLayers()
  const option = props.guide?.options?.[props.guide?.index ?? 0]
  if (!option) return
  const points = []
  const pin = (lat, lng, kind, html, tip) =>
    L.marker([lat, lng], {
      icon: L.divIcon({ className: '', html: `<div class="gpin ${kind}">${html}</div>`, iconSize: [28, 28], iconAnchor: [14, 14] }),
      zIndexOffset: 1200,
    })
      .bindTooltip(tip)
      .addTo(guideLayer)

  const user = props.guide.user
  if (user) {
    pin(user.lat, user.lng, 'me', '', '현재 위치')
    points.push([user.lat, user.lng])
  }
  const legs = option.legs ?? []
  legs.forEach((leg, i) => {
    // 서버가 준 정류장 좌표를 쓰고(전체 노선), 없으면 화면이 가진 노선 좌표에서 뽑음
    const stops = props.geometries[leg.route]?.stops ?? []
    const line = leg.path?.length
      ? leg.path
      : stops.filter((s) => s.seq >= leg.board.seq && s.seq <= leg.alight.seq).map((s) => [s.lat, s.lng])
    if (line.length > 1) {
      L.polyline(line, { color: palette.value.panel, weight: 9, opacity: 0.95, smoothFactor: 0 }).addTo(guideLayer)
      L.polyline(line, { color: palette.value.accent, weight: 5, opacity: 1, dashArray: '1 9', lineCap: 'round', smoothFactor: 0 }).addTo(guideLayer)
    }
    const last = i === legs.length - 1
    pin(leg.board.lat, leg.board.lng, 'board', busSvg(18), `<b>${escapeHtml(leg.route)}번 승차</b><br/>${escapeHtml(leg.board.name)}`)
    pin(leg.alight.lat, leg.alight.lng, last ? 'end' : 'xfer', last ? '도착' : '환승', `<b>${last ? '도착' : `${escapeHtml(leg.route)}번 하차 · 환승`}</b><br/>${escapeHtml(leg.alight.name)}`)
    points.push(...line, [leg.board.lat, leg.board.lng], [leg.alight.lat, leg.alight.lng])
  })
  if (points.length) {
    fitting = true
    map.fitBounds(points, { padding: [44, 44], animate: false, maxZoom: 15 })
    fitting = false
    userMoved = true // 길안내 화면을 유지 (패널 크기를 바꿔도 전체 노선으로 되돌리지 않음)
  }
}

// 키 없이 쓸 수 있는 회색 배경지도 (노선 색이 잘 보이도록) - Esri Canvas, 야간은 어두운 지도
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas'
const tileUrl = (part) => `${ESRI}/World_${isNight.value ? 'Dark' : 'Light'}_Gray_${part}/MapServer/tile/{z}/{y}/{x}`
let baseTiles = null
let labelTiles = null
let resizeObserver = null

// 테마를 바꿀 때 setUrl은 쓰지 않음: 소수 확대 상태에서 재요청하면 10.5 같은 없는 확대값으로 타일을 요청함
function setTiles() {
  baseTiles?.remove()
  labelTiles?.remove()
  // 서울 기준 Esri 밝은 배경과 지명 층은 확대 13까지만 있고, 14부터는 "Map data not yet available" 이미지만 옴.
  // maxNativeZoom: 그 이상 확대하면 13 타일을 늘려서 표시 (어두운 배경은 16까지 있음)
  const LAST_REAL_ZOOM = 13
  baseTiles = L.tileLayer(tileUrl('Base'), {
    attribution: 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors',
    maxZoom: 16,
    maxNativeZoom: isNight.value ? 16 : LAST_REAL_ZOOM,
  }).addTo(map)
  labelTiles = L.tileLayer(tileUrl('Reference'), { maxZoom: 16, maxNativeZoom: LAST_REAL_ZOOM }).addTo(map)
}

let wasNear = null
onMounted(() => {
  // zoomSnap을 잘게 해야 정수 확대에 묶이지 않고 패널에 꽉 차게 맞음
  map = L.map(el.value, { zoomControl: true, preferCanvas: false, zoomSnap: 0.25, zoomDelta: 1 }).setView([37.5665, 126.978], 12)
  map.on('dragstart zoomstart', () => {
    if (!fitting) userMoved = true
  })
  // 도로 교통 띠는 노선 선(overlayPane 400) 아래, 돌발 표식은 번호표·혼잡 점 위
  map.createPane('trafficBand').style.zIndex = 395
  map.createPane('trafficAcc').style.zIndex = 640
  bandRenderer = L.svg({ pane: 'trafficBand' })
  setTiles()
  bandLayer = L.layerGroup().addTo(map)
  routeLayer = L.layerGroup().addTo(map)
  hotspotLayer = L.layerGroup().addTo(map)
  tagLayer = L.layerGroup().addTo(map)
  accLayer = L.layerGroup().addTo(map)
  guideLayer = L.layerGroup().addTo(map)
  map.on('zoomend moveend resize', placeTags)
  // 띠 굵기는 확대 13을 넘나들 때만 바뀜 → 그때만 다시 그림
  wasNear = near()
  map.on('zoomend', () => {
    if (near() !== wasNear) {
      wasNear = near()
      draw()
    }
  })
  draw()
  drawIncidents()
  drawGuide()
  // 패널 크기를 바꾸면 지도도 다시 맞춤
  resizeObserver = new ResizeObserver(() => {
    if (!map) return
    map.invalidateSize({ animate: false })
    if (!userMoved) fitRoutes()
  })
  resizeObserver.observe(el.value)
})

watch(() => [props.geometries, props.overview, props.selected, props.traffic, layers.band], draw)
watch(() => [props.traffic, layers.acc], drawIncidents)
watch(() => props.focusIncident, focusIncidentOnMap)
watch(() => props.guide, drawGuide)
watch(isNight, () => {
  setTiles()
  draw()
  drawGuide()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  // 확대 애니메이션 중에 패널을 닫아도 번호표 계산이 호출되지 않게 먼저 해제
  map?.off('zoomend moveend resize', placeTags)
  map?.remove()
  map = null
})
</script>

<style>
.mapwrap { position: relative; flex: 1; min-height: 0; display: flex; }
.map { width: 100%; flex: 1; min-height: 0; }
/* 확대·축소 애니메이션 동안 선 굵기가 부풀었다 줄어들지 않게 */
.map .leaflet-pane path { vector-effect: non-scaling-stroke; }
.route-tag { width: 60px; height: 24px; font-size: 13px; line-height: 20px; padding: 0; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 3px; color: var(--rc, var(--ink)); }
.route-tag.on { color: var(--on); }
/* 길안내 지점 표시 */
.gpin { width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; font-size: 10px; font-weight: 700; border: 2px solid var(--panel); background: var(--ink); color: var(--panel); box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35); }
.gpin.board { background: var(--accent); color: var(--on); }
.gpin.xfer { background: var(--panel); color: var(--ink); border-color: var(--ink); }
.gpin.me { width: 16px; height: 16px; margin: 6px; background: var(--baseline, #1668a8); border: 3px solid var(--panel); }
.legend { display: flex; flex-direction: column; gap: 1px; padding: 3px 8px; border-top: 1px solid var(--line); font-size: 10px; line-height: 1.35; color: var(--ink2); flex: none; }
.lg-row { display: flex; flex-wrap: wrap; gap: 1px 10px; align-items: center; }
.legend span { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
.legend small { color: var(--ink3); font-size: 9px; }
.legend .lg-t { font-weight: 600; color: var(--ink); }
.legend .lg-sep { width: 1px; height: 10px; background: var(--line); }
.lg-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--c3); }
/* 도로 교통 띠 견본: 주간·야간 같은 색 (MapView BAND와 같은 값) */
.tband-chip { display: inline-block; width: 16px; height: 8px; border-radius: 4px; }
.tband-chip.t1 { background: #facc15; box-shadow: inset 0 0 0 1px #ca8a04; }
.tband-chip.t2 { background: #f97316; box-shadow: inset 0 0 0 1px #c2410c; }
.tband-chip.t3 { background: #b91c1c; box-shadow: inset 0 0 0 1px #7f1d1d; }
.band-g { display: inline-block; width: 16px; height: 8px; border-radius: 4px; background: linear-gradient(90deg, #facc15 0 33%, #f97316 33% 66%, #b91c1c 66%); position: relative; flex: none; }
.band-g::after { content: ''; position: absolute; left: 0; right: 0; top: 3px; height: 2px; background: var(--c0); }
.acc-g { width: 14px; height: 14px; border-radius: 4px; background: var(--ink); color: var(--panel); display: inline-grid; place-items: center; flex: none; }
.acc-g svg { width: 10px; height: 10px; }
.legend .chip { width: 8px; height: 8px; }
/* 실시간 표시 상자 */
.layers { position: absolute; top: 8px; right: 8px; z-index: 800; display: flex; flex-direction: column; gap: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 3px 4px; box-shadow: var(--shadow, 0 2px 8px rgba(0, 0, 0, 0.15)); }
.layers .lt { font-size: 10px; color: var(--ink3); padding: 1px 4px 0; }
.layers label { display: flex; align-items: center; gap: 6px; font-size: 11px; padding: 2px 4px; border-radius: 5px; cursor: pointer; white-space: nowrap; }
.layers label:hover { background: var(--panel2); }
.layers input { accent-color: var(--accent); margin: 0; width: 13px; height: 13px; }
.layers .n { margin-left: auto; padding-left: 6px; font-family: var(--mono); color: var(--ink3); font-size: 10px; }
@media (max-width: 820px) { .layers label { min-height: 40px; } }
/* 돌발 표식 */
.acc { position: relative; width: 34px; height: 40px; }
.acc .pin { position: absolute; left: 0; top: 0; width: 34px; height: 34px; border-radius: 10px; background: var(--ink); color: var(--panel); display: grid; place-items: center; border: 2px solid var(--panel); box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35); }
.acc .pin::after { content: ''; position: absolute; left: 50%; bottom: -7px; width: 10px; height: 10px; background: var(--ink); border-right: 2px solid var(--panel); border-bottom: 2px solid var(--panel); transform: translateX(-50%) rotate(45deg); z-index: -1; }
.acc .cnt { position: absolute; right: -8px; top: -7px; min-width: 18px; height: 18px; border-radius: 9px; padding: 0 4px; background: var(--accent-fill, var(--accent)); color: var(--on-accent, var(--on)); font: 600 11px/14px var(--mono); text-align: center; border: 2px solid var(--panel); }
.acc.on .pin { outline: 3px solid var(--accent); outline-offset: 1px; }
.acc-popup .leaflet-popup-content-wrapper, .acc-popup .leaflet-popup-tip { background: var(--panel); color: var(--ink); }
.acc-popup .leaflet-popup-content { margin: 12px 14px; width: 280px !important; font-size: 12px; line-height: 1.5; }
.acc-pop h3 { margin: 0 0 2px; font-size: 13px; }
.acc-pop .when { color: var(--ink2); }
.acc-pop ul { list-style: none; margin: 8px 0 0; padding: 8px 0 0; border-top: 1px solid var(--line); display: grid; gap: 4px; }
.acc-pop li { display: flex; gap: 8px; align-items: center; }
.acc-pop .rno { min-width: 38px; height: 20px; font-size: 11px; display: inline-flex; align-items: center; justify-content: center; }
.acc-pop p { margin: 6px 0 0; }
.tt-sub { color: var(--ink3); font-size: 11px; }
</style>
