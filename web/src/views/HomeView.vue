<template>
  <v-container>
    <div class="text-center mt-8 mb-6">
      <h1 class="text-h4 mb-2">Chorus</h1>
      <p class="text-medium-emphasis">选一种协作配方，开一场。</p>
    </div>

    <!-- L1 配方选择（§6.6；L2 荐配方 / L3 自拼留后）-->
    <v-row justify="center">
      <v-col v-for="r in recipes" :key="r.to" cols="12" sm="6" md="5">
        <v-card variant="outlined" height="100%" class="recipe" @click="go(r.to)">
          <v-card-title>{{ r.title }}</v-card-title>
          <v-card-subtitle>{{ r.tagline }}</v-card-subtitle>
          <v-card-text>{{ r.desc }}</v-card-text>
          <v-card-actions>
            <v-btn :color="r.color" variant="tonal" @click.stop="go(r.to)">{{ r.cta }}</v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>

    <!-- brainApi 连通指示（继承 S1.7）-->
    <div class="text-center mt-8">
      <v-chip
        size="small"
        :color="health === 'ok' ? 'success' : health === 'down' ? 'error' : undefined"
        variant="flat"
        @click="ping"
      >
        编排服务：{{ health === 'ok' ? '已连通' : health === 'down' ? '不可达' : '点击检测' }}
      </v-chip>
    </div>
  </v-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { brainApi } from '../api/brain'

const router = useRouter()
const health = ref('') // '' | 'ok' | 'down'

const recipes = [
  {
    title: '圆桌',
    tagline: '多位 AI 朋友轮流讨论，你随时插话改向',
    desc: '一个议题，到场好友依次发言、彼此看得到上文；每轮后你可插话或让讨论继续，最后主笔综合。',
    color: 'primary',
    cta: '开圆桌',
    to: '/roundtable',
  },
  {
    title: '扇出策展',
    tagline: '多位 AI 并行给候选，你来策展',
    desc: '一个需求，到场好友并行各出一份候选；你选中 / 淘汰 / 把某个点交给别人写，最后汇成产出。',
    color: 'success',
    cta: '去策展',
    to: '/curate',
  },
]

const go = (to) => router.push(to)

async function ping() {
  try {
    await brainApi.get('/health')
    health.value = 'ok'
  } catch {
    health.value = 'down'
  }
}

onMounted(ping)
</script>

<style scoped>
.recipe {
  cursor: pointer;
  transition: box-shadow 0.15s;
}
.recipe:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.15);
}
</style>
