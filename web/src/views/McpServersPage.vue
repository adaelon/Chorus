<template>
  <v-container>
    <h2 class="mb-1">MCP 工具注册表</h2>
    <p class="text-medium-emphasis mb-4">
      圆桌 AI 的工具面（沙箱之外）：登记 MCP server，AI 发言那一轮可调用它们的工具。
      <br />
      改动后需<strong>重启服务</strong>生效（启动时连各 server 拉工具目录）。需 <code>CHORUS_EXECUTION=1</code>
      或配了沙箱时才启用执行。
    </p>

    <v-alert v-if="error" type="error" class="mb-4" :text="error" />

    <!-- 从预设添加 -->
    <v-card variant="tonal" class="mb-4">
      <v-card-title class="text-subtitle-1">从预设添加</v-card-title>
      <v-card-text>
        <p class="text-medium-emphasis text-body-2 mb-2">
          点一个官方 MCP server 预设 → 下方表单自动预填 → 按需改路径/参数 → 点「新建」保存。
        </p>
        <v-chip-group column>
          <v-chip
            v-for="p in presets"
            :key="p.name"
            variant="outlined"
            @click="applyPreset(p)"
          >
            {{ p.name }}
          </v-chip>
        </v-chip-group>
        <v-alert
          v-if="presetHint"
          type="info"
          variant="tonal"
          density="compact"
          class="mt-2"
          :text="presetHint"
        />
      </v-card-text>
    </v-card>

    <!-- 新建 / 编辑表单 -->
    <v-card variant="outlined" class="mb-6">
      <v-card-title>{{ editing ? '编辑' : '新建' }} MCP server</v-card-title>
      <v-card-text>
        <v-text-field v-model="form.name" label="显示名（如 filesystem / web-search）" variant="outlined" />
        <v-select
          v-model="form.transport"
          :items="[
            { value: 'stdio', title: 'stdio（起子进程：command + args）' },
            { value: 'sse', title: 'sse（连远程 SSE server：url）' },
          ]"
          item-title="title"
          item-value="value"
          label="连接方式"
          variant="outlined"
        />

        <template v-if="form.transport === 'stdio'">
          <v-text-field
            v-model="form.command"
            label="command（如 npx / python / uvx）"
            variant="outlined"
          />
          <v-text-field
            v-model="argsText"
            label="args（空格分隔，如 -y @modelcontextprotocol/server-filesystem /tmp）"
            variant="outlined"
            hint="按空格拆成数组；带空格的参数暂不支持（用 sse 或后续增强）"
            persistent-hint
          />
        </template>
        <template v-else>
          <v-text-field
            v-model="form.url"
            label="url（SSE server，如 http://127.0.0.1:8931/sse）"
            variant="outlined"
          />
        </template>

        <v-btn color="primary" :loading="loading" :disabled="!form.name" class="mt-2" @click="save">
          {{ editing ? '保存' : '新建' }}
        </v-btn>
        <v-btn v-if="editing" class="ml-2" variant="text" @click="resetForm">取消</v-btn>
      </v-card-text>
    </v-card>

    <!-- 列表 -->
    <v-list>
      <v-list-item v-for="m in servers" :key="m.id" :title="m.name" :subtitle="subtitleOf(m)">
        <template #append>
          <v-btn size="small" variant="text" @click="edit(m)">编辑</v-btn>
          <v-btn size="small" color="error" variant="text" @click="remove(m.id)">删除</v-btn>
        </template>
      </v-list-item>
      <v-list-item v-if="!servers.length" title="还没有 MCP server" subtitle="新建一个，重启后圆桌 AI 即可调用它的工具。" />
    </v-list>
  </v-container>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  updateMcpServer,
} from '../api/chorus'

const servers = ref([])
const loading = ref(false)
const error = ref('')
const editing = ref(false)

const blank = () => ({ id: '', name: '', transport: 'stdio', command: '', args: [], url: '' })
const form = ref(blank())
const presetHint = ref('')

// 官方 MCP server 预设：点一下预填表单，需要的路径/参数由用户再改。
// command/args 是常见安装方式（npx 需 node、uvx 需 uv/uvx）。
const presets = [
  {
    name: 'filesystem',
    transport: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem', '/tmp'],
    hint: 'filesystem：读写本地目录。把 /tmp 改成要暴露给 AI 的目录（需 node/npx）。',
  },
  {
    name: 'fetch',
    transport: 'stdio',
    command: 'uvx',
    args: ['mcp-server-fetch'],
    hint: 'fetch：抓网页转 markdown（需 uv/uvx）。',
  },
  {
    name: 'git',
    transport: 'stdio',
    command: 'uvx',
    args: ['mcp-server-git', '--repository', '/path/to/repo'],
    hint: 'git：操作本地仓库。把 /path/to/repo 改成你的仓库路径（需 uv/uvx）。',
  },
  {
    name: 'memory',
    transport: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-memory'],
    hint: 'memory：知识图谱式长期记忆（需 node/npx）。',
  },
  {
    name: 'sequential-thinking',
    transport: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-sequential-thinking'],
    hint: 'sequential-thinking：分步推理工具（需 node/npx）。',
  },
  {
    name: 'time',
    transport: 'stdio',
    command: 'uvx',
    args: ['mcp-server-time'],
    hint: 'time：时区/时间换算（需 uv/uvx）。',
  },
]

function applyPreset(p) {
  editing.value = false
  form.value = {
    id: '',
    name: p.name,
    transport: p.transport,
    command: p.command || '',
    args: [...(p.args || [])],
    url: p.url || '',
  }
  presetHint.value = p.hint || ''
}

// args 数组 ↔ 空格分隔文本（表单友好）
const argsText = computed({
  get: () => (form.value.args || []).join(' '),
  set: (v) => {
    form.value.args = v.split(/\s+/).filter(Boolean)
  },
})

function subtitleOf(m) {
  return m.transport === 'sse'
    ? `sse · ${m.url || '—'}`
    : `stdio · ${m.command || '—'} ${(m.args || []).join(' ')}`
}

function resetForm() {
  form.value = blank()
  editing.value = false
  presetHint.value = ''
}

async function load() {
  error.value = ''
  try {
    servers.value = await listMcpServers()
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

async function save() {
  loading.value = true
  error.value = ''
  try {
    if (editing.value) {
      await updateMcpServer(form.value.id, form.value)
    } else {
      const payload = { ...form.value, id: `mcp-${crypto.randomUUID().slice(0, 8)}` }
      await createMcpServer(payload)
    }
    resetForm()
    await load()
  } catch (e) {
    error.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    loading.value = false
  }
}

function edit(m) {
  form.value = {
    id: m.id,
    name: m.name,
    transport: m.transport || 'stdio',
    command: m.command || '',
    args: m.args || [],
    url: m.url || '',
  }
  editing.value = true
}

async function remove(id) {
  error.value = ''
  try {
    await deleteMcpServer(id)
    await load()
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

onMounted(load)
</script>
