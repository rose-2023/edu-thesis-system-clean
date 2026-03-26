<template>
  <div class="homePage">
    <!-- 上方資訊列 -->
    <header class="topBar">
      <div class="leftProfile">
        <div class="avatar">
          <img
            v-if="avatarUrl"
            :src="avatarUrl"
            alt="avatar"
            class="avatarImg"
          />
          <div v-else class="avatarPlaceholder"></div>
        </div>

        <div class="profileText">
          <div class="hello">你好，{{ studentName }}同學</div>
          <div class="welcomeText">歡迎回來，今天也一起完成學習進度吧！</div>
        </div>
      </div>

      <div class="rightTools">
        <div class="rightProgress cardPanel">
          <div class="progressTitle">
            未完成課程：<span>{{ ongoingUnit }}</span> Parsons
          </div>
          <div class="dots">
            <span
              v-for="i in totalDots"
              :key="i"
              class="dot"
              :class="{ on: i <= doneDots }"
            ></span>
          </div>

          <!-- ✅【新增】後測進度點點：與老師端「後測發布/取消發布」連動（同一份 test_control） -->
          <div class="postProgress">
            <div class="progressTitle">
              後測：<span>{{ postOpen ? (postDone ? "已完成" : "進行中") : "未開放" }}</span>
            </div>
            <div class="dots postDots" v-if="postOpen">
              <span
                v-for="i in postTotalDots"
                :key="'post-dot-' + i"
                class="dot"
                :class="{ on: i <= postDoneDots }"
              ></span>
            </div>
          </div>
        </div>

        <button class="logoutBtn" @click="logout">
          登出
        </button>
      </div>
    </header>

    <!-- 單元列表 -->
    <main class="main">
      <div class="pageIntro">
        <h1 class="title">單元列表</h1>
        <p class="subtitle">請選擇要進入的學習單元，依序完成影片學習與 Parsons 練習。</p>
      </div>

      <div class="unitGrid">
        <!-- ✅【新增】後測區塊：由 test_control 控制顯示/隱藏 -->
        <section
          v-if="postOpen"
          class="unitCard post"
        >
          <div class="cardGlow"></div>

          <div class="unitHeader">
            <div class="unitBadge">測驗</div>
            <div class="unitName">後測 Parsons</div>
          </div>

          <div class="unitFooter">
            <div class="progressText">
              {{ postDone ? "已完成" : "已開啟" }}
            </div>

            <!-- ✅【新增】後測卡片內的進度點點（與上方 header 的後測點點同一套資料） -->
            <div class="dots postDotsInline">
              <span
                v-for="i in postTotalDots"
                :key="'post-card-dot-' + i"
                class="dot"
                :class="{ on: i <= postDoneDots }"
              ></span>
            </div>

            <button
              class="enterBtn"
              :disabled="postDone"
              @click="goPostTest"
            >
              {{ postDone ? "完成" : "進入" }}
            </button>
          </div>

          <div class="progressBar">
            <div
              class="progressFill"
              :style="{ width: postDone ? '100%' : '0%' }"
            ></div>
          </div>
        </section>

        <section
          v-for="u in units"
          :key="u.unit"
          class="unitCard"
          :class="u.theme"
        >
          <div class="cardGlow"></div>

          <div class="unitHeader">
            <div class="unitBadge">課程單元</div>
            <div class="unitName">{{ u.name }}</div>
          </div>

          <div class="unitFooter">
            <div class="progressText">進度 {{ u.progress }}%</div>

            <button class="enterBtn" @click="goUnit(u.unit)">
              進入
            </button>
          </div>

          <div class="progressBar">
            <div class="progressFill" :style="{ width: u.progress + '%' }"></div>
          </div>
        </section>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import axios from "axios";

// ✅ 固定打後端（避免相對路徑打到 Vite 5173 回傳 index.html => <!doctype html>）
const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000";

const router = useRouter();

// ==============================
// 基本資訊（維持原本畫面用）
// ==============================
const avatarUrl = ref("");
const studentName = ref("");

// ==============================
// 單元列表（動態）
// ==============================
const units = ref([]);

// 右上角「進度點點」
const totalDots = ref(12);
const doneDots = ref(0);

// 顯示未完成課程（沿用舊 UI 字串）
// 若你想更精準，可由後端回 current_unit / current_video 再組字
const ongoingUnit = ref("U1-2 Parsons");

