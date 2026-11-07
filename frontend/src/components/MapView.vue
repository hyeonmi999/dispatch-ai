<template>
  <div ref="el" class="map"></div>
  <div class="legend">
    <span><i class="chip b0"></i>여유 ~0.45</span>
    <span><i class="chip b1"></i>보통 ~0.80</span>
    <span><i class="chip b2"></i>혼잡 ~1.00</span>
    <span><i class="chip b3"></i>매우혼잡 1.00~</span>
    <span><i class="lg-dot"></i>혼잡 지점 0.8~</span>
  </div>
</template>

<script setup>
// 서울 지도 위 노선도: 정류장 구간마다 혼잡도 색, 혼잡 구간은 점 깜빡임
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import { fmtCongestion, levelColor } from '../api'
import { routeShape } from '../routeShape'
import { isNight, palette } from '../theme'

const props = defineProps({
  geometries: { type: Object, default: () => ({}) },
  overview: { type: Object, default: null },
  selected: { type: String, default: null },
})
const emit = defineEmits(['select'])

const el = ref(null)
let map = null
let routeLayer = null
let hotspotLayer = null
let tagLayer = null
let fitted = false

// 노선별 상·하행 구조는 좌표가 바뀔 때만 다시 계산
const shapes = new Map()
function shapeOf(route) {
  const geo = props.geometries[route]
  if (shapes.get(route)?.geo !== geo) shapes.set(route, { geo, shape: routeShape(geo) })
  return shapes.get(route).shape
}

function segmentLookup(route) {
  const r = props.overview?.routes.find((x) => x.route === route)
  return new Map((r?.segments || []).map((s) => [s.seq, s]))
}

