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
        <RecipeEditor
          v-else-if="editing"
          :initial-graph="draft.graph"
          :initial-name="draft.name"
          :recipe-id="draft.recipeId"
          :primitives="primitives"
          @saved="onSaved"
          @cancel="editing = false"
        />
        <template v-else-if="current">
          <div class="d-flex mb-2" style="gap: 8px; flex-wrap: wrap">
            <v-btn size="small" color="success" variant="tonal" @click="runCurrent">▶ 运行此配方</v-btn>
            <v-btn v-if="current.builtin" size="small" variant="tonal" @click="copyToDraft">
              复制为可编辑草稿
            </v-btn>
            <v-btn v-else size="small" color="primary" variant="tonal" @click="editCurrent">编辑</v-btn>
            <v-btn size="small" variant="text" @click="newDraft">＋ 新建空配方</v-btn>
          </div>
          <RecipeFlow :graph="current.graph" :primitives="primitives" />
        </template>
        <div v-else class="text-medium-emphasis">从左侧选一个配方查看其工作流。</div>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import RecipeFlow from '../components/RecipeFlow.vue'
import RecipeEditor from '../components/RecipeEditor.vue'
import { getRecipe, listPrimitives, listRecipes } from '../api/chorus'

const route = useRoute()
const router = useRouter()
const runCurrent = () => router.push({ path: '/roundtable', query: { recipe: current.value.id } })

const recipes = ref([]) // [{id,name,builtin}]
const primitives = ref({}) // name -> spec
const selectedId = ref('')
const current = ref(null) // {id,name,graph}
const loading = ref(false)
const error = ref('')

const editing = ref(false)
const draft = ref({ graph: null, name: '', recipeId: '' })

const EMPTY_GRAPH = { recipe: '', version: 1, nodes: [], edges: [{ from: 'START', to: 'END' }] }

function copyToDraft() {
  draft.value = { graph: current.value.graph, name: current.value.name + ' 副本', recipeId: '' }
  editing.value = true
}
function editCurrent() {
  draft.value = { graph: current.value.graph, name: current.value.name, recipeId: current.value.id }
  editing.value = true
}
function newDraft() {
  draft.value = { graph: EMPTY_GRAPH, name: '', recipeId: '' }
  editing.value = true
}
async function onSaved(r) {
  editing.value = false
  await load()
  if (r?.id) select(r.id)
}

async function load() {
  try {
    const [rs, ps] = await Promise.all([listRecipes(), listPrimitives()])
    recipes.value = rs
    primitives.value = Object.fromEntries(ps.map((p) => [p.name, p]))
    // ?select=id（如 AI 刚搭好的）优先选中，否则选第一个
    const want = route.query.select
    const target = (want && rs.find((r) => r.id === want)?.id) || (rs[0] && rs[0].id)
    if (target) select(target)
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

async function select(id) {
  selectedId.value = id
  editing.value = false
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
