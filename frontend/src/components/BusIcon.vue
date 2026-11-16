<template>
  <svg class="bus-icon" viewBox="0 0 32 32" :width="size" :height="size" :role="label ? 'img' : undefined" :aria-label="label || undefined" :aria-hidden="label ? undefined : 'true'">
    <path :d="BUS_PATH" fill="currentColor" fill-rule="evenodd" />
  </svg>
</template>

<script setup>
import { BUS_PATH } from '../busIcon'

defineProps({
  size: { type: Number, default: 18 },
  label: { type: String, default: '' },
})
</script>

<style scoped>
.bus-icon { flex: none; vertical-align: middle; }
</style>
