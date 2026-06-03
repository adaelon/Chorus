<template>
  <v-container>
    <h2 class="mb-1">配方库</h2>
    <p class="text-medium-emphasis mb-4">
      每个配方是一张原语有向图——选一个看它的工作流（只读）。AI 现编的图也会在这里长成同样看得懂的卡片流。
    </p>

    <v-alert v-if="error" type="error" class="mb-4" :text="error" />

    <v-row>
      <v-col cols="12" sm="4" md="3">
        <v-list density="compact" nav>
          <v-list-subheader>库内配方</v-list-subheader>
          <v-list-item
            v-for="r in recipes"
            :key="r.id"
            :active="selectedId === r.id"
            @click="select(r.id)"
          >
            <v-list-item-title>{{ r.name || r.id }}</v-list-item-title>
            <template #append>
              <v-chip v-if="r.builtin" size="x-small" label color="primary" variant="tonal">内置</v-chip>
            </template>
          </v-list-item>
        </v-list>
      </v-col>

      <v-col cols="12" sm="8" md="9">
        <div v-if="loading" class="text-medium-emphasis">加载中…</div>
        <RecipeFlow v-else-if="current" :graph="current.graph" :primitives="primitives" />
        <div v-else class="text-medium-emphasis">从左侧选一个配方查看其工作流。</div>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import RecipeFlow from '../components/RecipeFlow.vue'
import { getRecipe, listPrimitives, listRecipes } from '../api/chorus'

const recipes = ref([]) // [{id,name,builtin}]
const primitives = ref({}) // name -> spec
const selectedId = ref('')
const current = ref(null) // {id,name,graph}
const loading = ref(false)
const error = ref('')

async function load() {
  try {
    const [rs, ps] = await Promise.all([listRecipes(), listPrimitives()])
    recipes.value = rs
    primitives.value = Object.fromEntries(ps.map((p) => [p.name, p]))
    if (rs.length) select(rs[0].id)
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

async function select(id) {
  selectedId.value = id
  loading.value = true
  error.value = ''
  try {
    current.value = await getRecipe(id)
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
