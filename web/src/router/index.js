import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import CuratePage from '../views/CuratePage.vue'

const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/curate', name: 'curate', component: CuratePage },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