// ==============================
// 後測卡片（test_control）
// ==============================
const postOpen = ref(false);
const postDoneCount = ref(0);
const postTotalCount = ref(0);

const postTotalDots = computed(() => {
  // 若後端有回 total，就用 total；否則保持 12
  const t = Number(postTotalCount.value || 0);
  return t > 0 ? t : 12;
});

const postDoneDots = computed(() => {
  const t = postTotalDots.value;
  const d = Math.max(0, Math.min(Number(postDoneCount.value || 0), t));
  return d;
});

const postDone = computed(() => {
  const total = Number(postTotalCount.value || 0);
  const done = Number(postDoneCount.value || 0);
  return total > 0 && done >= total;
});

// ==============================
// API：載入首頁資料（單元進度 + 後測狀態）
// ==============================
function normalizeUnitPrefix(rawUnit) {
  const raw = String(rawUnit || "").trim();
  const m = raw.match(/^(U\d+)/i);
  return m ? m[1].toUpperCase() : raw.toUpperCase();
}

function unitDisplayName(rawUnit) {
  const raw = String(rawUnit || "").trim();
  const prefix = normalizeUnitPrefix(raw);
  const m = raw.match(/^(U\d+)(?:[-_ ]*([A-Za-z]+))?(.*)$/i);
  const subTag = (m?.[2] || "").toLowerCase();
  const tail = String(m?.[3] || "").trim();

  // 先保留同一主單元下的子題型差異（但不顯示 Ux- 代碼）
  if (prefix === "U3") {
    if (subTag === "for") return "for 迴圈";
    if (subTag === "loop") return "迴圈觀念解析";
  }
  if (prefix === "U2") {
    if (subTag === "if") return "if 條件判斷";
    if (subTag === "ifelse") return "if-else 條件判斷";
    if (subTag === "elif") return "elif 條件判斷";
  }
  if (prefix === "U1" && subTag === "io") {
    return "輸入輸出";
  }

  // 若原字串含中文尾綴（例如 U7-Function函數觀念解析），優先用中文尾綴
  if (tail) {
    const cleanTail = tail.replace(/^[-_\s]+/, "").trim();
    if (cleanTail) return cleanTail;
  }

  const nameMap = {
    U1: "輸入輸出",
    U2: "條件判斷",
    U3: "迴圈觀念解析",
    U4: "巢狀迴圈",
    U5: "不定數迴圈",
    U6: "串列與字典",
    U7: "函數觀念解析",
  };
  return nameMap[prefix] || String(rawUnit || "").trim();
}

async function loadHomeData() {
  try {
    const student_id =
      localStorage.getItem("student_id") ||
      localStorage.getItem("studentId") ||
      "";

    const { data } = await axios.get(`${API_BASE}/api/student/units_progress`, {
      params: {
        student_id,
        test_cycle_id: "default",
      },
    });

    if (!data?.ok) {
      console.error("units_progress not ok:", data);
      return;
    }

    // 1) units
    // 後端會回：[{unit, progress, total_videos, done_videos}]
    const themePool = ["blue", "green", "purple", "orange"];

    units.value = (data.units || []).map((u, idx) => ({
      unit: u.unit,
      name: unitDisplayName(u.unit),
      progress: Number(u.progress || 0),
      theme: themePool[idx % themePool.length],
    }));

    const firstOngoing = units.value.find((u) => Number(u.progress || 0) < 100);
    ongoingUnit.value = firstOngoing ? firstOngoing.name : "已完成";

    // 2) 點點（用平均 progress 估算）
    if (units.value.length > 0) {
      const avg =
        units.value.reduce((sum, u) => sum + Number(u.progress || 0), 0) /
        units.value.length;
      doneDots.value = Math.round((avg / 100) * totalDots.value);
    } else {
      doneDots.value = 0;
    }

    // 3) posttest 狀態（同一份 test_control）
    postOpen.value = !!data.posttest?.post_open;
    postDoneCount.value = Number(data.posttest?.done || 0);
    postTotalCount.value = Number(data.posttest?.total || 0);
  } catch (e) {
    console.error("loadHomeData error:", e);
  }
}

