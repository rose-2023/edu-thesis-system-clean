<template>
  <div class="page">
    <div class="card">
      <h2>老師端：上傳影片</h2>

      <!-- ===== 上傳區 ===== -->
      <div class="row">
        <label>單元（unit）</label>
        <input v-model="unit" placeholder="例如：U1" />
      </div>

      <div class="row">
        <label>標題</label>
        <input v-model="title" placeholder="例如：單元一：迴圈基礎" />
      </div>

      <div class="row">
        <label>描述（可選）</label>
        <textarea v-model="description" rows="3" placeholder="簡短描述"></textarea>
      </div>

      <div class="row">
        <label>選擇影片（建議 mp4）</label>
        <input type="file" accept="video/*" @change="onFile" />
      </div>



      <p v-if="msg" class="msg">{{ msg }}</p>
      <p v-if="err" class="err">{{ err }}</p>

      <hr />

      <!-- ✅ 字幕/逐字稿校正 -->
      <div class="subCard">
        <div class="subTitle">字幕 / 逐字稿校正（SRT / TXT）</div>

        <div class="row">
          <label>上傳字幕檔（必填，.srt / .txt）</label>
          <input type="file" accept=".srt,.txt" @change="onSubtitleFile" />
        </div>

        <div class="subOps">
          <button class="btn2" type="button" @click="validateSubtitleOnly">
            檢查字幕時間軸
          </button>
          <span class="subOk" v-if="subCheckMsg">{{ subCheckMsg }}</span>
        </div>

        <div v-if="subCheckErrs.length" class="subErrBox">
          <div class="subErrTitle">字幕時間軸有錯（請修正後再上傳）：</div>
          <ul class="subErrList">
            <li v-for="(x,i) in subCheckErrs" :key="i">{{ x }}</li>
          </ul>
        </div>
      </div>

            <button class="btn" :disabled="uploading" @click="upload">
        {{ uploading ? "上傳中…" : "開始上傳" }}
      </button>
      <br />
      <br />

      <!-- ===== YouTube 風格清單區 ===== -->
      <div class="topbar">
        <div class="tabs">
          <button class="tab" :class="{ active: currentTab === 'active' }" @click="switchTab('active')">
            啟用中
          </button>
          <button class="tab" :class="{ active: currentTab === 'inactive' }" @click="switchTab('inactive')">
            停用中
          </button>
          <button class="tab" :class="{ active: currentTab === 'deleted' }" @click="switchTab('deleted')">
            已刪除
          </button>
        </div>

        <div class="rightOps">
          <button class="btn2" @click="loadList">刷新清單</button>
        </div>
      </div>

      <!-- ✅ 搜尋列（單元/標題） -->
      <div class="searchBar">
        <input v-model="qUnit" placeholder="搜尋單元（例如 U1）" @keyup.enter="doSearch" />
        <input v-model="qTitle" placeholder="搜尋標題（例如 迴圈）" @keyup.enter="doSearch" />
        <button class="btn2" @click="doSearch">搜尋</button>
        <button class="btn2" @click="clearSearch">清除</button>
      </div>

      <div class="hint">
        <span class="pill">目前分類：{{ tabLabel }}</span>
        <span class="pill">總筆數：{{ total }}</span>
        <span class="pill">本頁：{{ page }} / {{ totalPages }}</span>
        <span class="pill" v-if="loadingList">載入中…</span>
      </div>

      <div v-if="!videos.length" class="empty">
        <div class="emptyTitle">目前此分類沒有影片</div>
        <div class="emptySub">你可以切換上方分類、調整搜尋條件，或按「刷新清單」重新抓取。</div>
      </div>

      <div v-else class="list">
        <div class="thead">
          <div class="cThumb">縮圖</div>
          <div class="cTitle">影片</div>
          <div class="cMeta">資訊</div>
          <div class="cStatus">狀態</div>
          <div class="cActions">操作</div>
        </div>

        <div class="rowItem" v-for="v in videos" :key="v._id">
          <div class="cThumb">
            <div class="thumbWrap">
              <img
                v-if="thumbSrc(v)"
                class="thumbImg"
                :src="thumbSrc(v)" @load="console.log('thumb ok', thumbSrc(v))"
                @error="onThumbError(v)"
              />
              <div v-else class="thumbPlaceholder">尚無縮圖</div>

              <!-- ✅ 縮圖角落顯示長度（像 YouTube） -->
              <div class="dur" v-if="v.duration_sec">{{ formatDuration(v.duration_sec) }}</div>
            </div>
          </div>

          <div class="cTitle">
            <div class="titleMain">{{ v.title }}</div>
            <div class="titleSub">
              單元：{{ v.unit }} ｜ 檔名：{{ v.original_name || v.filename }}
            </div>
          </div>

          <div class="cMeta">
            <div class="metaLine">保存檔：{{ v.filename }}</div>
            <div class="metaLine">大小：{{ formatSize(v.size) }}</div>
            <div class="metaLine">長度：{{ v.duration_sec ? formatDuration(v.duration_sec) : "-" }}</div>

            <!-- ✅ 相對時間顯示 -->
            <div class="metaLine">
              上傳時間：{{ formatRelativeTime(v.created_at) }}
              <span class="mutedInline">（{{ formatTime(v.created_at) }}）</span>
            </div>
          </div>

          <div class="cStatus">
            <span v-if="v.deleted" class="badge deleted">已刪除</span>
            <span v-else-if="v.active" class="badge on">啟用中</span>
            <span v-else class="badge off">停用中</span>
          </div>

          <div class="cActions">
            <button
              v-if="!v.deleted"
              class="btnSmall"
              :disabled="busyId === v._id"
              @click="toggleActive(v)"
            >
              {{ v.active ? "停用" : "啟用" }}
            </button>

            <button
              v-if="!v.deleted"
              class="btnSmall danger"
              :disabled="busyId === v._id"
              @click="softDelete(v)"
            >
              刪除
            </button>

            <span v-if="v.deleted" class="muted">（已刪除）</span>
            <button class="btn" @click="goSubtitleCorrect(v)">字幕校正</button>
          </div>
        </div>
      </div>

      <!-- ✅ 分頁控制（每頁 10 筆） -->
      <div class="pager" v-if="totalPages > 1">
        <button class="btn2" :disabled="page <= 1 || loadingList" @click="goPage(1)">第一頁</button>
        <button class="btn2" :disabled="page <= 1 || loadingList" @click="goPage(page - 1)">上一頁</button>

        <span class="pagerInfo">第 {{ page }} / {{ totalPages }} 頁</span>

        <button class="btn2" :disabled="page >= totalPages || loadingList" @click="goPage(page + 1)">下一頁</button>
        <button class="btn2" :disabled="page >= totalPages || loadingList" @click="goPage(totalPages)">最後頁</button>
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";
import { api } from "../api";
import { useRoute, useRouter } from "vue-router";



