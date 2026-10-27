<template>
  <n-config-provider :theme-overrides="themeOverrides" :locale="koKR" :date-locale="dateKoKR">
    <n-message-provider>
      <Dashboard />
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { NConfigProvider, NMessageProvider, koKR, dateKoKR } from 'naive-ui'
import Dashboard from './components/Dashboard.vue'

const themeOverrides = {
  common: {
    primaryColor: '#2a78d6',
    primaryColorHover: '#3987e5',
    primaryColorPressed: '#1c5cab',
    fontFamily: "'Pretendard', -apple-system, 'Apple SD Gothic Neo', sans-serif",
    borderRadius: '8px',
  },
}
</script>
