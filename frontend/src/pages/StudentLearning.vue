<template>
  <div class="page">
    <header class="topbar">
      <div class="topbar-left">
        <div class="brand-dot"></div>
        <div class="topbar-texts">
          <div class="title">單元列表</div>
          <div class="subtitle">{{ currentUnit }}<span v-if="currentTitle">：{{ currentTitle }}</span></div>
        </div>
      </div>

      <div class="topbar-actions">
        <button class="header-btn header-btn-light" @click="goHome">
          回首頁
        </button>
        <button class="header-btn header-btn-primary" @click="logout">
          登出
        </button>
      </div>
    </header>

    <div class="grid">
      <!-- 左：單元列表 -->
      <aside class="unitNav">
        <div class="sideHeader">
          <div class="sideTitle">課程單元</div>
          <div class="sideHint">點選左側影片即可播放</div>
        </div>

        <div v-for="(u, idx) in units" :key="u.unit" class="unit">
          <button class="unit-header" @click="toggleUnit(idx)">
            <div class="unit-left">
              <span class="chev" :class="{ open: openIndex === idx }">▾</span>
              <span class="unit-title">{{ u.unit }}</span>
            </div>
            <span class="count">{{ u.videos.length }} 部</span>
          </button>

          <div v-show="openIndex === idx" class="videoList">
            <button
              v-for="v in u.videos"
              :key="v._id"
              class="videoItem"
              :class="{ active: selectedVideoId === v._id }"
              @click="selectVideo(u, v)"
            >
              <img v-if="v.thumbnailUrl" class="thumb" :src="v.thumbnailUrl" />
              <div v-else class="thumb thumb-placeholder">影片</div>

              <div class="meta">
                <div class="vtitle">{{ v.title }}</div>
                <div class="vsub"></div>
              </div>
            </button>
          </div>
        </div>
      </aside>

      <!-- 中：影片區 -->
      <section class="videoArea">
        <div class="videoHeader">
          <div>
            <div class="videoLabel">目前影片</div>
            <div class="videoTitleText">{{ currentTitle || '請從左側選擇影片' }}</div>
          </div>
        </div>

        <div v-if="showReturnBar" class="returnBar">
          <div class="returnText">
            你正在回看錯誤片段（{{ segmentLabel }}）
          </div>
          <button class="returnBtn" @click="backToPractice">
            返回練習
          </button>
        </div>

        <video
          ref="player"
          v-if="selectedVideo"
          class="player"
          controls
          :src="selectedVideo.videoUrl"
        ></video>
        <div v-else class="empty">請從左側選擇影片</div>

        <div class="actions">
          <button class="btn" @click="goParsons" :disabled="!selectedVideoId">
            下一步：進入練習
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from "vue";
import { useRouter, useRoute } from "vue-router";
import { computed } from "vue";
import { onBeforeUnmount } from "vue";

const router = useRouter();
const route = useRoute();

// 如果是從測驗頁過來的，會帶 query ?start=xx&end=xx&attempt_id=xxx
// 這時候顯示回看條（return bar），並在影片上標示正在回看的片段區間
const watchStartAt = ref(null);
const reachedEnd = ref(false);
const watchSeconds = ref(0);

let lastCurrentTime = 0;
let autoSaveTimer = null; // ✅【新增】每 5 秒送一次
let hasSentReached = false; // ✅【新增】避免 reached_end 重複送

// ✅【修正】用「影片時間差」累積實際觀看秒數（比 setInterval 穩定、也能避免背景分頁被節流）
let _watchBound = false;
let _lastVideoTime = null; // 上一次 timeupdate 的 currentTime

function _resetWatchAccumulators() {
  _lastVideoTime = null;
  // 注意：watchSeconds / reachedEnd / watchStartAt 是否重置由呼叫端決定
}

