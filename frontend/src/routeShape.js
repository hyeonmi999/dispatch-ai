// 노선 형태 계산: 상행(기점 → 회차 종점) / 하행(회차 종점 → 기점)
//
// 서울 간선버스는 왕복 노선이라 상행·하행이 반대 차로나 인접 도로로 따로 그려집니다.
// 지도에서는 상행 경로 하나만 그리고, 하행 구간은 가장 가까운 상행 구간에 겹쳐
// 두 방향 중 더 혼잡한 값을 색으로 씁니다.

// 회차 정류장 순번 (is_turn, 없으면 방향이 바뀌기 직전 정류장)
export function turnSeq(stops) {
  const t = stops.find((s) => s.is_turn)
  if (t) return t.seq
  const i = stops.findIndex((s) => s.direction !== stops[0]?.direction)
  return i > 0 ? stops[i - 1].seq : stops[stops.length - 1]?.seq
}

function midpoint(coords) {
  return coords[Math.floor(coords.length / 2)]
}

function dist2([a, b], [c, d]) {
  const k = Math.cos((a * Math.PI) / 180)
  return (a - c) ** 2 + ((b - d) * k) ** 2
}

// geo → { turn, outbound: [segment], mapTo: Map(구간 seq → 지도에 그리는 상행 구간 seq) }
export function routeShape(geo) {
  const turn = turnSeq(geo.stops)
  const outbound = geo.segments.filter((s) => s.seq < turn)
  const mapTo = new Map(outbound.map((s) => [s.seq, s.seq]))
  for (const seg of geo.segments) {
    if (seg.seq < turn || !outbound.length) continue
    const m = midpoint(seg.coords)
    let best = outbound[0]
    for (const o of outbound) if (dist2(m, midpoint(o.coords)) < dist2(m, midpoint(best.coords))) best = o
    mapTo.set(seg.seq, best.seq)
  }
  const directions = {
    up: geo.stops[0]?.direction,
    down: geo.stops.find((s) => s.seq > turn)?.direction,
  }
  return { turn, outbound, mapTo, directions }
}
