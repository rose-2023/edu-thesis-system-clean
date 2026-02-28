<template>
  <div class="page">
    <header class="topbar">
      <div class="title">單元列表</div>
      <div class="current">{{ currentUnit }}：{{ currentTitle }}</div>
    </header>

    <div class="grid">
      <!-- 左：單元列表 -->
      <aside class="unitNav">
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
              <div class="meta">
                <div class="vtitle">{{ v.title }}</div>
                <div class="vsub">
                  <span class="pill">id: {{ v._id }}</span>
                </div>
              </div>
            </button>
          </div>
        </div>
      </aside>

      <!-- 中：影片區 -->
      <section class="videoArea">
        <video
          ref="player"
          v-if="selectedVideo"
          class="player"
          controls
          :src="selectedVideo.videoUrl"
        ></video>
        <div v-else class="empty">請從左側選擇影片</div>

        <div v-if="showReturnBar" class="returnBar">
        <div class="returnText">
          你正在回看錯誤片段（{{ segmentLabel }}）
        </div>
        <button class="returnBtn" @click="backToPractice">
          返回練習
        </button>
      </div>


        <div class="actions">
          <button class="btn" @click="goParsons" :disabled="!selectedVideoId">
            下一步：進入練習
          </button>
        </div>
      </section>

      <!-- 右：AI 區（先保留） -->
      <section class="aiArea">
        <h3>AI 重點回顧（示意）</h3>
        <ul>
          <li v-for="(b, i) in bullets" :key="i">{{ b }}</li>
        </ul>
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
// const unit = route.params.unit;

// 如果是從測驗頁過來的，會帶 query ?start=xx&end=xx&attempt_id=xxx
// 這時候顯示回看條（return bar），並在影片上標示正在回看的片段區間
const watchStartAt = ref(null);
const reachedEnd = ref(false);
const watchSeconds = ref(0);
const seekEvents = ref([]);  // V1.7: 記錄 seek 事件
      
  
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

  // V1.7: 使用者拖曳進度條（seek）時，記錄 seek 事件
  let seekStartTime = null;
  el.addEventListener("seeking", () => {
    seekStartTime = el.currentTime;
    _lastVideoTime = el.currentTime;
  });

  el.addEventListener("seeked", () => {
    // 記錄 seek 距離
    if (seekStartTime !== null) {
      const seekEndTime = el.currentTime;
      const seekDistance = Math.abs(seekEndTime - seekStartTime);
      
      if (seekDistance > 0.5) {  // 只記錄大於 0.5 秒的 seek
        seekEvents.value.push({
          from: Math.round(seekStartTime * 100) / 100,
          to: Math.round(seekEndTime * 100) / 100,
          distance: Math.round(seekDistance * 100) / 100,
          timestamp: new Date().toISOString()
        });
      }
      seekStartTime = null;
    }
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

  const normalized = filtered.map((v) => ({
    ...v,
    _id: v._id?.$oid || v._id || v.id,
    videoUrl: v.path ? `${API_BASE}/${v.path}` : "",
    thumbnailUrl: v.thumbnail ? `${API_BASE}/${v.thumbnail}` : "",
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
    
    // V1.7: 新增 seek 事件追蹤
    seek_events: seekEvents.value,
    seek_count: seekEvents.value.length,
  };

  try {
    const resp = await fetch(`${API_BASE}/api/parsons/review_watch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    });
    const data = await resp.json();
    if (data.ok && data.stats) {
      console.log("✅ 回看記錄已保存:", data.stats);
    }
  } catch (e) {
    console.warn("failed to save watch log:", e);
  }
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
  const vid = String(route.params.videoId || "");
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
.page { padding: 16px; }
.topbar { display:flex; justify-content:space-between; align-items:center; padding: 10px 12px; border:2px solid #000; border-radius:12px; }
.grid { display:grid; grid-template-columns: 320px 1fr 360px; gap: 14px; margin-top: 14px; }
.unitNav { border:2px solid #000; border-radius:12px; padding:10px; overflow:auto; max-height: 75vh; }
.unit-header { width:100%; display:flex; justify-content:space-between; align-items:center; padding:10px; border-radius:10px; border:1px solid #ddd; background:#fff; cursor:pointer; }
.videoList { margin:10px 0 0; display:grid; gap:8px; }
.videoItem { display:flex; gap:10px; align-items:center; padding:10px; border-radius:12px; border:1px solid #e1e1e1; background:#fff; cursor:pointer; text-align:left; }
.videoItem.active { outline:2px solid #4e9b6a; }
.thumb { width:72px; height:44px; object-fit:cover; border-radius:8px; }
.player { width:100%; max-height: 60vh; background:#000; border-radius:12px; }
.videoArea, .aiArea { border:2px solid #000; border-radius:12px; padding:12px; }
.actions { margin-top: 10px; display:flex; justify-content:center; }
.btn { padding:10px 14px; border-radius:999px; border:0; background:#4e9b6a; color:#fff; font-weight:900; cursor:pointer; }
.btn:disabled { opacity:.6; cursor:not-allowed; }
.empty { padding: 20px; text-align:center; color:#666; }
.pill { font-size:12px; opacity:.75; }

/* 回看錯誤片段 */
.returnBar{
  display:flex;
  justify-content:space-between;
  align-items:center;
  padding:10px 12px;
  border-radius:12px;
  border:2px solid #000;
  margin-bottom:10px;
  background:#fff3cd;
}
.returnText{ font-weight:900; }
.returnBtn{
  border:0;
  border-radius:999px;
  padding:10px 14px;
  background:#4e9b6a;
  color:#fff;
  font-weight:900;
  cursor:pointer;
}

</style>
