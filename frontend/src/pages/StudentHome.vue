<template>
  <div class="homePage">
    <!-- 上方資訊列 -->
    <header class="topBar">
      <div class="leftProfile">
        <button
          class="avatar"
          :class="`avatar-${studentSexj || 'unknown'}`"
          type="button"
          :aria-label="`${avatarLabel}，點擊更換頭像`"
          @click="openAvatarModal"
        >
          <span class="avatarMedia">
            <img
              v-if="currentAvatarImageUrl"
              class="avatarImg"
              :src="currentAvatarImageUrl"
              alt=""
            />
            <span v-else class="avatarSymbol" aria-hidden="true">{{ currentAvatarEmoji }}</span>
          </span>
          <span class="avatarEditIcon" aria-hidden="true">✎</span>
        </button>

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

    <div v-if="avatarModalOpen" class="modalBackdrop" @click.self="closeAvatarModal">
      <section class="avatarModal" role="dialog" aria-modal="true" aria-labelledby="avatar-modal-title">
        <header class="avatarModalHeader">
          <h2 id="avatar-modal-title">選擇頭像</h2>
          <button
            class="modalCloseButton"
            type="button"
            aria-label="關閉頭像選擇"
            :disabled="avatarSaving"
            @click="closeAvatarModal"
          >
            ×
          </button>
        </header>

        <div class="avatarModalBody">
          <div v-if="avatarError" class="avatarError">{{ avatarError }}</div>
          <div v-if="avatarOptionsLoading" class="avatarLoading">載入頭像...</div>

          <template v-else>
            <section
              v-for="group in avatarGroups"
              :key="group.group_label"
              class="avatarSection"
            >
              <h3>{{ group.group_label }}</h3>
              <div class="avatarOptions">
                <button
                  v-for="option in group.avatars"
                  :key="option.avatar_key"
                  class="avatarOption"
                  :class="{ selected: option.avatar_key === avatarKey }"
                  type="button"
                  :disabled="avatarSaving"
                  @click="selectAvatar(option.avatar_key)"
                >
                  <span
                    v-if="option.avatar_type === 'emoji'"
                    class="avatarOptionEmoji"
                    aria-hidden="true"
                  >
                    {{ option.avatar_src }}
                  </span>
                  <img v-else :src="avatarAssetUrl(option.avatar_src)" alt="" />
                  <span>{{ option.label }}</span>
                </button>
              </div>
            </section>
          </template>
        </div>

        <footer class="avatarModalFooter">
          頭像樣式由 DiceBear 提供。部分樣式採用 CC0 1.0 授權；Bottts 和 Avataaars 的樣式可免費用於個人和商業用途。
        </footer>
      </section>
    </div>

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
import { logoutCurrentSession } from "../sessionAuth";

// ✅ 固定打後端（避免相對路徑打到 Vite 5173 回傳 index.html => <!doctype html>）
const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000";

const router = useRouter();

// ==============================
// 基本資訊（維持原本畫面用）
// ==============================
const studentName = ref("");
const studentSexj = ref(null);
const avatarType = ref(null);
const avatarKey = ref(null);
const avatarSrc = ref(null);
const avatarGroups = ref([]);
const avatarModalOpen = ref(false);
const avatarOptionsLoading = ref(false);
const avatarSaving = ref(false);
const avatarError = ref("");
const currentAvatarEmoji = computed(() => {
  if (avatarType.value === "emoji" && ["👨", "👩"].includes(avatarSrc.value)) {
    return avatarSrc.value;
  }
  return "👨";
});
const currentAvatarImageUrl = computed(() => (
  avatarType.value === "image" ? avatarAssetUrl(avatarSrc.value) : ""
));
const avatarLabel = computed(() => {
  if (currentAvatarImageUrl.value) return "學生圖片頭像";
  if (currentAvatarEmoji.value === "👩") return "女學生頭像";
  if (currentAvatarEmoji.value === "👨") return "男學生頭像";
  return "學生頭像";
});

