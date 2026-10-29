import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

if (window.location.hostname === 'lifeos-pc4x.vercel.app') {
  const destination = new URL(window.location.href)
  destination.hostname = 'lifeos-sigma-sooty.vercel.app'
  window.location.replace(destination.href)
} else {
  createApp(App).mount('#app')
}