// ==============================
// 互動：進入單元 / 後測 / 登出
// ==============================
function goUnit(unit) {
  // 保留你原本的進入單元邏輯（這裡只示範：導到 /unit/:unit）
  // 如果你原本是點「進入」就進到該單元第一支影片 / learning，請把這段改回你的舊邏輯。
  router.push({ name: "StudentLearning", params: { unit } });
}

function goPostTest() {
  // [修改] 後測 Parsons 走專用路由(B)，不再導到選擇題 Quiz
  router.push({
    path: "/posttest/parsons",
    query: { mode: "test", test_role: "post", test_cycle_id: "default" },
  });
}

function logout() {
  localStorage.removeItem("student_id");
  localStorage.removeItem("studentId");
  localStorage.removeItem("token");
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  router.replace("/");
}

onMounted(async () => {
  const id = localStorage.getItem("student_id") || localStorage.getItem("studentId");
  if (!id) return;

  // [新增] 進首頁先檢查「前測是否必做」：未完成就先導去前測
  try {
    const sres = await fetch(`${API_BASE}/api/parsons/test/status?student_id=${encodeURIComponent(id)}&test_cycle_id=default`);
    const sdata = await sres.json();
    if (sdata?.ok && sdata?.pre_open && !sdata?.pre_done) {
      router.replace({
        path: "/posttest/parsons",
        query: { mode: "test", test_role: "pre", test_cycle_id: "default" },
      });
      return;
    }
  } catch (e) {
    console.warn("pretest status check failed:", e);
  }

  // [修改] 用 API_BASE，避免打到錯的 host/port
  const res = await fetch(`${API_BASE}/api/records/students?page=1&page_size=100`);
  const data = await res.json();

  if (data.ok) {
    const found = data.students.find((s) => s.student_id === id);
    if (found) {
      studentName.value = found.name;
    }
  }

  loadHomeData();
});
</script>

<style scoped>
.homePage {
  min-height: 100vh;
  background:
    radial-gradient(circle at top left, rgba(255, 223, 186, 0.28), transparent 26%),
    linear-gradient(180deg, #fffdf9 0%, #f7f8fc 100%);
}

/* 上方資訊列 */
.topBar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 26px 36px;
  border-bottom: 1px solid rgba(15, 23, 42, 0.08);
  background: rgba(255, 255, 255, 0.84);
  backdrop-filter: blur(10px);
}

.leftProfile {
  display: flex;
  align-items: center;
  gap: 16px;
}

.avatar {
  width: 62px;
  height: 62px;
  border-radius: 50%;
  overflow: hidden;
  background: #fde2d1;
  display: grid;
  place-items: center;
  box-shadow: 0 8px 18px rgba(246, 178, 74, 0.2);
  border: 3px solid rgba(255, 255, 255, 0.8);
}

.avatarImg {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatarPlaceholder {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.15);
}

.profileText .hello {
  font-weight: 900;
  font-size: 22px;
  color: #111827;
}

.welcomeText {
  margin-top: 6px;
  color: #6b7280;
  font-size: 14px;
  font-weight: 700;
}

