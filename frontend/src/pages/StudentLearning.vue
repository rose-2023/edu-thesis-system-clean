<template>
  <div class="page">
    <header class="topbar">
      <div class="topbar-left">
        <div class="brand-dot"></div>
        <div class="topbar-texts">
          <div class="title">單元列表</div>
          <div class="subtitle">{{ currentUnitLabel || displayUnitName(currentUnit) }}<span v-if="currentTitle">：{{ currentTitle }}</span></div>
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
              <span class="unit-title">{{ u.unitLabel || displayUnitName(u.unit) }}</span>
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
                <div class="vtitle">
                  {{ v.title }}
                  <span v-if="isVideoCompleted(v)" class="doneTag">✓ 已完成</span>
                </div>
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

        <div v-if="selectedVideo" class="playerShell">
          <video
            ref="player"
            :key="selectedVideoId"
            class="player"
            controls
            :src="selectedVideo.videoUrl"
          >
            <track
              v-if="subtitleTrackUrl"
              kind="subtitles"
              srclang="zh-Hant"
              label="中文字幕"
              :src="subtitleTrackUrl"
            />
          </video>
          <div v-if="activeSubtitleText" class="subtitleOverlay">
            {{ activeSubtitleText }}
          </div>
        </div>
        <div v-else class="empty">請從左側選擇影片</div>

        <div v-if="subtitleLoading" class="subtitleStatus">字幕載入中...</div>
        <div v-else-if="subtitleError" class="subtitleStatus subtitleStatusError">
          {{ subtitleError }}
        </div>

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
import { logoutCurrentSession } from "../sessionAuth";

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
let _lastSentWatchSeconds = 0;
let _watchSessionId = "";

function newWatchSessionId() {
  try {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  } catch (_) {}
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function _resetWatchAccumulators() {
  _lastVideoTime = null;
  // 注意：watchSeconds / reachedEnd / watchStartAt 是否重置由呼叫端決定
}

function resetWatchCounters() {
  if (autoSaveTimer) {
    clearInterval(autoSaveTimer);
    autoSaveTimer = null;
  }
  watchStartAt.value = null;
  reachedEnd.value = false;
  watchSeconds.value = 0;
  hasSentReached = false;
  _lastSentWatchSeconds = 0;
  _resetWatchAccumulators();
}

function resetWatchSession() {
  _watchSessionId = newWatchSessionId();
  resetWatchCounters();
}

function _bindPlayerWatchEvents() {
  const el = player.value;
  if (!el) return;

  // 避免重複綁定
  if (_watchBound) return;
  _watchBound = true;

  el.addEventListener("loadedmetadata", () => {
    updateActiveSubtitle(el.currentTime || 0);
    syncNativeSubtitleMode();
  });

  el.addEventListener("webkitbeginfullscreen", () => {
    isNativeSubtitleFullscreen.value = true;
    syncNativeSubtitleMode(true);
  });

  el.addEventListener("webkitendfullscreen", () => {
    isNativeSubtitleFullscreen.value = false;
    syncNativeSubtitleMode(false);
  });

  el.addEventListener("play", () => {
    if (!watchStartAt.value) watchStartAt.value = new Date().toISOString();
    // 以目前播放位置作為起點
    _lastVideoTime = el.currentTime;
    sendWatchLog("video_play");

    // ✅【新增】播放時啟動自動送
    if (!autoSaveTimer) {
      autoSaveTimer = setInterval(() => {
        sendWatchLog("video_progress");
      }, 5000);
    }
  });

  el.addEventListener("pause", () => {
    _lastVideoTime = el.currentTime;
    if (!reachedEnd.value) {
      sendWatchLog("video_pause");
    }
    // ✅【新增】暫停就停止自動送（省資源）
    if (autoSaveTimer) {
      clearInterval(autoSaveTimer);
      autoSaveTimer = null;
    }
  });

  // 使用者拖曳進度條（seek）時，不計入「連續觀看」秒數，直接重設起點
  el.addEventListener("seeking", () => {
    _lastVideoTime = el.currentTime;
    updateActiveSubtitle(el.currentTime || 0);
  });

  el.addEventListener("ended", () => {
    reachedEnd.value = true;
    activeSubtitleText.value = "";
    _lastVideoTime = null;
    if (autoSaveTimer) {
      clearInterval(autoSaveTimer);
      autoSaveTimer = null;
    }
    // 看完就先送一次（不等離開頁面）
    sendWatchLog("video_ended");
  });

  el.addEventListener("timeupdate", () => {
    updateActiveSubtitle(el.currentTime || 0);
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
        sendWatchLog("video_ended"); // ✅ 到 end 立刻送一次
      }
    }
  });
}

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000";

