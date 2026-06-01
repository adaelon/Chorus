<template>
  <v-container>
    <v-card max-width="560" class="mx-auto mt-8" variant="outlined">
      <v-card-title>Chorus 产品骨架</v-card-title>
      <v-card-text>
        <p class="mb-4">空白产品页（S1.7 前端基座）。点击验证与编排服务（brainApi）的连通。</p>
        <v-btn color="primary" :loading="loading" @click="ping">Ping brainApi</v-btn>
        <v-alert
          v-if="result"
          class="mt-4"
          :type="result.ok ? 'success' : 'error'"
          :text="result.msg"
        />
      </v-card-text>
    </v-card>
  </v-container>
</template>

<script setup>
import { ref } from 'vue'
import { brainApi } from '../api/brain'

const loading = ref(false)
const result = ref(null)

async function ping() {
  loading.value = true
  result.value = null
  try {
    const { data } = await brainApi.get('/health')
    result.value = { ok: true, msg: `200 OK — ${JSON.stringify(data)}` }
  } catch (e) {
    result.value = { ok: false, msg: String(e?.message || e) }
  } finally {
    loading.value = false
  }
}
</script>
