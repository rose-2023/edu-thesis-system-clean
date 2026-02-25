<template>
  <div class="video-wrap">
    <video ref="vref" class="video" controls :src="src" @loadedmetadata="onReady"></video>

    <div v-if="segment" class="segment-bar">
      <div>建議回看：{{ fmt(segment.start) }} - {{ fmt(segment.end) }}</div>
      <button @click="jump">跳回片段</button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from "vue";

const props = defineProps({
  src: { type: String, required: true },
  segment: { type: Object, default: null } // {start,end} seconds
});

const emit = defineEmits(["jumped", "ready"]);

const vref = ref(null);

function onReady() {
  emit("ready");
}

function fmt(sec) {
  if (sec == null) return "--:--";
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

function jump() {
  if (!vref.value || !props.segment) return;
  vref.value.currentTime = props.segment.start || 0;
  vref.value.play();
  emit("jumped", props.segment);
}
</script>

<style scoped>
.video { width: 100%; border-radius: 10px; background: #000; }
.segment-bar { margin-top: 10px; display: flex; justify-content: space-between; gap: 10px; align-items: center; }
button { padding: 8px 12px; border: 0; border-radius: 8px; cursor: pointer; }
</style>
