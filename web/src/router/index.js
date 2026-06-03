import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import CuratePage from '../views/CuratePage.vue'
import ChatPage from '../views/ChatPage.vue'
import ContactsPage from '../views/ContactsPage.vue'
import RecipesPage from '../views/RecipesPage.vue'
import HistoryPage from '../views/HistoryPage.vue'

const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/roundtable', name: 'roundtable', component: ChatPage },
  { path: '/curate', name: 'curate', component: CuratePage },
  { path: '/recipes', name: 'recipes', component: RecipesPage },
  { path: '/history', name: 'history', component: HistoryPage },
  { path: '/contacts', name: 'contacts', component: ContactsPage },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
