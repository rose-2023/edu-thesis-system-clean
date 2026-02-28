<template>
  <div class="postTestEntry">
    <div class="box">
      <h2>後測載入中...</h2>
      <p v-if="err" class="err">{{ err }}</p>
    </div>
  </div>
</template>

<script setup>
// ✅ 後測入口頁（B）：只負責「檢查是否開放」→ 導到 /posttest/parsons
import { onMounted, ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { api } from "../api.js"; // ✅ 一律用 named export，避免 default 匯入錯誤

const router = useRouter();
const route = useRoute();

const err = ref("");

async function boot() {
  try {
    const test_cycle_id = String(route.query.test_cycle_id || "default");
    // 1) 先讀後測開放狀態（統一讀 test_control）
    const r = await api.get("/api/parsons/test/status", {
      params: { test_cycle_id, test_role: "post" },
    });
    if (!r?.data?.ok) {
      err.value = "後測狀態讀取失敗";
      return;
    }
    if (!r.data.post_open) {
      err.value = "後測尚未開放";
      return;
    }

    // 2) 開放 → 導到 後測 Parsons 專用路由(B)
    router.replace({
      path: "/posttest/parsons",
      query: { mode: "test", test_role: "post", test_cycle_id },
    });
  } catch (e) {
    err.value = e?.response?.data?.error || e?.message || "載入失敗";
  }
}

onMounted(() => {
  boot();
});
</script>

<style scoped>
.postTestEntry {
  min-height: 60vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.box {
  padding: 24px 28px;
  border: 1px solid #ddd;
  border-radius: 12px;
  background: #fff;
}
.err {
  margin-top: 10px;
  color: #c00;
}
</style>
