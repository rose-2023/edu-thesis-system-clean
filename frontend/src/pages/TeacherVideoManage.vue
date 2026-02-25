<template>
  <div class="page">
    <h2>影片管理（啟用 / 停用）</h2>
    <button @click="load">重新整理</button>

    <div v-for="v in videos" :key="v.id" class="card">
      <div class="row">
        <div>
          <b>{{ v.unit }}｜{{ v.title }}</b>
          <div class="sub">字幕：{{ v.subtitle_uploaded ? "已上傳" : "未上傳" }} / {{ v.subtitle_verified ? "已驗證" : "未驗證" }}</div>
        </div>

        <button @click="toggle(v)">
          {{ v.enabled ? "停用" : "啟用" }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { api } from "../api";

const videos = ref([]);

async function load() {
  const r = await api.get("/video_admin/list");
  videos.value = r.data.videos || [];
}

async function toggle(v) {
  await api.post("/video_admin/set_enabled", { video_id: v.id, enabled: !v.enabled });
  await load();
}

onMounted(load);
</script>

<style scoped>
.page { padding: 16px; }
.card { background:#1e1e1e; padding:12px; border-radius:10px; margin-top:12px; }
.row { display:flex; justify-content:space-between; align-items:center; gap:12px; }
.sub { opacity:0.8; margin-top:4px; }
button { padding:8px 12px; border-radius:8px; border:0; cursor:pointer; }
</style>
