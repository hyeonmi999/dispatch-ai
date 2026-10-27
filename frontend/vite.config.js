import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// /api 요청은 FastAPI(8000)로 프록시
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