const unit = ref("U1");
const title = ref("");
const description = ref("");
const file = ref(null);
const subtitleFile = ref(null);
const route = useRoute();
const router = useRouter();

const uploading = ref(false);
const loadingList = ref(false);
const busyId = ref("");

const msg = ref("");
const err = ref("");

const currentTab = ref("active"); // active | inactive | deleted
const videos = ref([]);
const videoId = route.query.video_id; // ✅ 從 AdminUpload 帶過來的

/* ✅ 搜尋條件 */
const qUnit = ref("");
const qTitle = ref("");

/* ✅ 分頁 */
const page = ref(1);
const perPage = ref(10);
const total = ref(0);

/* ✅ 字幕檢核結果 */
const subCheckMsg = ref("");
const subCheckErrs = ref([]);

function onFile(e) {
  file.value = e.target.files?.[0] || null;
}

function onSubtitleFile(e) {
  subtitleFile.value = e.target.files?.[0] || null;
  subCheckMsg.value = "";
  subCheckErrs.value = [];
}

// ✅ 導到字幕校正頁
// ✅ 導到字幕校正頁
// ✅ 導到字幕校正頁
function goSubtitleCorrect(v) {
  const videoId = v?._id || v?.id;
  if (!videoId) {
    alert("找不到 video_id，請確認影片資料有 _id 或 id");
    return;
  }

  router.push({
    name: "teacherSubtitle",
    query: {
      video_id: String(videoId),
      unit: v.unit || "",
      title: v.title || "",
      video_url: v.video_url || v.path || ""
    }
  });
}




