<template>
  <v-container>
    <h2 class="mb-1">历史对话</h2>
    <p class="text-medium-emphasis mb-4">过往的每一场会话。没跑完的（停在让位窗口/未收尾）可以接着继续。</p>

    <v-alert v-if="error" type="error" class="mb-4" :text="error" />

    <v-list v-if="convos.length" lines="two">
      <v-list-item
        v-for="c in convos"
        :key="c.id"
        :title="c.title || '(无题)'"
        :subtitle="fmt(c.created_at)"
        @click="open(c.id)"
      >
        <template #append>
          <v-chip size="x-small" label :color="c.resumable_hint ? 'warning' : 'default'" variant="tonal">
            {{ c.recipe_id ? '配方' : '圆桌' }}
          </v-chip>
        </template>
      </v-list-item>
    </v-list>
    <div v-else class="text-medium-emphasis">还没有对话。去首页开一场吧。</div>
  </v-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listConversations } from '../api/chorus'

const router = useRouter()
const convos = ref([])
const error = ref('')

const fmt = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : '')
// 打开 = 进圆桌页载入该会话（只读查看 / 未结束可继续，由 ChatPage 据 resumable 决定）
const open = (id) => router.push({ path: '/roundtable', query: { conversation: id } })

onMounted(async () => {
  try {
    convos.value = await listConversations()
  } catch (e) {
    error.value = String(e?.message || e)
  }
})
</script>