function _bindPlayerWatchEvents() {
  const el = player.value;
  if (!el) return;

  // 避免重複綁定
  if (_watchBound) return;
  _watchBound = true;

  el.addEventListener("play", () => {
    if (!watchStartAt.value) watchStartAt.value = new Date().toISOString();
    // 以目前播放位置作為起點
    _lastVideoTime = el.currentTime;

    // ✅【新增】播放時啟動自動送
    if (!autoSaveTimer) {
      autoSaveTimer = setInterval(() => {
        sendWatchLog();
      }, 5000);
    }
  });

  el.addEventListener("pause", () => {
    _lastVideoTime = el.currentTime;
    // ✅【新增】暫停就停止自動送（省資源）
    if (autoSaveTimer) {
      clearInterval(autoSaveTimer);
      autoSaveTimer = null;
    }
  });

  // 使用者拖曳進度條（seek）時，不計入「連續觀看」秒數，直接重設起點
  el.addEventListener("seeking", () => {
    _lastVideoTime = el.currentTime;
  });

  el.addEventListener("ended", () => {
    reachedEnd.value = true;
    _lastVideoTime = null;
    // 看完就先送一次（不等離開頁面）
    sendWatchLog?.();
  });

  el.addEventListener("timeupdate", () => {
    // 1) 累積觀看秒數：以 currentTime 差值為準（更貼近「實際看了幾秒」）
    if (!el.paused) {
      if (_lastVideoTime == null) {
        _lastVideoTime = el.currentTime;
      } else {
        const delta = el.currentTime - _lastVideoTime;
        // delta 太大多半是 seek；太小則忽略
        if (delta > 0 && delta < 2.0) {
          watchSeconds.value = Number(watchSeconds.value || 0) + delta;
        }
        _lastVideoTime = el.currentTime;
      }
    }

    // 2) 回看片段：只要播放到 end，就視為看完
    const end = Number(route.query.end);
    if (Number.isFinite(end) && el.currentTime >= end - 0.15) {
      if (!reachedEnd.value) {
        reachedEnd.value = true;
      }
      if (!hasSentReached) {
        hasSentReached = true;
        sendWatchLog(); // ✅ 到 end 立刻送一次
      }
    }
  });
}

const API_BASE = "http://127.0.0.1:5000";

const units = ref([]);
const openIndex = ref(0);

const selectedVideo = ref(null);
const selectedVideoId = ref(null);

const currentUnit = ref("");
const currentTitle = ref("");

const player = ref(null);

const bullets = ref([
  "（示意）這裡未來可以放 AI 摘要/錯誤原因",
]);

const showReturnBar = computed(() => {
  // 只要有 start 就代表是「回看片段」進來的
  return route.query.start != null;
});

const segmentLabel = computed(() => {
  const s = Number(route.query.start);
  const e = Number(route.query.end);
  if (!Number.isFinite(s)) return "";
  if (!Number.isFinite(e)) return `${Math.floor(s)}s 起`;
  return `${Math.floor(s)}s–${Math.floor(e)}s`;
});

function toggleUnit(idx) {
  openIndex.value = openIndex.value === idx ? -1 : idx;
}

function groupByUnit(videos) {
  const map = new Map();
  for (const v of videos) {
    const key = v.unit || "未分類";
    if (!map.has(key)) map.set(key, { unit: key, videos: [] });
    map.get(key).videos.push(v);
  }
  return [...map.values()].sort((a, b) =>
    a.unit.localeCompare(b.unit, "en", { numeric: true })
  );
}

function selectVideo(u, v) {
  selectedVideo.value = v;
  selectedVideoId.value = v._id || v.id || v.video_id;
  currentUnit.value = u.unit;
  currentTitle.value = v.title;

  // ✅ 每次切換影片都重新綁定一次監聽（新的 <video> 會重新渲染）
  _watchBound = false;
  nextTick(() => {
    _bindPlayerWatchEvents();
  });
}

function goParsons() {
  if (!selectedVideoId.value) {
    alert("請先從左側選擇一部影片");
    return;
  }
  router.push({
    path: `/parsons/${selectedVideoId.value || route.params.videoId}`,
    query: {
      review_attempt_id: String(route.query.attempt_id || ""),
    },
  });
}

function goHome() {
  router.push("/home");
}

function logout() {
  try {
    localStorage.removeItem("student_id");
    localStorage.removeItem("studentId");
    localStorage.removeItem("token");
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    localStorage.removeItem("user");
    sessionStorage.removeItem("token");
    sessionStorage.removeItem("access_token");
  } catch (_) {}
  router.replace("/login");
}

