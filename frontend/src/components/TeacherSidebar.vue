<template>
  <aside class="sidebar">
    <div class="profile">
      <div class="avatar">👩‍🏫</div>
      <div class="hello">您好，老師</div>
    </div>

    <div class="sidebar-menu">
      <div class="navitem" :class="{ active: active === 'dashboard' }" @click="go('/admin/dashboard')">📚 老師儀錶板總覽</div>
      <div class="navitem" :class="{ active: active === 'upload' }" @click="go('/admin/upload')">📁 影片管理</div>
      <div class="navitem" :class="{ active: active === 'subtitle' }" @click="go('/admin/subtitle')">📝 字幕/逐字稿</div>
      <div class="navitem" :class="{ active: active === 'agentlog' }" @click="go('/admin/agentlog')">🧩 AI 管理生成紀錄檢視</div>
      <div class="navitem" :class="{ active: active === 'analyze' }" @click="go('/admin/analyze')">📊 學生作答紀錄分析</div>
    </div>

    <div class="sidebar-logout">
      <button class="btn ghost logout-btn" @click="logout">登出</button>
    </div>
  </aside>
</template>

<script setup>
import { useRouter } from "vue-router";

defineProps({
  active: {
    type: String,
    default: "",
  },
});

const router = useRouter();

function go(path) {
  router.push(path);
}

function logout() {
  localStorage.removeItem("user");
  localStorage.removeItem("token");
  router.push("/login");
}
</script>

<style scoped>
.sidebar {
  background: #d4b34a;
  border-radius: 16px;
  padding: 18px 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: calc(100vh - 32px);
  font: 1em sans-serif;
}

.profile {
  display: flex;
  align-items: center;
  gap: 10px;
}

.avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.35);
  display: grid;
  place-items: center;
  font-size: 24px;
}

.hello {
  font-weight: 900;
  font-size: 18px;
  color: #111;
}

.sidebar-menu {
  display: grid;
  gap: 8px;
}

.navitem {
  border: none;
  background: transparent;
  text-align: left;
  padding: 10px 10px;
  border-radius: 10px;
  cursor: pointer;
  font-weight: 800;
  color: #1b1b1b;
}

.navitem:hover {
  background: rgba(255, 255, 255, 0.25);
}

.navitem.active {
  background: rgba(255, 255, 255, 0.35);
}

.sidebar-logout {
  margin-top: auto;
}

.btn {
  border: none;
  background: #f2c266;
  padding: 10px 14px;
  border-radius: 10px;
  font-weight: 900;
  cursor: pointer;
}

.btn.ghost.logout-btn {
  width: 100%;
  border: 2px solid #0b2a4a;
  background: #fff;
  color: #111;
}

@media (max-width: 900px) {
  .sidebar {
    min-height: auto;
  }
}
</style>
