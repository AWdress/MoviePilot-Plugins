<template>
  <div class="awembypush-config pa-4">
    <div class="d-flex justify-space-between align-center mb-6">
      <div class="d-flex align-center">
        <v-icon icon="mdi-cog-outline" size="24" class="mr-2" color="primary"></v-icon>
        <div>
          <h2 class="text-h6 font-weight-bold">AWEmbyPush · 配置</h2>
          <div class="text-caption text-medium-emphasis">设置 Emby Webhook 通知推送参数</div>
        </div>
      </div>
      <div>
        <v-btn color="primary" variant="tonal" size="small" @click="emit('switch')" class="mr-2">
          <v-icon icon="mdi-view-dashboard" size="18" class="mr-1"></v-icon>
          状态页
        </v-btn>
        <v-btn color="primary" @click="saveConfig" size="small" :loading="saving">
          <v-icon icon="mdi-content-save" size="18" class="mr-1"></v-icon>
          保存
        </v-btn>
        <v-btn variant="text" size="small" @click="emit('close')">
          <v-icon icon="mdi-close" size="18"></v-icon>
        </v-btn>
      </div>
    </div>

    <!-- 通用设置 -->
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center text-subtitle-1">
        <v-icon icon="mdi-tune" size="20" color="primary" class="mr-2"></v-icon>
        基础设置
      </v-card-title>
      <v-divider></v-divider>
      <v-card-text>
        <v-row>
          <v-col cols="12" md="6">
            <v-switch
              v-model="config.enabled"
              label="启用插件"
              color="primary"
              hide-details
              density="compact"
            ></v-switch>
            <div class="text-caption text-medium-emphasis ml-12">开启后才处理 Webhook 通知</div>
          </v-col>
          <v-col cols="12" md="6">
            <v-switch
              v-model="config.enable_tmdb"
              label="开启 TMDB 元数据增强"
              color="primary"
              hide-details
              density="compact"
            ></v-switch>
            <div class="text-caption text-medium-emphasis ml-12">通过 TMDB API 补充中文名、简介、评分等</div>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <!-- 消息提示 -->
    <v-snackbar v-model="message.show" :color="message.type" :timeout="3000" location="top">
      {{ message.text }}
    </v-snackbar>
  </div>
</template>

<script setup>
import { reactive, ref, onMounted } from 'vue'

const props = defineProps({
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['close', 'switch'])

const config = reactive({
  enabled: false,
  enable_tmdb: true,
  ...props.initialConfig,
})

const saving = ref(false)
const message = reactive({ show: false, type: 'info', text: '' })

function setMessage(type, text) {
  message.type = type
  message.text = text
  message.show = true
}

async function request(path, options = {}) {
  const apiPath = `plugin/AWEmbyPush${path}`
  if (options.method === 'POST' && props.api?.post) {
    return props.api.post(apiPath, options.body ? JSON.parse(options.body) : {}, options)
  } else if (props.api?.get) {
    return props.api.get(apiPath, options)
  }
  const response = await fetch(`/api/v1/${apiPath}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  return response.json()
}

async function saveConfig() {
  saving.value = true
  try {
    const res = await request('/config', {
      method: 'POST',
      body: JSON.stringify(config)
    })
    if (res && res.code === 0) {
      setMessage('success', '配置保存成功')
    } else {
      setMessage('error', res?.message || '配置保存失败')
    }
  } catch (error) {
    setMessage('error', `保存出错: ${error.message}`)
  } finally {
    saving.value = false
  }
}
</script>