function avatarAssetUrl(value) {
  const path = String(value || "").trim();
  const allowedPath = /^\/static\/avatars\/(?:bot_0[1-6]|bottts(?:_neutral)?_0[1-6]|initial_face_0[1-6]|lorelei(?:_neutral)?_0[1-6]|notionists(?:_neutral)?_0[1-6]|avataaars(?:_neutral)?_0[1-6])\.svg$/;
  if (!allowedPath.test(path)) return "";
  return `${API_BASE.replace(/\/$/, "")}${path}`;
}

function applyAvatar(avatar) {
  avatarType.value = avatar?.avatar_type || null;
  avatarKey.value = avatar?.avatar_key || null;
  avatarSrc.value = (
    avatar?.avatar_src || avatar?.avatar_url || avatar?.avatar_value || null
  );
}

async function loadAvatarOptions() {
  avatarOptionsLoading.value = true;
  avatarError.value = "";
  try {
    const response = await fetch(`${API_BASE}/api/avatars`);
    const data = await response.json();
    if (!response.ok || !data?.ok) throw new Error(data?.message || "載入頭像失敗");
    avatarGroups.value = Array.isArray(data.groups) ? data.groups : [];
  } catch (error) {
    avatarError.value = error?.message || "載入頭像失敗";
  } finally {
    avatarOptionsLoading.value = false;
  }
}

async function openAvatarModal() {
  avatarModalOpen.value = true;
  avatarError.value = "";
  if (!avatarGroups.value.length) await loadAvatarOptions();
}

function closeAvatarModal() {
  if (avatarSaving.value) return;
  avatarModalOpen.value = false;
  avatarError.value = "";
}

async function selectAvatar(selectedAvatarKey) {
  const token = localStorage.getItem("token") || "";
  if (!token) {
    avatarError.value = "登入資訊已失效，請重新登入。";
    return;
  }

  avatarSaving.value = true;
  avatarError.value = "";
  try {
    const response = await fetch(`${API_BASE}/api/users/me/avatar`, {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ avatar_key: selectedAvatarKey }),
    });
    const data = await response.json();
    if (!response.ok || !data?.ok) throw new Error(data?.message || "更新頭像失敗");
    applyAvatar(data.avatar);
    avatarModalOpen.value = false;
  } catch (error) {
    avatarError.value = error?.message || "更新頭像失敗";
  } finally {
    avatarSaving.value = false;
  }
}

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
      total_videos: Number(u.total_videos || 0),
      theme: themePool[idx % themePool.length],
    })).filter((u) => u.total_videos > 0);

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

