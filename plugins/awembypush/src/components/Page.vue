<template>
  <div class="awembypush-page pa-4">
    <div class="d-flex justify-space-between align-center mb-6">
      <div class="d-flex align-center">
        <v-icon icon="mdi-bell-ring-outline" size="24" class="mr-2" color="primary"></v-icon>
        <div>
          <h2 class="text-h6 font-weight-bold">AWEmbyPush</h2>
          <div class="text-caption text-medium-emphasis">最近推送记录</div>
        </div>
      </div>
      <div>
        <v-btn color="primary" variant="tonal" size="small" @click="emit('switch')" class="mr-2">
          <v-icon icon="mdi-cog" size="18" class="mr-1"></v-icon>
          配置
        </v-btn>
        <v-btn variant="text" size="small" @click="emit('close')">
          <v-icon icon="mdi-close" size="18"></v-icon>
        </v-btn>
      </div>
    </div>

    <!-- 数据占位 -->
    <v-card variant="outlined" class="pa-8 text-center bg-grey-lighten-4">
      <v-icon icon="mdi-information-outline" size="48" color="grey-lighten-1" class="mb-4"></v-icon>
      <div class="text-subtitle-1 text-medium-emphasis">更多功能和日志开发中...</div>
    </v-card>
  </div>
</template>

<script setup>
const props = defineProps({
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['close', 'switch'])
</script>