<template>
  <div ref="el" class="map"></div>
</template>

<script setup>
// 서울 지도 위 노선도: 정류장 구간마다 혼잡도 색, 혼잡 구간은 빨간 점 깜빡임
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import { LEVEL_COLORS, levelColor } from '../api'
import { routeShape } from '../routeShape'

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

    // 바탕 경로 (흰 테두리 효과) - 상행 경로 하나만
    const outPath = shape.outbound.flatMap((o) => o.coords)
    L.polyline(outPath, { color: '#ffffff', weight: active ? 11 : 7, opacity: active ? 0.95 : 0.6 }).addTo(routeLayer)

    for (const seg of shape.outbound) {
      const { up, down } = merged.get(seg.seq)
      const worst = [up, down].filter(Boolean).sort((a, b) => b.congestion - a.congestion)[0]
      const level = worst?.level || '정보없음'
      const line = L.polyline(seg.coords, {
        color: levelColor(level),
        weight: active ? 7 : 4,
        opacity: active ? 1 : 0.55,
      })
      const fmt = (x) => (x ? `${x.congestion.toFixed(2)} (${x.level})` : '-')
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

    // 노선 번호 라벨
    const mid = outPath[Math.floor(outPath.length / 2)]
    L.marker(mid, {
      icon: L.divIcon({
        className: '',
        html: `<div class="route-pill ${active ? 'on' : ''}">${route}</div>`,
        iconSize: [44, 22],
        iconAnchor: [22, 11],
      }),
    })
      .on('click', () => emit('select', route))
      .addTo(routeLayer)

    if (active) {
      for (const st of geo.stops.filter((s) => s.seq <= shape.turn)) {
        L.circleMarker([st.lat, st.lng], { radius: 3, color: '#52514e', weight: 1, fillColor: '#fff', fillOpacity: 1 })
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
        icon: L.divIcon({ className: '', html: '<div class="hotspot-pulse"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }),
        zIndexOffset: 1000,
      })
        .bindTooltip(`<b>${r.route}번 혼잡</b><br/>${h.name}<br/>혼잡도 ${h.congestion.toFixed(2)}`)
        .on('click', () => emit('select', r.route))
        .addTo(hotspotLayer)
    }
  }

  if (!fitted && bounds.length) {
    map.fitBounds(bounds, { padding: [30, 30] })
    fitted = true
  }
}

function addLegend() {
  const legend = L.control({ position: 'bottomright' })
  legend.onAdd = () => {
    const div = L.DomUtil.create('div', 'map-legend')
    const items = [
      ['여유', '좌석 여유'],
      ['보통', '입석 손잡이'],
      ['혼잡', '통로까지 입석'],
      ['매우혼잡', '정원 초과'],
    ]
    div.innerHTML =
      '<div class="lg-title">차내 혼잡도 (재차 ÷ 정원)</div>' +
      items.map(([k, d]) => `<div class="lg-row"><span class="lg-sw" style="background:${LEVEL_COLORS[k]}"></span>${k} <span class="lg-d">${d}</span></div>`).join('') +
      '<div class="lg-row"><span class="hotspot-pulse lg-dot"></span>혼잡 지점 (0.8 이상)</div>'
    return div
  }
  legend.addTo(map)
}

onMounted(() => {
  map = L.map(el.value, { zoomControl: true, preferCanvas: false }).setView([37.5665, 126.978], 12)
  // 키 없이 쓸 수 있는 회색 배경지도 (노선 색이 잘 보이도록) - Esri World Light Gray Canvas
  const esri = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas'
  L.tileLayer(`${esri}/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`, {
    attribution: 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors',
    maxZoom: 16,
  }).addTo(map)
  L.tileLayer(`${esri}/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 16 }).addTo(map)
  routeLayer = L.layerGroup().addTo(map)
  hotspotLayer = L.layerGroup().addTo(map)
  addLegend()
  draw()
})

watch(() => [props.geometries, props.overview, props.selected], draw)

onBeforeUnmount(() => map?.remove())
</script>

<style>
.map { width: 100%; height: 100%; }
.route-pill {
  display: inline-block; padding: 2px 8px; border-radius: 11px; font-size: 12px; font-weight: 700;
  background: #fff; color: #0b0b0b; border: 1.5px solid #52514e; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
  cursor: pointer; text-align: center;
}
.route-pill.on { background: #2a78d6; color: #fff; border-color: #2a78d6; }
.map-legend {
  background: rgba(255, 255, 255, 0.95); padding: 8px 10px; border-radius: 8px; font-size: 12px;
  line-height: 1.6; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15); color: #0b0b0b;
}
.lg-title { font-weight: 700; margin-bottom: 2px; }
.lg-row { display: flex; align-items: center; gap: 6px; }
.lg-sw { width: 18px; height: 5px; border-radius: 3px; display: inline-block; }
.lg-d { color: #898781; }
.lg-dot { width: 10px; height: 10px; border-width: 1px; animation: none; }
</style>
