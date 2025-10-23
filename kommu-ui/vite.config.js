import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'


const isDocker = process.env.DOCKER_ENV === 'true'


export default defineConfig({
  base: isDocker ? './' : '/ui/',
  plugins: [react()],
  build: {
    sourcemap: false,
    outDir: 'dist',
    emptyOutDir: true
  },
  server: {
    host: '0.0.0.0',
    port: 5173
  }
})
