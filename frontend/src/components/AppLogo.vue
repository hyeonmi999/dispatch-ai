<template>
  <!-- 한 곳에서 세 갈래로 배차. 색칠한 첫 갈래가 현재 구현된 버스 -->
  <svg :width="size" :height="size" viewBox="0 0 32 32" role="img" aria-label="통합 배차 AI 플랫폼 로고" class="logo">
    <path class="line" d="M13 16H26" />
    <path class="line" d="M13 16L21 24H26" />
    <path class="line accent" d="M6 16H13L21 8H26" />
    <circle class="dot" cx="5" cy="16" r="3.5" />
    <circle class="dot accent" cx="27" cy="8" r="3" />
    <circle class="dot" cx="27" cy="16" r="3" />
    <circle class="dot" cx="27" cy="24" r="3" />
  </svg>
</template>

<script setup>
defineProps({ size: { type: Number, default: 30 } })
</script>

<style scoped>
.logo { flex: none; display: block; }
.line { fill: none; stroke: currentColor; stroke-width: 3; stroke-linecap: square; stroke-linejoin: miter; }
.line.accent { stroke: #ff8a4c; }
.dot { fill: currentColor; }
.dot.accent { fill: #ff8a4c; }
</style>