const units = ref([]);
const openIndex = ref(0);
const completedVideoIds = ref(new Set());

const selectedVideo = ref(null);
const selectedVideoId = ref(null);

const currentUnit = ref("");
const currentUnitLabel = ref("");
const currentTitle = ref("");
const subtitleCues = ref([]);
const activeSubtitleText = ref("");
const subtitleLoading = ref(false);
const subtitleError = ref("");
const subtitleTrackUrl = ref("");
const isNativeSubtitleFullscreen = ref(false);

const player = ref(null);

const bullets = ref([
  "（示意）這裡未來可以放 AI 摘要/錯誤原因",
]);

const showReturnBar = computed(() => {
  // 只要有 start 就代表是「回看片段」進來的
  return route.query.start != null;
});

function _normalizeRawUnit(rawUnit) {
  const raw = String(rawUnit || "").trim();
  if (!raw) return "";

  // 老師端有些資料會出現 AU1-IO / au1-io，學生端一律視為 U1-IO
  const normalized = raw.replace(/^A(?=U\d+)/i, "");
  return normalized.trim();
}

function _normalizeUnitPrefix(rawUnit) {
  const raw = _normalizeRawUnit(rawUnit);
  const m = raw.match(/^(U\d+)/i);
  return m ? m[1].toUpperCase() : raw.toUpperCase();
}

function _normalizeUnitKey(rawUnit) {
  const raw = _normalizeRawUnit(rawUnit);
  if (!raw) return "";
  const m = raw.match(/^(U\d+)(?:[-_\s]*([A-Za-z]+))?/i);
  if (m) {
    const p = String(m[1] || "").toUpperCase();
    const sub = String(m[2] || "").toLowerCase();
    return sub ? `${p}-${sub}` : p;
  }
  return raw.toUpperCase().replace(/\s+/g, "");
}

function displayUnitName(rawUnit) {
  const raw = _normalizeRawUnit(rawUnit);
  if (!raw) return "";

  const prefix = _normalizeUnitPrefix(raw);
  const m = raw.match(/^(U\d+)(?:[-_ ]*([A-Za-z]+))?(.*)$/i);
  const subTag = (m?.[2] || "").toLowerCase();
  const tail = String(m?.[3] || "").trim();

  if (prefix === "U3") {
    if (subTag === "for") return "定數迴圈";
    if (subTag === "loop") return "迴圈觀念解析";
  }
  if (prefix === "U2") {
    if (subTag === "if") return "條件判斷與應用";
    if (subTag === "ifelse") return "if-else 條件判斷";
    if (subTag === "elif") return "elif 條件判斷";
  }
  if (prefix === "U1" && subTag === "io") return "輸入輸出";

  if (tail) {
    const cleanTail = tail.replace(/^[-_\s]+/, "").trim();
    if (cleanTail) return cleanTail;
  }

  const nameMap = {
    U1: "輸入輸出",
    U2: "條件判斷與應用",
    U3: "迴圈觀念解析",
    U4: "巢狀迴圈",
    U5: "不定數迴圈",
    U6: "串列觀念解析",
    // U7: "函數觀念解析",
  };
  return nameMap[prefix] || raw;
}

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
    const rawUnit = _normalizeRawUnit(v.unit || "未分類");
    const key = _normalizeUnitKey(rawUnit) || "未分類";
    const label = String(v.unit_label || v.unitLabel || "").trim() || displayUnitName(rawUnit);
    if (!map.has(key)) map.set(key, { unit: rawUnit, unitKey: key, unitLabel: label, videos: [] });
    if (label && !map.get(key).unitLabel) map.get(key).unitLabel = label;
    map.get(key).videos.push({
      ...v,
      unit: rawUnit,
      unit_label: label,
    });
  }
  return [...map.values()].sort((a, b) =>
    (a.unitKey || a.unit).localeCompare((b.unitKey || b.unit), "en", { numeric: true })
  );
}