async function validateSubtitleOnly() {
  subCheckMsg.value = "";
  subCheckErrs.value = [];

  if (!subtitleFile.value) {
    subCheckErrs.value = ["請先選擇字幕檔（.srt 或 .txt）"];
    return;
  }

  try {
    const form = new FormData();
    form.append("file", subtitleFile.value);

    const res = await api.post("/api/admin_upload/subtitle/validate", form, {
      headers: { "Content-Type": "multipart/form-data" }
    });

    if (res.data?.ok) {
      subCheckMsg.value = `✅ 字幕時間軸正常（共 ${res.data.blocks} 段）`;
    } else {
      subCheckErrs.value = res.data?.errors || ["字幕時間軸有錯"];
    }
  } catch (e) {
    const data = e?.response?.data;
    subCheckErrs.value = data?.errors || data?.subtitle_errors || [data?.message || e.message || "檢查失敗"];
  }
}

const totalPages = computed(() => Math.max(1, Math.ceil((total.value || 0) / perPage.value)));

const tabLabel = computed(() => {
  if (currentTab.value === "active") return "啟用中";
  if (currentTab.value === "inactive") return "停用中";
  if (currentTab.value === "deleted") return "已刪除";
  return currentTab.value;
});

function formatSize(n) {
  if (!n && n !== 0) return "-";
  const kb = 1024;
  const mb = kb * 1024;
  const gb = mb * 1024;
  if (n >= gb) return (n / gb).toFixed(2) + " GB";
  if (n >= mb) return (n / mb).toFixed(2) + " MB";
  if (n >= kb) return (n / kb).toFixed(2) + " KB";
  return n + " B";
}

function formatTime(t) {
  const d = parseServerTime(t);
  if (!d) return "-";
  return d.toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false });
}

function formatRelativeTime(t) {
  if (!t) return "";

  const d = new Date(t);
  if (Number.isNaN(d.getTime())) return "";

  const diffMs = Date.now() - d.getTime();
  const sec = Math.floor(diffMs / 1000);

  if (sec < 0) return "剛剛";
  if (sec < 60) return `${sec} 秒前`;

  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分鐘前`;

  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小時前`;

  const day = Math.floor(hr / 24);
  if (day < 7) return `${day} 天前`;

  return d.toLocaleString();
}