// ✅ 跳到指定片段（確保 video 已載入 metadata）
async function seekToSegment(start, end) {
  if (!Number.isFinite(start)) return;
  await nextTick();

  const el = player.value;
  if (!el) return;

  const doSeek = () => {
    try {
      // ✅【新增】每次開始回看片段前，重置回看統計（避免上一段殘留）
      watchStartAt.value = null;
      reachedEnd.value = false;
      watchSeconds.value = 0;
      _resetWatchAccumulators();

      el.currentTime = start;
      el.play?.();

      if (Number.isFinite(end)) {
        const timer = setInterval(() => {
          if (!player.value) return clearInterval(timer);
          if (player.value.currentTime >= end) {
            player.value.pause();
            clearInterval(timer);
          }
        }, 300);
      }
    } catch (_) {}
  };

  // metadata 還沒好就等一下
  if (el.readyState >= 1) doSeek();
  else el.addEventListener("loadedmetadata", doSeek, { once: true });
}

onMounted(async () => {
  await nextTick();

  // ✅ 注意：影片 <video> 會在選到影片後才出現，所以監聽會在 selectVideo() 裡 nextTick 後綁定

  // 讀 query start/end
  const start = Number(route.query.start);
  const end = Number(route.query.end);

  // 讀影片列表
  const res = await fetch(`${API_BASE}/api/admin_upload/videos`);
  const data = await res.json();
  const list = Array.isArray(data) ? data : data.items || data.videos || [];

  const filtered = list.filter((v) => v.active !== false && v.deleted !== true);

  function resolveFileUrl(p, kind = "") {
    if (!p) return "";
    const s = String(p).trim();

    // 已經是完整網址
    if (/^https?:\/\//i.test(s)) return s;

    // 縮圖特別處理：後端 DB 存 thumbnails/xxx.jpg，但實際可讀路徑是 uploads/thumbnails/xxx.jpg
    if (kind === "thumbnail") {
      if (s.startsWith("/uploads/")) return `${API_BASE}${s}`;
      if (s.startsWith("uploads/")) return `${API_BASE}/${s}`;
      if (s.startsWith("/thumbnails/")) return `${API_BASE}/uploads${s}`;
      if (s.startsWith("thumbnails/")) return `${API_BASE}/uploads/${s}`;
    }

    // 一般檔案
    if (s.startsWith("/")) return `${API_BASE}${s}`;
    return `${API_BASE}/${s}`;
  }

  const normalized = filtered.map((v) => ({
    ...v,
    _id: v._id?.$oid || v._id || v.id,
    videoUrl: resolveFileUrl(v.path || v.videoUrl || v.video_path),
    thumbnailUrl: resolveFileUrl(
      v.thumbnail || v.thumbnailUrl || v.thumbnail_url,
      "thumbnail"
    ),
  }));

  units.value = groupByUnit(normalized);

  // ✅ 方案 B：如果是 /learn/video/:videoId
  const routeVid = route.params.videoId ? String(route.params.videoId) : "";
  if (routeVid) {
    // 找到該影片
    let foundU = null;
    let foundV = null;

    for (let i = 0; i < units.value.length; i++) {
      const u = units.value[i];
      const v = u.videos.find((x) => String(x._id) === routeVid);
      if (v) {
        foundU = u;
        foundV = v;
        openIndex.value = i;
        break;
      }
    }

    if (foundU && foundV) {
      selectVideo(foundU, foundV);
      await seekToSegment(start, end);
      return;
    }
  }

  // ✅ 原本路由：/learn/:unit
  const unitParam = route.params.unit ? String(route.params.unit) : "";
  if (unitParam) {
    const idx = units.value.findIndex((u) => u.unit === unitParam);
    if (idx >= 0 && units.value[idx].videos.length) {
      openIndex.value = idx;
      selectVideo(units.value[idx], units.value[idx].videos[0]);
      // /learn/:unit 也可支援 query start/end（可有可無）
      await seekToSegment(start, end);
      return;
    }
  }

  // fallback：沒有 unit、也找不到 videoId，就選第一部
  if (units.value.length && units.value[0].videos.length) {
    openIndex.value = 0;
    selectVideo(units.value[0], units.value[0].videos[0]);
    await seekToSegment(start, end);
  }
});

async function sendWatchLog() {
  const attemptId = route.query.attempt_id ? String(route.query.attempt_id) : "";
  if (!attemptId) return;

  const vid = selectedVideoId.value
    ? String(selectedVideoId.value)
    : (route.params.videoId ? String(route.params.videoId) : "");

  const payload = {
    attempt_id: attemptId,
    video_id: vid,
    task_id: route.query.task_id ? String(route.query.task_id) : "",
    start_sec: route.query.start != null ? Number(route.query.start) : null,
    end_sec: route.query.end != null ? Number(route.query.end) : null,

    watch_seconds: Math.round(Number(watchSeconds.value || 0)), // ✅【修正】送出整數秒
    reached_end: Boolean(reachedEnd.value),
    watch_start_at: watchStartAt.value,
    watch_end_at: new Date().toISOString(),
  };

  try {
    await fetch(`${API_BASE}/api/parsons/review_watch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    });
  } catch (_) {}
}

onBeforeUnmount(() => {
  if (autoSaveTimer) {
    clearInterval(autoSaveTimer);
    autoSaveTimer = null;
  }
  sendWatchLog();
});

// 返回練習」按鈕 backToPractice() 裡也先送再跳
async function backToPractice() {
  await sendWatchLog();
  router.push({
    path: `/parsons/${selectedVideoId.value || route.params.videoId}`,
    query: {
      review_attempt_id: String(route.query.attempt_id || ""),
    },
  });
}

// ==============================
// ✅【新增】播放監聽主邏輯
// ==============================
function bindVideoWatchEvents() {
  const v = videoRef.value;
  if (!v) return;

  v.addEventListener("play", () => {
    if (!watchStartAt.value) {
      watchStartAt.value = new Date().toISOString();
    }

    lastCurrentTime = v.currentTime;

    // ✅ 啟動每 5 秒自動送一次
    startAutoSave();
  });

  v.addEventListener("pause", () => {
    stopAutoSave();
  });

  v.addEventListener("timeupdate", () => {
    const delta = v.currentTime - lastCurrentTime;

    if (delta > 0 && delta < 5) {
      watchSeconds.value += delta;
    }

    lastCurrentTime = v.currentTime;

    // ✅ 播到 end 秒
    const end = Number(route.query.end || 0);
    if (!hasSentReached && end && v.currentTime >= end - 0.2) {
      reachedEnd.value = true;
      hasSentReached = true;
      sendWatchToServer(); // 立刻送一次
    }
  });
}

// ==============================
// ✅【新增】每 5 秒自動送資料
// ==============================
function startAutoSave() {
  if (autoSaveTimer) return;

  autoSaveTimer = setInterval(() => {
    sendWatchToServer();
  }, 5000);
}

function stopAutoSave() {
  if (autoSaveTimer) {
    clearInterval(autoSaveTimer);
    autoSaveTimer = null;
  }
}

// ==============================
// ✅【新增】送 watch 資料到後端
// ==============================
async function sendWatchToServer() {
  const attempt_id = String(route.query.attempt_id || "");
  if (!attempt_id) return;

  try {
    await fetch(`${API_BASE}/api/parsons/review_watch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        attempt_id,
        watch_seconds: Math.floor(watchSeconds.value),
        reached_end: reachedEnd.value,
        watch_start_at: watchStartAt.value,
        watch_end_at: new Date().toISOString(),
      }),
    });
  } catch (e) {
    console.warn("watch save failed", e);
  }
}

