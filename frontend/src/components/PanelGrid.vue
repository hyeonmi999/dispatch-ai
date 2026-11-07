<template>
  <!-- 패널 격자: 제목줄을 끌어 이동, 오른쪽 아래 모서리를 끌어 크기 조절, X로 숨기기 -->
  <div ref="gridEl" class="grid" :style="{ height: gridHeight }">
    <div v-if="ghost" class="ghost" :style="boxStyle(ghost)"><div></div></div>

    <template v-for="(item, id) in layout" :key="id">
      <section
        v-if="item.on || KEEP_MOUNTED.has(id)"
        v-show="item.on"
        class="slot"
        :class="{ moving: drag?.id === id }"
        :style="slotStyle(id, item)"
        :aria-label="TITLES[id]"
      >
        <div class="panel">
          <div class="ph">
            <div class="ph-drag" @pointerdown="begin(id, 'move', $event)">
              <button class="grip" :aria-label="`${TITLES[id]} 패널 이동. 화살표 키로 한 칸씩 움직입니다`" @keydown="nudge(id, $event)">
                <svg viewBox="0 0 10 14" aria-hidden="true">
                  <g fill="currentColor">
                    <rect x="1" y="1" width="2.4" height="2.4" /><rect x="6.6" y="1" width="2.4" height="2.4" />
                    <rect x="1" y="5.8" width="2.4" height="2.4" /><rect x="6.6" y="5.8" width="2.4" height="2.4" />
                    <rect x="1" y="10.6" width="2.4" height="2.4" /><rect x="6.6" y="10.6" width="2.4" height="2.4" />
                  </g>
                </svg>
              </button>
              <h2>{{ TITLES[id] }}</h2>
              <span class="meta">{{ metas[id] }}</span>
            </div>
            <button class="x" :aria-label="`${TITLES[id]} 패널 닫기`" @click="emit('close', id)">
              <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M1 1L9 9M9 1L1 9" stroke="currentColor" fill="none" /></svg>
            </button>
          </div>
          <div class="pb" :class="BODY_CLASS[id]"><slot :name="id" /></div>
          <div class="resize" aria-hidden="true" @pointerdown="begin(id, 'resize', $event)">
            <svg viewBox="0 0 18 18"><path d="M16 8L8 16M16 12L12 16" stroke="currentColor" fill="none" /></svg>
          </div>
        </div>
      </section>
    </template>

    <div v-if="visibleCount === 0" class="empty-note">표시 중인 패널이 없습니다. 패널 메뉴에서 켜 주세요.</div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { COLS, MIN_SIZE, ROW, TITLES, resolve } from '../layout'

