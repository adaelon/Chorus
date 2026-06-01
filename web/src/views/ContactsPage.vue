<template>
  <v-container>
    <h2 class="mb-4">好友注册表（Contact）</h2>

    <v-alert v-if="error" type="error" class="mb-4" :text="error" />

    <!-- 新建 / 编辑表单 -->
    <v-card variant="outlined" class="mb-6">
      <v-card-title>{{ editing ? '编辑' : '新建' }}好友</v-card-title>
      <v-card-text>
        <v-text-field v-model="form.id" label="id（唯一）" :disabled="editing" variant="outlined" />
        <v-text-field v-model="form.name" label="名字" variant="outlined" />
        <v-text-field v-model="form.title" label="头衔" variant="outlined" />
        <v-text-field v-model="form.persona_style" label="说话风格" variant="outlined" />
        <v-text-field v-model="form.base_stance" label="底层立场" variant="outlined" />
        <v-btn color="primary" :loading="loading" :disabled="!form.id || !form.name" @click="save">
          {{ editing ? '保存' : '新建' }}
        </v-btn>
        <v-btn v-if="editing" class="ml-2" variant="text" @click="resetForm">取消</v-btn>
      </v-card-text>
    </v-card>

    <!-- 列表 -->
    <v-list>
      <v-list-item
        v-for="c in contacts"
        :key="c.id"
        :title="`${c.name}（${c.id}）`"
        :subtitle="`${c.title || '—'} · 风格:${c.persona_style || '—'} · 立场:${c.base_stance || '—'} · 信誉:${c.reputation}`"
      >
        <template #append>
          <v-btn size="small" variant="text" @click="edit(c)">编辑</v-btn>
          <v-btn size="small" color="error" variant="text" @click="remove(c.id)">删除</v-btn>
        </template>
      </v-list-item>
    </v-list>
  </v-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { createContact, deleteContact, listContacts, updateContact } from '../api/chorus'

const contacts = ref([])
const loading = ref(false)
const error = ref('')
const editing = ref(false)

const blank = () => ({ id: '', name: '', title: '', persona_style: '', base_stance: '' })
const form = ref(blank())

function resetForm() {
  form.value = blank()
  editing.value = false
}

async function load() {
  error.value = ''
  try {
    contacts.value = await listContacts()
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

async function save() {
  loading.value = true
  error.value = ''
  try {
    if (editing.value) {
      await updateContact(form.value.id, form.value)
    } else {
      await createContact(form.value)
    }
    resetForm()
    await load()
  } catch (e) {
    error.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    loading.value = false
  }
}

function edit(c) {
  form.value = { id: c.id, name: c.name, title: c.title, persona_style: c.persona_style, base_stance: c.base_stance }
  editing.value = true
}

async function remove(id) {
  error.value = ''
  try {
    await deleteContact(id)
    await load()
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

onMounted(load)
</script>