// ==============================
// ✅【新增】關閉頁面前保底
// ==============================
// window.addEventListener("beforeunload", () => {
//   sendWatchToServer();
// });

// onMounted(() => {
//   bindVideoWatchEvents();
// });
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 16px;
  background: linear-gradient(180deg, #f7f8fb 0%, #eef3f7 100%);
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 10px 16px;
  border: 1px solid #d8e1e8;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 10px 26px rgba(46, 72, 98, 0.08);
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.brand-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: linear-gradient(135deg, #4e9b6a, #77bf90);
  flex: 0 0 auto;
}

.topbar-texts {
  min-width: 0;
}

.title {
  font-size: 20px;
  font-weight: 900;
  color: #1f2d3d;
  line-height: 1.2;
}

.subtitle {
  margin-top: 2px;
  font-size: 13px;
  color: #687789;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 auto;
}

.header-btn {
  border: 0;
  border-radius: 999px;
  padding: 10px 16px;
  font-size: 14px;
  font-weight: 900;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
}

.header-btn:hover,
.unit-header:hover,
.videoItem:hover,
.btn:hover,
.returnBtn:hover {
  transform: translateY(-1px);
}

.header-btn-light {
  background: #eef4f7;
  color: #24425d;
}

.header-btn-primary {
  background: linear-gradient(135deg, #f6b334, #f2a312);
  color: #2f2200;
  box-shadow: 0 8px 18px rgba(242, 163, 18, 0.22);
}

.grid {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 16px;
  margin-top: 16px;
  align-items: start;
}

.unitNav,
.videoArea,
.aiArea {
  border: 1px solid #d8e1e8;
  border-radius: 18px;
  padding: 14px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 12px 28px rgba(46, 72, 98, 0.08);
}

.unitNav {
  overflow: auto;
  max-height: calc(100vh - 120px);
}

.sideHeader {
  margin-bottom: 12px;
}

.sideTitle {
  font-size: 17px;
  font-weight: 900;
  color: #1f2d3d;
}

.sideHint {
  margin-top: 4px;
  font-size: 12px;
  color: #7a8796;
}

.unit {
  margin-bottom: 10px;
}

.unit-header {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid #d7dee6;
  background: #f9fbfc;
  cursor: pointer;
}

.unit-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chev {
  transition: transform 0.2s ease;
}

.chev.open {
  transform: rotate(180deg);
}

.unit-title {
  font-weight: 800;
  color: #223446;
}

.count {
  font-size: 13px;
  font-weight: 800;
  color: #688198;
}

.videoList {
  margin: 10px 0 0;
  display: grid;
  gap: 8px;
}

.videoItem {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px;
  border-radius: 14px;
  border: 1px solid #e1e7ed;
  background: #ffffff;
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.videoItem.active {
  border-color: #4e9b6a;
  box-shadow: 0 0 0 3px rgba(78, 155, 106, 0.12);
  background: #f5fbf7;
}

.thumb {
  width: 72px;
  height: 44px;
  object-fit: cover;
  border-radius: 10px;
  flex: 0 0 auto;
  background: #edf1f5;
}

.thumb-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #7b8795;
  font-size: 12px;
  font-weight: 700;
}

.meta {
  min-width: 0;
}

.vtitle {
  font-weight: 800;
  color: #1f2d3d;
  line-height: 1.4;
  word-break: break-word;
}

.vsub {
  margin-top: 2px;
  min-height: 16px;
}

.videoHeader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.videoLabel {
  font-size: 12px;
  font-weight: 800;
  color: #6d7b8b;
}

.videoTitleText {
  margin-top: 4px;
  font-size: 20px;
  font-weight: 900;
  color: #1d2f42;
  word-break: break-word;
}

.player {
  width: 100%;
  max-height: 64vh;
  background: #000;
  border-radius: 18px;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.08);
}

.actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
}

