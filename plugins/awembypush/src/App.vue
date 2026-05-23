<template>
  <v-app>
    <v-main class="bg-grey-lighten-5">
      <component
        :is="currentView"
        :initial-config="initialConfig"
        :api="api"
        @switch="toggleView"
      />
    </v-main>
  </v-app>
</template>

<script setup>
import { computed, ref } from 'vue'
import Config from './components/Config.vue'
import Page from './components/Page.vue'

const props = defineProps({
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
})

const view = ref('config')

const currentView = computed(() => (view.value === 'config' ? Config : Page))

function toggleView() {
  view.value = view.value === 'config' ? 'page' : 'config'
}
</script>