function draw() {
  if (!map) return
  routeLayer.clearLayers()
  hotspotLayer.clearLayers()
  const bounds = []

  // 선택 노선을 마지막에 그려 위로 올림
  const routes = Object.keys(props.geometries).sort((a, b) => (a === props.selected) - (b === props.selected))
  for (const route of routes) {
    const geo = props.geometries[route]
    const active = route === props.selected
    const segs = segmentLookup(route)
    const shape = shapeOf(route)
    const names = new Map(geo.stops.map((s) => [s.seq, s.name]))

    // 상행·하행 구간 혼잡도를 지도에 그리는 상행 구간 하나로 모음
    const merged = new Map(shape.outbound.map((o) => [o.seq, { up: null, down: null }]))
    for (const seg of geo.segments) {
      const target = merged.get(shape.mapTo.get(seg.seq))
      const s = segs.get(seg.seq)
      if (!target || !s || s.congestion == null) continue
      const side = seg.seq < shape.turn ? 'up' : 'down'
      if (!target[side] || s.congestion > target[side].congestion) target[side] = s
    }

    // 바탕 경로 (테두리 효과) - 상행 경로 하나만. 주간은 검은색, 야간은 어두운 지도에 묻히지 않게 밝은색
    // smoothFactor 0: Leaflet이 선마다 점을 따로 줄이면 테두리와 색선이 어긋나 테두리 두께가 들쭉날쭉해짐
    const outPath = shape.outbound.flatMap((o) => o.coords)
    L.polyline(outPath, { color: palette.value.ink, weight: active ? 11 : 7, opacity: active ? 1 : 0.6, smoothFactor: 0 }).addTo(routeLayer)

    for (const seg of shape.outbound) {
      const { up, down } = merged.get(seg.seq)
      const worst = [up, down].filter(Boolean).sort((a, b) => b.congestion - a.congestion)[0]
      const level = worst?.level || '정보없음'
      const line = L.polyline(seg.coords, {
        color: levelColor(level),
        weight: active ? 7 : 4,
        opacity: 1, // 반투명이면 검은 테두리가 비쳐 혼잡도 색이 탁해짐
        smoothFactor: 0,
      })
      const fmt = (x) => (x ? `${fmtCongestion(x.congestion)} (${x.level})` : '-')
      line.bindTooltip(
        `<b>${route}번</b> ${names.get(seg.seq) || ''} 부근<br/>` +
          `→ ${shape.directions.up || '상행'}: ${fmt(up)}<br/>` +
          `→ ${shape.directions.down || '하행'}: ${fmt(down)}`,
        { sticky: true },
      )
      line.on('click', () => emit('select', route))
      line.addTo(routeLayer)
    }
    outPath.forEach((p) => bounds.push(p))

    if (active) {
      for (const st of geo.stops.filter((s) => s.seq <= shape.turn)) {
        L.circleMarker([st.lat, st.lng], { radius: 3, color: palette.value.ink2, weight: 1, fillColor: palette.value.panel, fillOpacity: 1 })
          .bindTooltip(`${st.name} (${st.seq})`)
          .addTo(routeLayer)
      }
    }
  }

  // 혼잡 지점(혼잡도 0.8 이상) 깜빡임 - 하행 지점도 지도에 그린 상행 선 위로 옮김
  for (const r of props.overview?.routes || []) {
    const geo = props.geometries[r.route]
    if (!geo) continue
    const shape = shapeOf(r.route)
    const drawn = new Map(shape.outbound.map((o) => [o.seq, o]))
    for (const h of r.hotspots) {
      const seg = drawn.get(shape.mapTo.get(h.seq))
      const pos = seg ? seg.coords[Math.floor(seg.coords.length / 2)] : h.lat != null ? [h.lat, h.lng] : null
      if (!pos) continue
      L.marker(pos, {
        icon: L.divIcon({ className: '', html: '<div class="hotspot-pulse"></div>', iconSize: [16, 16], iconAnchor: [8, 8] }),
        zIndexOffset: 1000,
      })
        .bindTooltip(`<b>${r.route}번 혼잡</b><br/>${h.name}<br/>혼잡도 ${fmtCongestion(h.congestion)}`)
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
const TAG_W = 44
const TAG_H = 24
const tagHomes = new Map()
const tagSpots = new Map()

function samplePath(route) {
  const coords = shapeOf(route).outbound.flatMap((o) => o.coords)
  const step = Math.max(1, Math.floor(coords.length / 80))
  return coords.filter((_, i) => i % step === 0)
}

function placeTags() {
  if (!map || !tagLayer) return
  tagLayer.clearLayers()
  const size = map.getSize()

  const routes = Object.keys(props.geometries).sort()
  const paths = new Map(routes.map((r) => [r, samplePath(r).map((ll) => ({ ll, p: map.latLngToContainerPoint(ll) }))]))

  // 지도 안쪽, 확대 버튼·출처 표기를 가리지 않는 자리만 후보
  const usable = (p) =>
    p.x > TAG_W / 2 + 4 && p.x < size.x - TAG_W / 2 - 4 && p.y > TAG_H / 2 + 4 && p.y < size.y - TAG_H / 2 - 24 && !(p.x < 60 && p.y < 90)
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
        html: `<div class="rno route-tag r${route} ${active ? 'on' : ''}">${route}</div>`,
        iconSize: [TAG_W, TAG_H],
        iconAnchor: [TAG_W / 2, TAG_H / 2],
      }),
      zIndexOffset: active ? 900 : 800,
    })
      .on('click', () => emit('select', route))
      .addTo(tagLayer)
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

onMounted(() => {
  // zoomSnap을 잘게 해야 정수 확대에 묶이지 않고 패널에 꽉 차게 맞음
  map = L.map(el.value, { zoomControl: true, preferCanvas: false, zoomSnap: 0.25, zoomDelta: 1 }).setView([37.5665, 126.978], 12)
  map.on('dragstart zoomstart', () => {
    if (!fitting) userMoved = true
  })
  setTiles()
  routeLayer = L.layerGroup().addTo(map)
  hotspotLayer = L.layerGroup().addTo(map)
  tagLayer = L.layerGroup().addTo(map)
  map.on('zoomend moveend resize', placeTags)
  draw()
  // 패널 크기를 바꾸면 지도도 다시 맞춤
  resizeObserver = new ResizeObserver(() => {
    if (!map) return
    map.invalidateSize({ animate: false })
    if (!userMoved) fitRoutes()
  })
  resizeObserver.observe(el.value)
})

watch(() => [props.geometries, props.overview, props.selected], draw)
watch(isNight, () => {
  setTiles()
  draw()
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
.map { width: 100%; flex: 1; min-height: 0; }
.route-tag { width: 44px; height: 24px; font-size: 13px; line-height: 20px; text-align: center; padding: 0; cursor: pointer; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 12px; padding: 6px 10px; border-top: 1px solid var(--line); font-size: 11px; color: var(--ink2); align-items: center; flex: none; }
.legend span { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.lg-dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: var(--c3); }
</style>
