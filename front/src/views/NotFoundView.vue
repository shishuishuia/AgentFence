<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <section class="panel empty-state">
    <p class="eyebrow">404 / PAGE NOT FOUND</p>
    <h1>页面不存在</h1>
    <p>请检查地址，或返回项目总览继续。</p>
    <RouterLink class="button primary" to="/dashboard">返回总览</RouterLink>
  </section>
</template>
