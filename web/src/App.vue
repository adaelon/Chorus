<template>
  <v-app>
    <v-app-bar flat title="Chorus">
      <v-btn to="/" variant="text">Home</v-btn>
      <v-btn to="/roundtable" variant="text">圆桌</v-btn>
      <v-btn to="/curate" variant="text">策展</v-btn>
      <v-btn to="/recipes" variant="text">配方</v-btn>
      <v-btn to="/history" variant="text">历史</v-btn>
      <v-btn to="/contacts" variant="text">好友</v-btn>
      <v-btn to="/llm-backends" variant="text">模型</v-btn>
      <v-btn to="/mcp-servers" variant="text">工具</v-btn>
    </v-app-bar>
    <v-main>
      <router-view />
    </v-main>
  </v-app>
</template>

<script setup></script>