/* ✅ 相對時間 */
function parseServerTime(t) {
  if (!t) return null;

  // 已經是 Date
  if (t instanceof Date) return t;

  const s = String(t);

  // 如果字串已經有時區（Z 或 +08:00 這種），直接交給 Date
  if (/[zZ]|[+\-]\d{2}:\d{2}$/.test(s)) {
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  // 常見後端格式：2026-01-25 12:10:46（沒有時區）
  // 我們把它視為 UTC，轉成 ISO + Z
  // "YYYY-MM-DD HH:mm:ss" -> "YYYY-MM-DDTHH:mm:ssZ"
  const isoUtc = s.replace(" ", "T") + "Z";
  const d = new Date(isoUtc);
  return isNaN(d.getTime()) ? null : d;
}


/* ✅ 長度 mm:ss */
function formatDuration(sec) {
  const s = Math.max(0, Number(sec || 0));
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
}

function switchTab(tab) {
  currentTab.value = tab;
  page.value = 1;
  loadList();
}

function doSearch() {
  page.value = 1;
  loadList();
}
function clearSearch() {
  qUnit.value = "";
  qTitle.value = "";
  page.value = 1;
  loadList();
}

function goPage(p) {
  const np = Math.min(Math.max(1, p), totalPages.value);
  if (np === page.value) return;
  page.value = np;
  loadList();
}

/* 縮圖 URL */
const BACKEND_BASE = api.defaults.baseURL?.replace(/\/$/, "") || "";
function thumbSrc(v) {
  const p = v?.thumbnail || "";
  if (!p) return "";
  if (p.startsWith("http://") || p.startsWith("https://")) return p;
  
  // 盡量用相對路徑（同網域/反向代理最穩）
  if (p.startsWith("/")) return p;
   return `${BACKEND_BASE}/api/admin_upload/uploads/${p.replace(/^\/+/, "")}`;
}

function onThumbError(v) {
  console.log("thumb err", thumbSrc(v), v.thumbnail);
  v.thumbnail = "";
}


async function upload() {
  msg.value = "";
  err.value = "";
  subCheckMsg.value = "";
  subCheckErrs.value = [];

  if (!unit.value || !title.value) {
    err.value = "unit 與 title 必填";
    return;
  }
  if (!file.value) {
    err.value = "請選擇影片檔案";
    return;
  }
  if (!subtitleFile.value) {
    err.value = "字幕檔必填（.srt / .txt）";
    return;
  }

  uploading.value = true;
  try {
    const form = new FormData();
    form.append("unit", unit.value);
    form.append("title", title.value);
    form.append("description", description.value || "");
    form.append("uploaded_by", localStorage.getItem("participant_id") || "admin");
    form.append("file", file.value);
    form.append("subtitle", subtitleFile.value);

    const res = await api.post("/api/admin_upload/video", form, {
      headers: { "Content-Type": "multipart/form-data" }
    });

    if (!res.data?.ok) {
      err.value = res.data?.message || "上傳失敗";
      return;
    }

    msg.value = `✅ 上傳成功：${res.data.filename}`;
    currentTab.value = "active";
    page.value = 1;
    await loadList();
  } catch (e) {
    const data = e?.response?.data;

    // ✅ 後端如果回字幕錯誤，會放 subtitle_errors
    if (data?.subtitle_errors?.length) {
      err.value = data?.message || "字幕時間軸有錯，請檢查";
      subCheckErrs.value = data.subtitle_errors;
    } else {
      err.value = data?.message || e.message || "上傳失敗";
    }
  } finally {
    uploading.value = false;
  }
}

async function loadList() {
  err.value = "";
  msg.value = "";
  loadingList.value = true;

  try {
    const res = await api.get("/api/admin_upload/videos", {
      params: {
        status: currentTab.value,
        unit: qUnit.value || "",
        title: qTitle.value || "",
        page: page.value,
        per_page: perPage.value
      }
    });

    if (!res.data?.ok) {
      err.value = res.data?.message || "讀取失敗";
      videos.value = [];
      total.value = 0;
      return;
    }

    videos.value = res.data.videos || [];
    total.value = res.data.total ?? (videos.value.length || 0);

    if (page.value > totalPages.value) page.value = totalPages.value;
  } catch (e) {
    err.value = e?.response?.data?.message || e.message || "讀取失敗";
    videos.value = [];
    total.value = 0;
  } finally {
    loadingList.value = false;
  }
}

async function toggleActive(v) {
  err.value = "";
  msg.value = "";
  busyId.value = v._id;

  try {
    const nowActive = !!v.active;
    const res = await api.patch(`/api/admin_upload/video/${v._id}/active`, {
      active: !nowActive
    });

    if (!res.data?.ok) {
      err.value = res.data?.message || "切換失敗";
      return;
    }

    await loadList();
  } catch (e) {
    err.value = e?.response?.data?.message || e.message || "切換失敗";
  } finally {
    busyId.value = "";
  }
}

async function softDelete(v) {
  err.value = "";
  msg.value = "";

  const ok = confirm(
    `確定要刪除此影片嗎？\n\n標題：${v.title}\n單元：${v.unit}\n\n⚠️ 這是「軟刪除」：資料庫不會刪除，會移到「已刪除」分類。`
  );
  if (!ok) return;

  busyId.value = v._id;
  try {
    const res = await api.patch(`/api/admin_upload/video/${v._id}/delete`, {
      deleted_by: localStorage.getItem("participant_id") || "admin"
    });

    if (!res.data?.ok) {
      err.value = res.data?.message || "刪除失敗";
      return;
    }

    msg.value = `✅ 已刪除（已移到「已刪除」分類）：${v.title}`;
    await loadList();
  } catch (e) {
    err.value = e?.response?.data?.message || e.message || "刪除失敗";
  } finally {
    busyId.value = "";
  }
}

onMounted(() => {
  loadList();
});
</script>

<style scoped>
.page { min-height: 100vh; display: grid; place-items: center; padding: 24px; background: #f6f7fb; }
.card { width: 100%; max-width: 90%; border: 1px solid #e6e6e6; border-radius: 16px; padding: 18px; background: #fff; box-shadow: 0 6px 26px rgba(0,0,0,.05); }
.row { margin: 12px 0; display: flex; flex-direction: column; gap: 6px; }
input, textarea { padding: 10px; border: 1px solid #ddd; border-radius: 10px; }
.btn { width: 100%; padding: 10px; border: 0; border-radius: 10px; cursor: pointer; background: #111827; color: #fff; }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.btn2 { padding: 10px 12px; border: 1px solid #ddd; border-radius: 10px; cursor: pointer; background: #fafafa; }
.msg { color: #065f46; font-weight: 700; margin-top: 10px; }
.err { color: #b91c1c; font-weight: 700; margin-top: 10px; }

.topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.tabs { display: flex; gap: 8px; flex-wrap: wrap; }
.tab { padding: 10px 12px; border: 1px solid #ddd; border-radius: 999px; cursor: pointer; background: #fff; }
.tab.active { background: #111827; color: #fff; border-color: #111827; }
.hint { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0 12px; }
.pill { font-size: 12px; border: 1px solid #e5e7eb; background: #f9fafb; padding: 6px 10px; border-radius: 999px; color: #374151; }

.searchBar{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin: 10px 0 8px; }
.searchBar input{ flex: 1; min-width: 200px; }

.empty { border: 1px dashed #d1d5db; border-radius: 14px; padding: 18px; background: #fbfbfd; }
.emptyTitle { font-weight: 800; margin-bottom: 4px; }
.emptySub { color: #6b7280; font-size: 13px; }

.list { border: 1px solid #eee; border-radius: 14px; overflow: hidden; }
.thead, .rowItem { display: grid; grid-template-columns: 1.2fr 2.2fr 2.2fr 1fr 1.2fr; gap: 10px; align-items: center; padding: 12px; }
.thead { background: #f9fafb; border-bottom: 1px solid #eee; font-weight: 800; }
.rowItem { border-bottom: 1px solid #f0f0f0; }
.rowItem:last-child { border-bottom: 0; }

.titleMain { font-weight: 800; }
.titleSub { font-size: 12px; color: #6b7280; margin-top: 4px; }
.metaLine { font-size: 12px; color: #374151; margin: 2px 0; }
.mutedInline{ color:#6b7280; margin-left:6px; }

.badge { display: inline-flex; align-items: center; justify-content: center; padding: 6px 10px; border-radius: 999px; font-size: 12px; font-weight: 800; border: 1px solid transparent; }
.badge.on { background: rgba(5,150,105,.12); color: #065f46; border-color: rgba(5,150,105,.22); }
.badge.off { background: rgba(185,28,28,.10); color: #991b1b; border-color: rgba(185,28,28,.22); }
.badge.deleted { background: rgba(107,114,128,.12); color: #374151; border-color: rgba(107,114,128,.25); }

.cActions { display: flex; gap: 10px; align-items: center; justify-content: flex-start; flex-wrap: wrap; }
.btnSmall { padding: 8px 10px; border-radius: 10px; border: 1px solid #ddd; cursor: pointer; background: #fafafa; }
.btnSmall:disabled { opacity: .6; cursor: not-allowed; }
.btnSmall.danger { border-color: rgba(185,28,28,.35); color: #b91c1c; background: rgba(185,28,28,.06); }
.muted { color: #6b7280; font-size: 12px; }

.thumbWrap{
  width: 100%;
  height: 84px;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid #eee;
  background: #f3f4f6;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}
.thumbImg{ width: 100%; height: 100%; object-fit: cover; display: block; }
.thumbPlaceholder{
  width: 100%;
  height: 100%;
  display:flex;
  align-items:center;
  justify-content:center;
  color:#6b7280;
  font-size:12px;
  font-weight:800;
}
.dur{
  position:absolute;
  right:8px;
  bottom:8px;
  background: rgba(0,0,0,.72);
  color:#fff;
  font-size:12px;
  font-weight:800;
  padding: 2px 6px;
  border-radius: 6px;
}

.pager{
  margin-top: 12px;
  display:flex;
  gap:10px;
  align-items:center;
  justify-content:flex-end;
  flex-wrap:wrap;
}
.pagerInfo{ font-size: 13px; color:#374151; font-weight: 800; }

.subCard{
  margin-top: 14px;
  padding: 12px;
  border: 1px solid #e5e7eb;
  background: #fbfbfd;
  border-radius: 14px;
}
.subTitle{ font-weight: 900; margin-bottom: 10px; }
.subOps{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.subOk{ color:#065f46; font-weight: 900; font-size: 13px; }
.subErrBox{
  margin-top: 10px;
  border: 1px solid rgba(185,28,28,.25);
  background: rgba(185,28,28,.06);
  border-radius: 12px;
  padding: 10px;
}
.subErrTitle{ color:#991b1b; font-weight: 900; margin-bottom: 6px; }
.subErrList{ margin: 0; padding-left: 18px; color:#991b1b; font-size: 13px; }

@media (max-width: 860px) {
  .thead { display: none; }
  .rowItem { grid-template-columns: 1fr; }
  .thumbWrap{ height: 180px; }
  .pager{ justify-content: flex-start; }
}
</style>