async function logout() {
  await logoutCurrentSession(API_BASE);
  router.replace("/login");
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

  try {
    const profileRes = await fetch(
      `${API_BASE}/api/student/profile?student_id=${encodeURIComponent(id)}`,
    );
    const profileData = await profileRes.json();
    if (profileData?.ok && profileData.student) {
      studentName.value = profileData.student.name || id;
      studentSexj.value = profileData.student.sexj || null;
      applyAvatar(profileData.student);
    }
  } catch (error) {
    console.warn("student profile load failed:", error);
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
  position: relative;
  width: 62px;
  height: 62px;
  padding: 0;
  border-radius: 50%;
  overflow: visible;
  background: #e2e8f0;
  display: grid;
  place-items: center;
  box-shadow: 0 8px 18px rgba(71, 85, 105, 0.18);
  border: 3px solid rgba(255, 255, 255, 0.8);
  cursor: pointer;
  flex: 0 0 62px;
  transition: box-shadow 160ms ease, transform 160ms ease;
}

.avatar:hover {
  box-shadow: 0 10px 22px rgba(20, 108, 100, 0.24);
  transform: translateY(-1px);
}

.avatar:focus-visible {
  outline: 3px solid rgba(20, 108, 100, 0.35);
  outline-offset: 3px;
}

.avatar-boy {
  background: #dbeafe;
  box-shadow: 0 8px 18px rgba(37, 99, 166, 0.2);
}

.avatar-girl {
  background: #fce7f3;
  box-shadow: 0 8px 18px rgba(190, 24, 93, 0.18);
}

.avatar-other,
.avatar-unknown {
  background: #e2e8f0;
}

.avatarSymbol {
  font-size: 34px;
  line-height: 1;
}

.avatarMedia {
  display: grid;
  width: 100%;
  height: 100%;
  overflow: hidden;
  border-radius: 50%;
  place-items: center;
  background: inherit;
}

.avatarImg {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatarEditIcon {
  position: absolute;
  z-index: 2;
  right: -6px;
  bottom: -5px;
  display: grid;
  width: 20px;
  height: 20px;
  border: 3px solid #fff;
  border-radius: 50%;
  place-items: center;
  color: #fff;
  background: #0f766e;
  box-shadow: 0 3px 8px rgba(15, 23, 42, 0.3);
  font-size: 15px;
  font-weight: 900;
  line-height: 1;
}

.modalBackdrop {
  position: fixed;
  z-index: 1000;
  inset: 0;
  display: grid;
  padding: 20px;
  place-items: center;
  background: rgba(15, 23, 42, 0.48);
}

.avatarModal {
  display: flex;
  width: min(560px, 92vw);
  max-height: 80vh;
  overflow: hidden;
  box-sizing: border-box;
  border: 1px solid #d8dee9;
  border-radius: 8px;
  padding: 0;
  flex-direction: column;
  background: #fff;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.2);
}

.avatarModalHeader {
  display: flex;
  padding: 20px 24px 12px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.avatarModalHeader h2 {
  margin: 0;
  color: #172033;
  font-size: 20px;
}

.modalCloseButton {
  display: grid;
  width: 34px;
  height: 34px;
  border: 1px solid #cbd5e1;
  border-radius: 50%;
  place-items: center;
  color: #475569;
  background: #fff;
  cursor: pointer;
  font-size: 22px;
  line-height: 1;
}

.modalCloseButton:disabled,
.avatarOption:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.avatarModalBody {
  max-height: 60vh;
  overflow-y: auto;
  padding: 0 24px 16px;
  flex: 1 1 auto;
}

.avatarSection {
  margin-top: 18px;
}

.avatarSection h3 {
  margin: 0 0 10px;
  color: #334155;
  font-size: 14px;
}

.avatarOptions {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));
  gap: 10px;
}

.avatarOption {
  display: flex;
  height: 104px;
  min-width: 0;
  box-sizing: border-box;
  border: 1px solid #d8dee9;
  border-radius: 8px;
  padding: 10px 8px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #475569;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
}

.avatarOption:hover {
  border-color: #72b6ad;
  background: #f0fdfa;
}

.avatarOption.selected {
  border-color: #146c64;
  box-shadow: 0 0 0 2px rgba(20, 108, 100, 0.16);
  background: #ecfdf5;
}

.avatarOptionEmoji {
  font-size: 44px;
  line-height: 1;
}

.avatarOption img {
  width: 56px;
  height: 56px;
  object-fit: contain;
}

.avatarModalFooter {
  border-top: 1px solid #e5e7eb;
  padding: 10px 24px 16px;
  flex: 0 0 auto;
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.avatarError {
  margin-top: 12px;
  border: 1px solid #fecaca;
  border-radius: 6px;
  padding: 9px 10px;
  color: #991b1b;
  background: #fee2e2;
  font-size: 13px;
  font-weight: 700;
}

.avatarLoading {
  display: grid;
  min-height: 180px;
  place-items: center;
  color: #64748b;
  font-size: 14px;
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

@media (max-width: 640px) {
  .modalBackdrop {
    padding: 10px;
  }

  .avatarModal {
    width: 94vw;
    max-height: 86vh;
  }

  .avatarModalHeader {
    padding: 16px 16px 10px;
  }

  .avatarModalBody {
    max-height: 66vh;
    padding: 0 16px 14px;
  }

  .avatarModalFooter {
    padding: 10px 16px 14px;
  }

  .avatarOptions {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