.rightTools {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.cardPanel {
  min-width: 360px;
  text-align: left;
  padding: 18px 20px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 14px 32px rgba(15, 23, 42, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.85);
}

.progressTitle {
  font-weight: 900;
  margin-bottom: 10px;
  color: #111827;
}

.progressTitle span {
  color: #0b6aa6;
}

.dots {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.dot {
  width: 14px;
  height: 14px;
  border-radius: 5px;
  border: 1.5px solid #334155;
  background: transparent;
}

.dot.on {
  background: #0f172a;
}

.postProgress {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed rgba(15, 23, 42, 0.12);
}

.logoutBtn {
  border: 0;
  min-width: 96px;
  height: 48px;
  padding: 0 18px;
  border-radius: 999px;
  font-size: 15px;
  font-weight: 900;
  cursor: pointer;
  background: linear-gradient(135deg, #f6b24a, #f59e0b);
  color: #3f2a00;
  box-shadow: 0 10px 20px rgba(245, 158, 11, 0.22);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.logoutBtn:hover {
  transform: translateY(-2px);
  box-shadow: 0 14px 24px rgba(245, 158, 11, 0.28);
}

/* 主內容 */
.main {
  padding: 34px 36px 42px;
}

.pageIntro {
  margin-bottom: 26px;
}

.title {
  font-size: 54px;
  margin: 0 0 10px;
  font-weight: 1000;
  letter-spacing: 1px;
  color: #111827;
}

.subtitle {
  margin: 0;
  color: #6b7280;
  font-size: 16px;
  font-weight: 700;
}

/* 單元卡片 */
.unitGrid {
  display: flex;
  gap: 30px;
  flex-wrap: wrap;
}

.unitCard {
  width: 360px;
  min-height: 190px;
  border-radius: 28px;
  padding: 22px 22px 18px;
  position: relative;
  overflow: hidden;
  box-shadow: 0 16px 32px rgba(15, 23, 42, 0.08);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.unitCard:hover {
  transform: translateY(-4px);
  box-shadow: 0 22px 40px rgba(15, 23, 42, 0.12);
}

.cardGlow {
  position: absolute;
  top: -48px;
  right: -28px;
  width: 120px;
  height: 120px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.15);
}

.unitHeader {
  position: relative;
  z-index: 1;
}

.unitBadge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 76px;
  height: 30px;
  padding: 0 12px;
  border-radius: 999px;
  margin-bottom: 12px;
  background: rgba(255, 255, 255, 0.22);
  color: rgba(255, 255, 255, 0.95);
  font-size: 13px;
  font-weight: 900;
  letter-spacing: 0.5px;
}

.unitHeader .unitName {
  color: #ffffff;
  font-weight: 1000;
  font-size: 28px;
  line-height: 1.25;
  word-break: break-word;
}

/* 底部：進度文字 + 進入按鈕 */
.unitFooter {
  position: absolute;
  left: 22px;
  right: 22px;
  bottom: 46px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.progressText {
  color: #ffffff;
  font-weight: 900;
  font-size: 18px;
}

.enterBtn {
  border: 0;
  padding: 10px 20px;
  border-radius: 999px;
  font-weight: 1000;
  font-size: 15px;
  cursor: pointer;
  background: #f6b24a;
  color: #333;
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.12);
  transition: transform 0.18s ease, opacity 0.18s ease;
}

.enterBtn:hover:not(:disabled) {
  transform: translateY(-2px);
}

.enterBtn:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

/* 進度條 */
.progressBar {
  position: absolute;
  left: 22px;
  right: 22px;
  bottom: 18px;
  height: 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.4);
  overflow: hidden;
}

.progressFill {
  height: 100%;
  border-radius: 999px;
  background: #f6b24a;
}

/* 顏色主題 */
.unitCard.blue {
  background: linear-gradient(135deg, #0b6aa6 0%, #0f86cf 100%);
}

.unitCard.green {
  background: linear-gradient(135deg, #5fa371 0%, #74bf84 100%);
}

.unitCard.purple {
  background: linear-gradient(135deg, #7b61c9 0%, #9a84e6 100%);
}

.unitCard.orange {
  background: linear-gradient(135deg, #d9893b 0%, #f2a14f 100%);
}

/* 後測卡片 */
.postDotsInline {
  flex: 1;
  display: flex;
  justify-content: center;
  gap: 6px;
}

.postDotsInline .dot {
  width: 10px;
  height: 10px;
}

.postDotsInline .dot.on {
  background: #111;
}

.postDots .dot {
  width: 10px;
  height: 10px;
}

.unitCard.post {
  background: linear-gradient(135deg, #f8fafc 0%, #eef2f7 100%);
}

.unitCard.post .cardGlow {
  background: rgba(148, 163, 184, 0.18);
}

.unitCard.post .unitBadge {
  background: rgba(148, 163, 184, 0.18);
  color: #334155;
}

.unitCard.post .unitName,
.unitCard.post .progressText {
  color: #1f2937;
}

.unitCard.post .progressBar {
  background: rgba(148, 163, 184, 0.28);
}

.unitCard.post .progressFill {
  background: #334155;
}

/* RWD */
@media (max-width: 900px) {
  .topBar {
    gap: 18px;
    flex-direction: column;
    align-items: stretch;
  }

  .rightTools {
    width: 100%;
    flex-direction: column;
    align-items: stretch;
  }

  .cardPanel {
    min-width: auto;
  }

  .logoutBtn {
    width: 100%;
  }

  .main {
    padding: 28px 20px 36px;
  }

  .topBar {
    padding: 22px 20px;
  }

  .title {
    font-size: 40px;
  }

  .unitCard {
    width: min(420px, 100%);
  }
}
</style>
