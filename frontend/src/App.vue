<template>
  <n-config-provider :theme="isNight ? darkTheme : null" :theme-overrides="themeOverrides" :locale="koKR" :date-locale="dateKoKR">
    <n-message-provider>
      <Dashboard />
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { computed } from 'vue'
import { NConfigProvider, NMessageProvider, darkTheme, koKR, dateKoKR } from 'naive-ui'
import Dashboard from './components/Dashboard.vue'
import { FONT_MONO, FONT_SANS, isNight, palette } from './theme'

const themeOverrides = computed(() => {
  const p = palette.value
  return {
    common: {
      primaryColor: p.accent,
      primaryColorHover: p.accentHover,
      primaryColorPressed: p.accentPressed,
      primaryColorSuppl: p.accentHover,
      fontFamily: FONT_SANS,
      fontFamilyMono: FONT_MONO,
      borderRadius: '2px',
      borderRadiusSmall: '2px',
      borderColor: p.line,
      dividerColor: p.line,
      textColorBase: p.ink,
      textColor1: p.ink,
      textColor2: p.ink,
      textColor3: p.ink3,
      placeholderColor: p.ink3,
    },
    Button: { textColorPrimary: isNight.value ? '#14181B' : '#FFFFFF', textColorHoverPrimary: isNight.value ? '#14181B' : '#FFFFFF', textColorPressedPrimary: isNight.value ? '#14181B' : '#FFFFFF', textColorFocusPrimary: isNight.value ? '#14181B' : '#FFFFFF' },
  }
})
</script>