function parseSubtitleTime(raw) {
  const text = String(raw || "").trim().replace(",", ".");
  const m = text.match(/(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?/);
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  const sec = Number(m[3]);
  const ms = Number((m[4] || "0").padEnd(3, "0").slice(0, 3));
  if (![h, min, sec, ms].every(Number.isFinite)) return null;
  return h * 3600 + min * 60 + sec + ms / 1000;
}

function parseSubtitleText(rawText) {
  const text = String(rawText || "").replace(/\r/g, "").trim();
  if (!text) return [];
  return text
    .split(/\n{2,}/)
    .map((block) => {
      const lines = block
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      const timeIndex = lines.findIndex((line) => line.includes("-->"));
      if (timeIndex < 0) return null;
      const [startRaw, endRaw] = lines[timeIndex].split("-->").map((part) => part.trim());
      const start = parseSubtitleTime(startRaw);
      const end = parseSubtitleTime(endRaw);
      const subtitleText = lines.slice(timeIndex + 1).join("\n").trim();
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || !subtitleText) return null;
      return { start, end, text: subtitleText };
    })
    .filter(Boolean)
    .sort((a, b) => a.start - b.start);
}

function formatVttTime(seconds) {
  const totalMs = Math.max(0, Math.round(Number(seconds || 0) * 1000));
  const ms = totalMs % 1000;
  const totalSec = Math.floor(totalMs / 1000);
  const sec = totalSec % 60;
  const totalMin = Math.floor(totalSec / 60);
  const min = totalMin % 60;
  const hour = Math.floor(totalMin / 60);
  const pad = (value, width = 2) => String(value).padStart(width, "0");
  return `${pad(hour)}:${pad(min)}:${pad(sec)}.${pad(ms, 3)}`;
}

function buildVttFromCues(cues) {
  const rows = ["WEBVTT", ""];
  cues.forEach((cue, index) => {
    const text = String(cue.text || "").replace(/-->/g, "->").trim();
    if (!text) return;
    rows.push(String(index + 1));
    rows.push(`${formatVttTime(cue.start)} --> ${formatVttTime(cue.end)}`);
    rows.push(text);
    rows.push("");
  });
  return rows.join("\n");
}

function clearSubtitleTrackUrl() {
  if (!subtitleTrackUrl.value) return;
  try {
    URL.revokeObjectURL(subtitleTrackUrl.value);
  } catch (_) {}
  subtitleTrackUrl.value = "";
}

function setSubtitleTrack(cues) {
  clearSubtitleTrackUrl();
  if (!Array.isArray(cues) || !cues.length) return;
  const blob = new Blob([buildVttFromCues(cues)], { type: "text/vtt;charset=utf-8" });
  subtitleTrackUrl.value = URL.createObjectURL(blob);
  nextTick(() => syncNativeSubtitleMode());
}

function updateActiveSubtitle(currentTime) {
  const t = Number(currentTime);
  if (!Number.isFinite(t) || !subtitleCues.value.length) {
    activeSubtitleText.value = "";
    return;
  }
  const cue = subtitleCues.value.find((item) => t >= item.start && t <= item.end);
  activeSubtitleText.value = cue?.text || "";
}

function isPlayerFullscreen() {
  const el = player.value;
  if (!el) return false;
  return document.fullscreenElement === el
    || document.webkitFullscreenElement === el
    || document.mozFullScreenElement === el
    || document.msFullscreenElement === el;
}

