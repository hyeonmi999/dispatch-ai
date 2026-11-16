// 버스 아이콘 (직접 그린 한 색 도형). 채팅·길안내 패널은 BusIcon.vue로, 지도(Leaflet)는 busSvg()로 씀
// 32×32 기준: 차체에서 창문·행선판·전조등을 뚫고(evenodd), 바퀴와 사이드미러를 붙임
export const BUS_PATH = [
  'M9 3h14a4 4 0 0 1 4 4v15a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4V7a4 4 0 0 1 4-4z', // 차체
  'M9 6h14v2.5H9z', // 행선판
  'M8.5 10.5h15v8h-15z', // 앞유리
  'M9 22.5a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0-3 0z', // 전조등
  'M20 22.5a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0-3 0z',
  'M7 26h5v3a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1z', // 바퀴
  'M20 26h5v3a1 1 0 0 1-1 1h-3a1 1 0 0 1-1-1z',
  'M2 10h2v5H2z', // 사이드미러
  'M28 10h2v5h-2z',
].join('')

// Leaflet divIcon에 넣는 문자열 (사용자 입력이 들어가지 않는 고정 도형)
export const busSvg = (size = 16) =>
  `<svg viewBox="0 0 32 32" width="${size}" height="${size}" aria-hidden="true"><path d="${BUS_PATH}" fill="currentColor" fill-rule="evenodd"/></svg>`
