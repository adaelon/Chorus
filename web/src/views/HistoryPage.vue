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
          <v-chip size="x-small" label variant="tonal" class="mr-2">
            {{ c.recipe_id ? '配方' : '圆桌' }}
          </v-chip>
          <v-btn
            size="x-small"
            variant="text"
            color="error"
            :loading="deleting === c.id"
            @click.stop="remove(c.id)"
          >
            删除
          </v-btn>
        </template>
      </v-list-item>
    </v-list>
    <div v-else class="text-medium-emphasis">还没有对话。去首页开一场吧。</div>
  </v-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { deleteConversation, listConversations } from '../api/chorus'

const router = useRouter()
const convos = ref([])
const error = ref('')
const deleting = ref('')

async function remove(id) {
  if (!confirm('删除这场对话？不可恢复。')) return
  deleting.value = id
  try {
    await deleteConversation(id)
    convos.value = convos.value.filter((c) => c.id !== id)
  } catch (e) {
    error.value = '删除失败：' + String(e?.message || e)
  } finally {
    deleting.value = ''
  }
}

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