function syncNativeSubtitleMode(forceVisible = null) {
  const el = player.value;
  if (!el?.textTracks?.length) return;
  const visible = forceVisible === null ? isNativeSubtitleFullscreen.value : Boolean(forceVisible);
  for (const track of Array.from(el.textTracks)) {
    track.mode = visible ? "showing" : "hidden";
  }
}

function handleFullscreenChange() {
  isNativeSubtitleFullscreen.value = isPlayerFullscreen();
  syncNativeSubtitleMode();
}

async function loadSubtitleForVideo(video) {
  subtitleCues.value = [];
  activeSubtitleText.value = "";
  subtitleError.value = "";
  isNativeSubtitleFullscreen.value = false;
  clearSubtitleTrackUrl();

  const vid = videoKey(video);
  if (!vid) return;

  subtitleLoading.value = true;
  try {
    const token = String(localStorage.getItem("token") || "").trim();
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const qs = new URLSearchParams({ video_id: vid });
    const response = await fetch(`${API_BASE}/api/admin_upload/subtitle/content?${qs.toString()}`, {
      headers,
    });
    if (response.status === 404) return;
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const cues = parseSubtitleText(data?.text || "");
    subtitleCues.value = cues;
    setSubtitleTrack(cues);
    if (!cues.length && data?.text) {
      subtitleError.value = "字幕格式無法顯示，請確認 SRT 時間軸格式。";
    }
    updateActiveSubtitle(player.value?.currentTime || 0);
  } catch (err) {
    subtitleError.value = "字幕載入失敗，請確認字幕檔已上傳。";
    console.warn("subtitle load failed", err);
  } finally {
    subtitleLoading.value = false;
  }
}

function selectVideo(u, v) {
  if (selectedVideoId.value) {
    sendWatchLog("video_leave");
  }
  selectedVideo.value = v;
  selectedVideoId.value = v._id || v.id || v.video_id;
  currentUnit.value = u.unit;
  currentUnitLabel.value = u.unitLabel || v.unit_label || displayUnitName(u.unit);
  currentTitle.value = v.title;
  resetWatchSession();
  loadSubtitleForVideo(v);
  sendWatchLog("video_click");

  // ✅ 每次切換影片都重新綁定一次監聽（新的 <video> 會重新渲染）
  _watchBound = false;
  nextTick(() => {
    _bindPlayerWatchEvents();
  });
}

function videoKey(v) {
  return String(v?._id || v?.id || v?.video_id || "").trim();
}

function isVideoCompleted(v) {
  const key = videoKey(v);
  if (!key) return false;
  return completedVideoIds.value.has(key);
}

function relatedTaskIdsForVideo(video = selectedVideo.value) {
  return Array.isArray(video?.related_task_ids)
    ? video.related_task_ids.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
}

function firstRelatedTaskId(video = selectedVideo.value) {
  return relatedTaskIdsForVideo(video)[0] || null;
}

function videoUnitKey(video) {
  return _normalizeUnitKey(_normalizeRawUnit(video?.unit || ""));
}

function routeUnitKey() {
  const raw = route.params.unit || route.query.unit || route.query.unit_id || "";
  return _normalizeUnitKey(_normalizeRawUnit(raw));
}

function filterVideosByUnit(videos, unitKey) {
  if (!unitKey) return videos;
  return videos.filter((video) => videoUnitKey(video) === unitKey);
}

async function logLearningTransition(eventType, extra = {}) {
  const token = String(localStorage.getItem("token") || "").trim();
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const relatedTaskIds = relatedTaskIdsForVideo();
  const fromVideoId = selectedVideoId.value
    ? String(selectedVideoId.value)
    : (route.params.videoId ? String(route.params.videoId) : "");
  const body = {
    session_id: _watchSessionId,
    event_type: eventType,
    page: "student_learning",
    activity_type: "practice",
    test_role: null,
    task_id: extra.to_task_id || null,
    unit_id: currentUnit.value || selectedVideo.value?.unit || "",
    from_video_id: fromVideoId || null,
    from_video_title: currentTitle.value || selectedVideo.value?.title || "",
    watch_session_id: _watchSessionId,
    to_task_id: extra.to_task_id || null,
    to_question_type: "parsons",
    question_type: "parsons",
    event_at: new Date().toISOString(),
    metadata: {
      related_task_ids: relatedTaskIds,
      ...(extra.metadata || {}),
    },
  };
  try {
    const response = await fetch(`${API_BASE}/api/learning_logs`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      keepalive: true,
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      console.warn("learning transition log failed", eventType, response.status, text);
    }
  } catch (err) {
    console.warn("learning transition log failed", eventType, err);
  }
}

