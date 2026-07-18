import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],

  server: {
    host: '0.0.0.0',
    port: 8080,
    strictPort: true,

    allowedHosts: [
      'f294-61-221-86-10.ngrok-free.app' /* ngrok 臨時網址*/
    ],

    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5000',
        changeOrigin: true
      },
    // 讓前端可以取得 Flask 的靜態投像圖片
      '/static': {
        target: 'http://127.0.0.1:5000',
        changeOrigin: true
      }
    }
  }
})