.btn {
  padding: 12px 22px;
  border-radius: 999px;
  border: 0;
  background: linear-gradient(135deg, #4e9b6a, #5cad78);
  color: #fff;
  font-weight: 900;
  font-size: 15px;
  cursor: pointer;
  box-shadow: 0 10px 22px rgba(78, 155, 106, 0.2);
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}

.empty {
  padding: 48px 20px;
  text-align: center;
  color: #6f7d8d;
  border: 1px dashed #d4dce4;
  border-radius: 18px;
  background: #f9fbfc;
}

.pill {
  font-size: 12px;
  opacity: 0.75;
}

/* 回看錯誤片段 */
.returnBar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid #f1d187;
  margin-bottom: 12px;
  background: linear-gradient(180deg, #fff9e8 0%, #fff4cf 100%);
}

.returnText {
  font-weight: 900;
  color: #5f4900;
}

.returnBtn {
  border: 0;
  border-radius: 999px;
  padding: 10px 14px;
  background: #4e9b6a;
  color: #fff;
  font-weight: 900;
  cursor: pointer;
  box-shadow: 0 8px 18px rgba(78, 155, 106, 0.16);
}

@media (max-width: 1024px) {
  .grid {
    grid-template-columns: 1fr;
  }

  .unitNav {
    max-height: none;
  }
}

@media (max-width: 720px) {
  .page {
    padding: 12px;
  }

  .topbar {
    flex-direction: column;
    align-items: stretch;
  }

  .topbar-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .title {
    font-size: 18px;
  }

  .videoTitleText {
    font-size: 18px;
  }

  .returnBar {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