async function loadCompletedVideoIds() {
  const sid = String(localStorage.getItem("student_id") || localStorage.getItem("studentId") || "").trim();
  const pid = String(localStorage.getItem("participant_id") || "").trim();
  if (!sid) {
    if (!pid) {
      completedVideoIds.value = new Set();
      return;
    }
  }

  try {
    const qs = new URLSearchParams();
    if (sid) qs.set("student_id", sid);
    if (pid) qs.set("participant_id", pid);

    const res = await fetch(`${API_BASE}/api/student/completed_videos?${qs.toString()}`);
    if (!res.ok) {
      completedVideoIds.value = new Set();
      return;
    }
    const data = await res.json();
    const ids = Array.isArray(data?.video_ids)
      ? data.video_ids.map((x) => String(x || "").trim()).filter(Boolean)
      : [];
    completedVideoIds.value = new Set(ids);
  } catch (_) {
    completedVideoIds.value = new Set();
  }
}

async function goParsons() {
  if (!selectedVideoId.value) {
    alert("請先從左側選擇一部影片");
    return;
  }
  await sendWatchLog("video_leave");
  const toTaskId = firstRelatedTaskId();
  await logLearningTransition("click_next_to_practice", {
    to_task_id: toTaskId,
  });
  router.push({
    path: `/parsons/${selectedVideoId.value || route.params.videoId}`,
    query: {
      review_attempt_id: String(route.query.attempt_id || ""),
      from_video_id: String(selectedVideoId.value || route.params.videoId || ""),
      from_video_title: currentTitle.value || selectedVideo.value?.title || "",
      unit_id: currentUnit.value || selectedVideo.value?.unit || "",
      watch_session_id: _watchSessionId,
      to_task_id: toTaskId || "",
    },
  });
}

async function goHome() {
  await sendWatchLog("video_leave");
  router.push("/home");
}

