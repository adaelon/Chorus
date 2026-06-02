<template>
  <v-app>
    <v-app-bar flat title="Chorus">
      <v-btn to="/" variant="text">Home</v-btn>
      <v-btn to="/roundtable" variant="text">圆桌</v-btn>
      <v-btn to="/curate" variant="text">策展</v-btn>
      <v-btn to="/contacts" variant="text">好友</v-btn>
    </v-app-bar>
    <v-main>
      <router-view />
    </v-main>
  </v-app>
</template>

<script setup></script>
