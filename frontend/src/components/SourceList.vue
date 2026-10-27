<template>
  <n-collapse :default-expanded-names="[]" class="sources">
    <n-collapse-item v-for="s in sources" :key="s.id + s.article" :name="s.id">
      <template #header>
        <span class="src"><b>[{{ s.id }}]</b> {{ s.document }} · {{ s.article }}</span>
      </template>
      <template #header-extra><span class="muted num small">{{ s.score.toFixed(2) }}</span></template>
      <div class="snippet">{{ s.snippet }}</div>
      <div class="muted small">
        <span v-if="s.page">PDF {{ s.page }}쪽</span>
        <a v-if="s.url" :href="s.url" target="_blank" rel="noopener">원문 보기</a>
      </div>
    </n-collapse-item>
  </n-collapse>
</template>

<script setup>
import { NCollapse, NCollapseItem } from 'naive-ui'

defineProps({ sources: { type: Array, default: () => [] } })
</script>

<style scoped>
.src { font-size: 12.5px; }
.small { font-size: 12px; }
.snippet { font-size: 12.5px; line-height: 1.6; color: var(--ink-2); background: #faf9f6; padding: 8px; border-radius: 6px; margin-bottom: 4px; }
</style>