async function logout() {
  await sendWatchLog("video_leave");
  await logoutCurrentSession(API_BASE);
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
      resetWatchCounters();

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

async function fetchAllActiveVideos() {
  const all = [];
  let page = 1;
  let totalPages = 1;

  while (page <= totalPages) {
    const qs = new URLSearchParams({
      status: "active",
      page: String(page),
      per_page: "200",
    });

    const res = await fetch(`${API_BASE}/api/admin_upload/videos?${qs.toString()}`);
    if (!res.ok) {
      throw new Error(`影片清單讀取失敗（HTTP ${res.status}）`);
    }

    const data = await res.json();
    const pageItems = Array.isArray(data)
      ? data
      : data.items || data.videos || [];

    all.push(...pageItems);

    const total = Number(data?.total || 0);
    const perPage = Number(data?.per_page || 200) || 200;
    totalPages = Number(data?.pages || Math.max(1, Math.ceil(total / perPage)) || 1);

    if (!data || Array.isArray(data)) break;
    if (!pageItems.length) break;
    page += 1;
  }

  return all;
}

onMounted(async () => {
  await nextTick();
  document.addEventListener("fullscreenchange", handleFullscreenChange);
  document.addEventListener("webkitfullscreenchange", handleFullscreenChange);
  await loadCompletedVideoIds();

  // ✅ 注意：影片 <video> 會在選到影片後才出現，所以監聽會在 selectVideo() 裡 nextTick 後綁定

  // 讀 query start/end
  const start = Number(route.query.start);
  const end = Number(route.query.end);

  // 讀影片列表（學生端不能只抓第一頁，否則單元/影片會漏）
  const list = await fetchAllActiveVideos();

  const filtered = list.filter((v) => (
    v.active !== false
    && v.is_active !== false
    && v.deleted !== true
    && v.is_deleted !== true
  ));

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

  let normalized = filtered.map((v) => ({
    ...v,
    _id: v._id?.$oid || v._id || v.id,
    videoUrl: resolveFileUrl(v.path || v.videoUrl || v.video_path),
    thumbnailUrl: resolveFileUrl(
      v.thumbnail || v.thumbnailUrl || v.thumbnail_url,
      "thumbnail"
    ),
  }));

  const initialUnitKey = routeUnitKey();
  if (initialUnitKey) {
    normalized = filterVideosByUnit(normalized, initialUnitKey);
  }

  units.value = groupByUnit(normalized);
  // 保險重抓一次完成清單，避免首次請求與列表載入時序造成漏顯示。
  await loadCompletedVideoIds();

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
      if (!initialUnitKey) {
        const selectedUnitKey = videoUnitKey(foundV);
        const sameUnitVideos = filterVideosByUnit(normalized, selectedUnitKey);
        const narrowedUnits = groupByUnit(sameUnitVideos);
        if (narrowedUnits.length) {
          units.value = narrowedUnits;
          foundU = narrowedUnits[0];
          foundV = foundU.videos.find((x) => String(x._id) === routeVid) || foundV;
          openIndex.value = 0;
        }
      }
      selectVideo(foundU, foundV);
      await seekToSegment(start, end);
      return;
    }
  }

  // ✅ 原本路由：/learn/:unit
  const unitParam = route.params.unit ? String(route.params.unit) : "";
  if (unitParam) {
    const targetKey = _normalizeUnitKey(unitParam);
    const idx = units.value.findIndex((u) => _normalizeUnitKey(u.unit) === targetKey || String(u.unit).trim() === String(unitParam).trim());
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

async function sendWatchLog(eventType = "video_progress") {
  const vid = selectedVideoId.value
    ? String(selectedVideoId.value)
    : (route.params.videoId ? String(route.params.videoId) : "");
  if (!vid) return;

  const el = player.value;
  const watchTotal = Math.round(Number(watchSeconds.value || 0));
  const watchDelta = Math.max(0, watchTotal - Math.round(Number(_lastSentWatchSeconds || 0)));
  if (eventType === "video_progress" && watchDelta <= 0 && !reachedEnd.value) return;

  const payload = {
    event_type: eventType,
    video_id: vid,
    unit_id: currentUnit.value || selectedVideo.value?.unit || "",
    video_title: currentTitle.value || selectedVideo.value?.title || "",
    watch_session_id: _watchSessionId,
    attempt_id: route.query.attempt_id ? String(route.query.attempt_id) : "",
    task_id: null,
    start_sec: route.query.start != null ? Number(route.query.start) : null,
    end_sec: route.query.end != null ? Number(route.query.end) : null,
    watch_seconds: watchTotal,
    watch_delta_sec: watchDelta,
    current_time_sec: el ? Number(el.currentTime || 0) : null,
    video_duration_sec: el && Number.isFinite(Number(el.duration)) ? Number(el.duration) : null,
    playback_rate: el ? Number(el.playbackRate || 1) : 1,
    reached_end: Boolean(reachedEnd.value),
    watch_start_at: watchStartAt.value,
    watch_end_at: new Date().toISOString(),
    event_at: new Date().toISOString(),
    page: "student_learning",
    route_path: route.fullPath,
  };

  try {
    const token = String(localStorage.getItem("token") || "").trim();
    const headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(`${API_BASE}/api/video_rewatch_logs`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      keepalive: true,
    });
    if (response.status === 401) return;
    if (response.ok) {
      _lastSentWatchSeconds = watchTotal;
    } else {
      const text = await response.text().catch(() => "");
      console.warn("video watch log failed", response.status, text);
    }
  } catch (err) {
    console.warn("video watch log failed", err);
  }
}

onBeforeUnmount(() => {
  document.removeEventListener("fullscreenchange", handleFullscreenChange);
  document.removeEventListener("webkitfullscreenchange", handleFullscreenChange);
  clearSubtitleTrackUrl();
  if (autoSaveTimer) {
    clearInterval(autoSaveTimer);
    autoSaveTimer = null;
  }
  sendWatchLog("video_leave");
});

// 返回練習」按鈕 backToPractice() 裡也先送再跳
async function backToPractice() {
  await sendWatchLog("video_leave");
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
  try {
    await sendWatchLog("video_progress");
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
  min-width: 0;
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

.videoArea,
.unitNav {
  min-width: 0;
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
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.vtitle {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.doneTag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  background: #e7f8ee;
  border: 1px solid #9edbb1;
  color: #1f7a3f;
  font-size: 12px;
  font-weight: 800;
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

.playerShell {
  position: relative;
  width: 100%;
  background: #000;
  border-radius: 18px;
  overflow: hidden;
}

.player {
  width: 100%;
  height: auto;
  max-height: 64vh;
  background: #000;
  border-radius: 18px;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.08);
  display: block;
}

.subtitleOverlay {
  position: absolute;
  left: 50%;
  bottom: 58px;
  transform: translateX(-50%);
  max-width: min(88%, 860px);
  padding: 8px 14px;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.72);
  color: #fff;
  font-size: clamp(16px, 2.4vw, 25px);
  font-weight: 800;
  line-height: 1.55;
  text-align: center;
  white-space: pre-line;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.7);
  pointer-events: none;
}

.player::cue {
  color: #fff;
  font-size: 35px;
  font-weight: 800;
  line-height: 1.5;
  background: rgba(0, 0, 0, 0.72);
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.7);
}

.subtitleStatus {
  margin-top: 8px;
  color: #607080;
  font-size: 13px;
  text-align: center;
}

.subtitleStatusError {
  color: #b45309;
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

  .topbar {
    align-items: flex-start;
  }

  .topbar-actions {
    align-self: flex-end;
  }
}

@media (max-width: 720px) {
  .page {
    padding: 10px;
  }

  .topbar {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
    padding: 12px;
  }

  .topbar-actions {
    width: 100%;
    justify-content: stretch;
  }

  .topbar-actions .header-btn {
    flex: 1 1 0;
  }

  .title {
    font-size: 18px;
  }

  .subtitle {
    white-space: normal;
  }

  .grid {
    margin-top: 12px;
    gap: 12px;
  }

  .unitNav,
  .videoArea {
    padding: 12px;
    border-radius: 16px;
  }

  .sideHeader {
    margin-bottom: 10px;
  }

  .sideTitle {
    font-size: 16px;
  }

  .unit-header {
    padding: 11px 12px;
  }

  .videoItem {
    align-items: flex-start;
  }

  .thumb {
    width: 64px;
    height: 40px;
  }

  .videoHeader {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }

  .videoTitleText {
    font-size: 18px;
  }

  .playerShell {
    aspect-ratio: 4 / 3;
  }

  .player {
    height: 100%;
    max-height: 56vh;
    object-fit: contain;
  }

  .subtitleOverlay {
    left: 12px;
    right: 12px;
    transform: none;
    max-width: none;
    font-size: 15px;
    padding: 7px 10px;
  }

  .actions {
    margin-top: 14px;
  }

  .btn {
    width: 100%;
  }

  .returnBar {
    flex-direction: column;
    align-items: stretch;
    padding: 11px 12px;
  }

  .returnText {
    font-size: 14px;
  }
}

@media (max-width: 420px) {
  .topbar-actions {
    flex-direction: column;
  }

  .topbar-actions .header-btn {
    width: 100%;
  }

  .unit-header,
  .videoItem,
  .btn,
  .returnBtn {
    border-radius: 12px;
  }

  .videoTitleText {
    font-size: 17px;
  }

  .subtitleOverlay {
    font-size: 14px;
  }
}

@media (max-width: 920px) and (orientation: landscape) {
  .subtitleOverlay {
    bottom: 10px;
    font-size: 12px;
    padding: 6px 9px;
    max-width: min(92%, 960px);
  }

  .player::cue {
    font-size: 22px;
  }
}
</style>