const props = defineProps({
  layout: { type: Object, required: true },
  metas: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:layout', 'commit', 'close'])

// 숨겨도 입력한 질문·생성한 권고가 사라지지 않게 유지
const KEEP_MOUNTED = new Set(['advice', 'qa', 'history'])
const BODY_CLASS = { map: 'fill flush', forecast: 'fill', stops: 'fill' }

const gridEl = ref(null)
const drag = ref(null) // 화면 표시용: { id, mode, dx, dy, x0, y0 }
let g = null // 끌기 시작 시점의 값

const visibleCount = computed(() => Object.values(props.layout).filter((i) => i.on).length)
const gridHeight = computed(() => {
  const bottom = Math.max(0, ...Object.values(props.layout).filter((i) => i.on).map((i) => i.y + i.h))
  return `${(bottom + 1) * ROW}px`
})
const ghost = computed(() => (drag.value?.mode === 'move' ? props.layout[drag.value.id] : null))

function boxStyle(i) {
  return { left: `${(i.x / COLS) * 100}%`, top: `${i.y * ROW}px`, width: `${(i.w / COLS) * 100}%`, height: `${i.h * ROW}px` }
}

function slotStyle(id, item) {
  const d = drag.value
  if (d?.id === id && d.mode === 'move') {
    // 끄는 동안은 시작 위치에서 포인터를 따라가고, 들어갈 자리는 ghost로 표시
    return { ...boxStyle({ ...item, x: d.x0, y: d.y0 }), transform: `translate(${d.dx}px, ${d.dy}px)`, zIndex: 40 }
  }
  return { ...boxStyle(item), zIndex: d?.id === id ? 40 : 1 }
}

const clamp = (v, a, b) => Math.max(a, Math.min(b, v))

function begin(id, mode, e) {
  if (e.button !== 0 || window.innerWidth <= 820) return
  e.preventDefault()
  const grid = gridEl.value
  const scale = grid.getBoundingClientRect().width / grid.offsetWidth || 1
  const it = props.layout[id]
  g = { id, mode, sx: e.clientX, sy: e.clientY, scale, col: grid.offsetWidth / COLS, base: props.layout, x0: it.x, y0: it.y, w0: it.w, h0: it.h }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  drag.value = { id, mode, dx: 0, dy: 0, x0: it.x, y0: it.y }
}

function onMove(e) {
  if (!g) return
  const dx = (e.clientX - g.sx) / g.scale
  const dy = (e.clientY - g.sy) / g.scale
  const it = { ...g.base[g.id] }
  if (g.mode === 'move') {
    it.x = clamp(Math.round(g.x0 + dx / g.col), 0, COLS - it.w)
    it.y = Math.max(0, Math.round(g.y0 + dy / ROW))
  } else {
    it.w = clamp(Math.round(g.w0 + dx / g.col), MIN_SIZE[g.id][0], COLS - it.x)
    it.h = Math.max(MIN_SIZE[g.id][1], Math.round(g.h0 + dy / ROW))
  }
  emit('update:layout', resolve({ ...g.base, [g.id]: it }, g.id))
  drag.value = { id: g.id, mode: g.mode, dx, dy, x0: g.x0, y0: g.y0 }
}

function stopListening() {
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', onUp)
}

function onUp() {
  stopListening()
  if (!g) return
  g = null
  drag.value = null
  emit('commit', resolve(props.layout))
}

function nudge(id, e) {
  const dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key]
  if (!dir) return
  e.preventDefault()
  const it = { ...props.layout[id] }
  it.x = clamp(it.x + dir[0], 0, COLS - it.w)
  it.y = Math.max(0, it.y + dir[1])
  emit('commit', resolve({ ...props.layout, [id]: it }, id))
}

onBeforeUnmount(stopListening)
</script>

<style scoped>
.grid { position: relative; margin: 4px 12px 20px; flex: none; }
.slot { position: absolute; padding: 4px; transition: left 0.12s ease, top 0.12s ease, width 0.12s ease, height 0.12s ease; }
.slot.moving { transition: none; opacity: 0.94; }
.ghost { position: absolute; padding: 4px; pointer-events: none; }
.ghost div { height: 100%; border: 1px dashed var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); }
.panel { height: 100%; display: flex; flex-direction: column; background: var(--panel); border: 1px solid var(--line); position: relative; min-width: 0; isolation: isolate; }
.moving .panel { border-color: var(--ink); box-shadow: 0 8px 20px rgba(0, 0, 0, 0.22); }
.ph { display: flex; align-items: center; gap: 6px; height: 32px; flex: none; border-bottom: 1px solid var(--line); padding: 0 4px 0 2px; background: var(--panel2); }
.ph-drag { flex: 1; min-width: 0; display: flex; align-items: center; gap: 4px; height: 100%; cursor: grab; touch-action: none; user-select: none; }
.moving .ph-drag { cursor: grabbing; }
.grip { width: 22px; height: 26px; display: grid; place-items: center; color: var(--ink3); flex: none; cursor: grab; }
.grip svg { width: 10px; height: 14px; }
h2 { margin: 0; font-size: 12px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.meta { font-size: 11px; color: var(--ink3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-left: 6px; }
.x { width: 26px; height: 26px; display: grid; place-items: center; color: var(--ink2); flex: none; }
.x:hover { background: var(--ground); color: var(--ink); }
.x svg { width: 10px; height: 10px; }
.pb { flex: 1; min-height: 0; overflow: auto; padding: 8px 10px; }
.pb.fill { overflow: hidden; display: flex; flex-direction: column; }
.pb.flush { padding: 0; }
.resize { position: absolute; right: 0; bottom: 0; width: 18px; height: 18px; cursor: nwse-resize; touch-action: none; color: var(--ink3); z-index: 500; }
.resize svg { width: 18px; height: 18px; display: block; }

@media (max-width: 820px) {
  .grid { height: auto !important; margin: 8px; }
  .slot { position: static !important; width: 100% !important; height: auto !important; transform: none !important; padding: 0 0 8px; }
  .panel { height: auto; }
  .pb.fill { height: 340px; flex: none; }
  .grip, .resize, .ghost { display: none; }
  .ph-drag { cursor: default; padding-left: 8px; touch-action: auto; }
  .x { width: 44px; height: 32px; }
}
</style>
