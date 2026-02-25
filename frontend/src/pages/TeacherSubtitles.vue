<template>
  <div class="tsPage">
    <!-- ===== Header ===== -->
    <div class="tsHeader">
      <div class="tsHeaderLeft">
        <div class="tsTitle">
          字幕/逐字稿校正：
          <span class="tsTitleStrong">{{ selectedVideoTitle || "未指定" }}</span>
        </div>
        <div class="tsMeta">
          <span class="pill">{{ selectedUnit || "未選單元" }}</span>
          <span class="pill pillGhost">video_id：{{ selectedVideoId || "—" }}</span>
          <span class="pill pillGhost">v{{ loadedVersion || "—" }}</span>
        </div>
      </div>

      <div class="tsHeaderRight">
        <button class="btnGhost" @click="goVideoManage">回影片管理</button>
        <button class="btnPrimary" :disabled="busySaving || !rows.length" @click="saveCorrected">
          {{ busySaving ? "儲存中…" : "儲存" }}
        </button>
        <button class="btn" :disabled="!subtitleText" @click="exportSrt">匯出 SRT</button>
        <button class="btn" :disabled="!subtitleText" @click="exportJson">匯出 JSON</button>
      </div>
    </div>

    <!-- ===== Top controls ===== -->
    <div class="tsControls">
      <div class="ctl">
        <label>單元</label>
        <select v-model="selectedUnit">
          <option value="">（請選擇）</option>
          <option v-for="u in units" :key="u" :value="u">{{ u }}</option>
        </select>
      </div>

      <div class="ctl">
        <label>影片</label>
        <select v-model="selectedVideoId">
          <option value="">（請選擇）</option>
          <option v-for="v in videos" :key="v._id" :value="v._id">{{ v.title || v._id }}</option>
        </select>
      </div>

      <div class="ctl">
        <label>版本</label>
        <select v-model="selectedVersion">
          <option value="">（自動：目前版本）</option>
          <option v-for="ver in versions" :key="ver.version" :value="String(ver.version)">v{{ ver.version }}</option>
        </select>
      </div>

      <div class="ctl grow">
        <label>字幕檔</label>
        <div class="uploadRow">
          <input
            type="file"
            ref="fileInputTop"
            @change="onFileSelected"
            accept=".srt,.vtt,.sub"
            :disabled="!selectedVideoId || uploading"
          />
          <button class="btnInfo" @click="uploadSubtitle" :disabled="!selectedFile || uploading">
            {{ uploading ? "上傳中…" : "上傳字幕" }}
          </button>
          <button class="btnGhost" :disabled="!selectedVersion || settingCurrent" @click="setAsCurrent">
            {{ settingCurrent ? "設定中…" : "設為目前版本" }}
          </button>
        </div>
      </div>
    </div>

    <!-- ===== Main ===== -->
    <div class="tsGrid">
      <!-- Left: rows editor -->
      <section class="tsCard tsLeft">
        <div class="tsCardHead">
          <div class="headTitle">字幕列表</div>
          <div class="headBtns">
            <button class="btn" @click="addRowNearActive" :disabled="!selectedVideoId">新增一行</button>
            <button class="btn" @click="mergeSelected" :disabled="selectedCount < 2">合併</button>
            <button class="btnDanger" @click="deleteSelected" :disabled="selectedCount < 1">刪除</button>
            <button class="btnInfo" @click="autoCorrect" :disabled="!rows.length">自動校正</button>
            <button class="btnGhost" @click="undoLast" :disabled="!canUndo">復原</button>
          </div>
        </div>

        <div v-if="!selectedVideoId" class="empty">
          請先選擇單元與影片。
        </div>

        <div v-else class="rowsWrap">
          <div v-if="!rows.length" class="empty">
            尚未載入字幕（你可以先上傳字幕檔，或選擇版本）。
          </div>

          <div v-else class="rows" ref="rowsListEl">
            <article
              v-for="(r, idx) in rows"
              :key="r._key"
              class="rowItem"
              :data-key="r._key"
              :class="{ active: activeRowKey === r._key }"
              @click.self="seekToRow(r)"
            >
              <div class="rowTop">
                <input class="chk" type="checkbox" v-model="r.selected" @click.stop />

                <input
                  class="time"
                  v-model="r.start"
                  @focus="activeRowKey = r._key"
                  @click.stop
                  placeholder="00:00:00,000"
                />
                <span class="arrow">→</span>
                <input
                  class="time"
                  v-model="r.end"
                  @focus="activeRowKey = r._key"
                  @click.stop
                  placeholder="00:00:00,000"
                />

                <textarea
                  class="txt"
                  v-model="r.text"
                  @focus="activeRowKey = r._key"
                  @click.stop
                  rows="2"
                  placeholder="輸入字幕內容…"
                ></textarea>
              </div>

              <div class="rowBottom">
                <button class="mini" @click.stop="setStartToCurrent(r)">用目前時間當開始</button>
                <button class="mini" @click.stop="setEndToCurrent(r)">用目前時間當結束</button>
                <button class="mini ghost" @click.stop="seekToRow(r)">跳到開始</button>
                <span class="hint">#{{ idx + 1 }}</span>
              </div>
            </article>
          </div>
        </div>

        <textarea v-model="subtitleText" class="hiddenTa" aria-hidden="true"></textarea>
      </section>

      <!-- Right: video -->
      <section class="tsCard tsRight">
        <div class="tsCardHead">
          <div class="headTitle">影片預覽</div>
          <div class="rightInfo">
            目前時間：<span class="mono">{{ currentTimeLabel }}</span>
          </div>
        </div>

        <div class="videoBox">
          <video
            v-if="videoUrl"
            ref="videoEl"
            class="player"
            controls
            :src="videoUrl"
            @timeupdate="onTimeUpdate"
          ></video>
          <div v-else class="empty">請先選擇影片</div>
        </div>

        <div class="note">
          點左側字幕列可跳到該時間；勾選多列可合併/刪除。
        </div>
      </section>
    </div>

    <div v-if="toast.show" class="toast">{{ toast.msg }}</div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, watch, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";

const route = useRoute();
const router = useRouter();

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000";

function makeUploadUrl(p) {
  if (!p) return "";
  const s = String(p).trim();
  if (!s) return "";
  if (/^https?:\/\//i.test(s)) return s;

  let clean = s.replace(/^\/+/, "");
  clean = clean.replace(/^uploads\//, "");
  clean = clean.replace(/^api\/admin_upload\/uploads\//, "");
  return `${API_BASE}/api/admin_upload/uploads/${clean}`;
}

/** ====== 狀態 ====== */
const units = ref([]);
const videos = ref([]);
const versions = ref([]);

const selectedUnit = ref("");
const selectedVideoId = ref("");
const selectedVideoTitle = ref("");

const selectedVersion = ref("");
const loadedVersion = ref("");

const videoUrl = ref("");
const currentSubtitlePath = ref("");
const subtitleText = ref("");

const videoEl = ref(null);
const currentTimeSec = ref(0);
const activeRowKey = ref("");

const rows = ref([]);
const rowsListEl = ref(null);

const busySaving = ref(false);
const uploading = ref(false);
const settingCurrent = ref(false);

const selectedFile = ref(null);
const errors = ref([]);

const toast = reactive({ show: false, msg: "" });

function setToast(msg, ms = 2000) {
  toast.msg = msg;
  toast.show = true;
  if (ms > 0) setTimeout(() => (toast.show = false), ms);
}

const selectedCount = computed(() => rows.value.filter((r) => r.selected).length);

const currentTimeLabel = computed(() => formatTimecode(currentTimeSec.value));

function scrollRowIntoView(rowKey) {
  requestAnimationFrame(() => {
    const root = rowsListEl.value;
    if (!root) return;
    const el = root.querySelector(`[data-key="${rowKey}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

function parseTimecode(s) {
  const str = String(s || "").trim();
  const m = str.match(/^(?:(\d{1,2}):)?(\d{2}):(\d{2})(?:[,.](\d{1,3}))?$/);
  if (!m) return NaN;

  const hh = Number(m[1] ?? 0);
  const mm = Number(m[2]);
  const ss = Number(m[3]);
  const ms = Number(String(m[4] ?? "0").padEnd(3, "0").slice(0, 3));
  if ([hh, mm, ss, ms].some((n) => !Number.isFinite(n))) return NaN;

  return hh * 3600 + mm * 60 + ss + ms / 1000;
}

function normalizeTimecode(s) {
  const str = String(s || "").trim();
  const m = str.match(/^(?:(\d{1,2}):)?(\d{2}):(\d{2})(?:[,.](\d{1,3}))?$/);
  if (!m) return String(s || "").trim();
  const hh = String(Number(m[1] ?? 0)).padStart(2, "0");
  const mm = String(Number(m[2])).padStart(2, "0");
  const ss = String(Number(m[3])).padStart(2, "0");
  const ms = String(m[4] ?? "0").padEnd(3, "0").slice(0, 3);
  return `${hh}:${mm}:${ss},${ms}`;
}

function formatTimecode(sec) {
  const s = Math.max(0, Number(sec || 0));
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = Math.floor(s % 60);
  const ms = Math.round((s - Math.floor(s)) * 1000);

  const pad2 = (n) => String(n).padStart(2, "0");
  const pad3 = (n) => String(n).padStart(3, "0");
  return `${pad2(hh)}:${pad2(mm)}:${pad2(ss)},${pad3(ms)}`;
}

function findRowKeyByTime(nowSec) {
  for (const r of rows.value) {
    const s = parseTimecode(r.start);
    const e = parseTimecode(r.end);
    if (!Number.isFinite(s)) continue;
    const end = Number.isFinite(e) ? e : s;
    if (nowSec >= s && nowSec <= end) return r._key;
  }
  return null;
}

function onTimeUpdate() {
  const t = Number(videoEl.value?.currentTime || 0);
  const now = Number.isFinite(t) ? t : 0;
  currentTimeSec.value = now;

  const hitKey = findRowKeyByTime(now);
  if (hitKey && hitKey !== activeRowKey.value) {
    activeRowKey.value = hitKey;
    scrollRowIntoView(hitKey);
  }
}

function parseSrtToRows(srtText) {
  // ✅ 更穩的 SRT 解析：支援「序號/時間/文字之間有空行」、多行字幕、無序號字幕
  const text = String(srtText || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = text.split("\n");
  const out = [];

  let i = 0;
  while (i < lines.length) {
    while (i < lines.length && lines[i].trim() === "") i++;
    if (i >= lines.length) break;

    if (/^\d+$/.test(lines[i].trim())) {
      i++;
      while (i < lines.length && lines[i].trim() === "") i++;
      if (i >= lines.length) break;
    }

    const timeLine = lines[i].trim();
    const tm = timeLine.match(
      /((?:(?:\d{1,2}:)?\d{2}:\d{2})(?:[,.]\d{1,3})?)\s*-->\s*((?:(?:\d{1,2}:)?\d{2}:\d{2})(?:[,.]\d{1,3})?)/
    );
    if (!tm) {
      i++;
      continue;
    }

    const start = normalizeTimecode(tm[1].replace(".", ","));
    const end = normalizeTimecode(tm[2].replace(".", ","));
    i++;

    while (i < lines.length && lines[i].trim() === "") i++;

    const textLines = [];
    while (i < lines.length) {
      const cur = lines[i];

      if (/^\d+$/.test(cur.trim())) {
        let j = i + 1;
        while (j < lines.length && lines[j].trim() === "") j++;
        if (j < lines.length && lines[j].includes("-->")) break;
      }
      if (cur.includes("-->")) break;

      if (cur.trim() === "") {
        i++;
        continue;
      }

      textLines.push(cur);
      i++;
    }

    out.push({
      _key: `${Date.now()}_${Math.random().toString(16).slice(2)}`,
      start,
      end,
      text: textLines.join("\n").trim(),
      selected: false,
    });
  }

  return out;
}

function buildRowsToSrt(list) {
  const parts = (list || []).map((r, idx) => {
    const start = (r.start || "").replace(".", ",").trim();
    const end = (r.end || "").replace(".", ",").trim();
    const body = String(r.text || "").replace(/\r\n/g, "\n").trimEnd();
    return `${idx + 1}\n${start} --> ${end}\n${body}\n`;
  });
  return parts.join("\n").trim() + "\n";
}

const _undo = ref(null);
const canUndo = computed(() => !!_undo.value);

function snapshotForUndo() {
  _undo.value = JSON.parse(JSON.stringify(rows.value));
}
function undoLast() {
  if (!_undo.value) return;
  rows.value = JSON.parse(JSON.stringify(_undo.value));
  _undo.value = null;
  setToast("↩️ 已復原上一個狀態");
}

function applySubtitleTextToRows(text) {
  subtitleText.value = String(text || "");
  const parsed = parseSrtToRows(subtitleText.value);

  if (rows.value.length) {
    for (let i = 0; i < Math.min(rows.value.length, parsed.length); i++) {
      parsed[i]._key = rows.value[i]._key;
    }
  }
  rows.value = parsed;
}

watch(
  rows,
  (val) => {
    subtitleText.value = buildRowsToSrt(val);
  },
  { deep: true }
);

function seekToRow(r) {
  const t = parseTimecode(r?.start);
  if (!videoEl.value || !Number.isFinite(t)) return;
  activeRowKey.value = r._key;
  scrollRowIntoView(r._key);
  videoEl.value.currentTime = Math.max(0, t);
  videoEl.value.play?.().catch(() => {});
}

function setStartToCurrent(r) {
  r.start = formatTimecode(currentTimeSec.value);
}
function setEndToCurrent(r) {
  r.end = formatTimecode(currentTimeSec.value);
}

function addRowNearActive() {
  const key = `${Date.now()}_${Math.random().toString(16).slice(2)}`;
  const t = formatTimecode(currentTimeSec.value);
  const newRow = { _key: key, start: t, end: t, text: "", selected: false };

  const activeKey = activeRowKey.value;
  if (activeKey) {
    const idx = rows.value.findIndex((x) => x._key === activeKey);
    if (idx >= 0) {
      rows.value.splice(idx + 1, 0, newRow);
      activeRowKey.value = key;
      scrollRowIntoView(key);
      return;
    }
  }

  rows.value.push(newRow);
  activeRowKey.value = key;
  scrollRowIntoView(key);
}

function deleteSelected() {
  rows.value = rows.value.filter((r) => !r.selected);
}

function mergeSelected() {
  const picked = rows.value.filter((r) => r.selected);
  if (picked.length < 2) return;

  const pickedSorted = picked.slice().sort((a, b) => {
    const ta = parseTimecode(a.start);
    const tb = parseTimecode(b.start);
    return (Number.isFinite(ta) ? ta : 0) - (Number.isFinite(tb) ? tb : 0);
  });

  const first = pickedSorted[0];
  const last = pickedSorted[pickedSorted.length - 1];

  first.text = pickedSorted
    .map((r) => String(r.text || "").trim())
    .filter(Boolean)
    .join("\n");
  first.end = last.end;
  first.selected = false;

  const keepKey = first._key;
  rows.value = rows.value.filter((r) => !r.selected || r._key === keepKey);
}

function autoCorrect() {
  snapshotForUndo();

  const fixed = rows.value
    .map((r) => ({
      ...r,
      start: String(r.start || "").replace(".", ",").trim(),
      end: String(r.end || "").replace(".", ",").trim(),
      text: String(r.text || "").trimEnd(),
    }))
    .sort((a, b) => {
      const ta = parseTimecode(a.start);
      const tb = parseTimecode(b.start);
      return (Number.isFinite(ta) ? ta : 0) - (Number.isFinite(tb) ? tb : 0);
    })
    .map((r) => {
      const s = parseTimecode(r.start);
      const e = parseTimecode(r.end);
      if (Number.isFinite(s) && Number.isFinite(e) && e < s) {
        r.end = formatTimecode(s);
      }
      return r;
    });

  rows.value = fixed;
  setToast("✅ 已自動校正");
}

/** ====== fetch helpers ====== */
async function getJSON(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` };
    return await res.json();
  } catch (e) {
    return { ok: false, message: e?.message || String(e) };
  }
}
async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, data };
}
async function postFormData(url, formData) {
  const res = await fetch(url, { method: "POST", body: formData });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, data };
}

/** ====== API ====== */
async function loadUnits() {
  const data = await getJSON(`${API_BASE}/api/subtitle/units`);
  if (data.ok) units.value = data.units || [];
  else setToast("❌ 載入單元失敗: " + (data.message || "未知錯誤"));
}

async function loadVideos() {
  if (!selectedUnit.value) {
    videos.value = [];
    return;
  }
  const url = `${API_BASE}/api/subtitle/videos?unit=${encodeURIComponent(selectedUnit.value)}`;
  const data = await getJSON(url);
  if (data.ok) videos.value = data.videos || [];
  else {
    videos.value = [];
    setToast("❌ 載入影片失敗: " + (data.message || "未知錯誤"));
  }
}

async function loadVersions() {
  if (!selectedVideoId.value) {
    versions.value = [];
    return;
  }
  const url = `${API_BASE}/api/subtitle/versions?video_id=${encodeURIComponent(selectedVideoId.value)}`;
  const data = await getJSON(url);
  versions.value = data.ok ? (data.versions || []) : [];
}

async function loadContent() {
  const vid = String(selectedVideoId.value || "").trim() || String(route.query.video_id || "").trim();
  if (!vid) return;

  const v = selectedVersion.value ? `&version=${encodeURIComponent(selectedVersion.value)}` : "";
  const url = `${API_BASE}/api/subtitle/content?video_id=${encodeURIComponent(vid)}${v}`;
  const data = await getJSON(url);

  if (!data.ok) {
    setToast("❌ " + (data.message || "載入字幕失敗"));
    rows.value = [];
    subtitleText.value = "";
    currentSubtitlePath.value = "";
    loadedVersion.value = "";
    return;
  }

  currentSubtitlePath.value = data.path || "";
  loadedVersion.value = data.version != null ? String(data.version) : "";

  if (Array.isArray(data.blocks) && data.blocks.length) {
    rows.value = data.blocks.map((b, i) => ({
      _key: `${Date.now()}_${i}_${Math.random().toString(16).slice(2)}`,
      start: b.start || b.s || "",
      end: b.end || b.e || "",
      text: b.text || b.t || b.content || b.line || "",
      selected: false,
    }));
    subtitleText.value = buildRowsToSrt(rows.value);
    _undo.value = null;
    return;
  }

  subtitleText.value = data.text || "";
  applySubtitleTextToRows(subtitleText.value);
  _undo.value = null;
}

/** ====== 上傳/儲存/匯出 ====== */
async function saveCorrected() {
  if (!selectedVideoId.value) return setToast("❌ 請先選影片");
  if (!subtitleText.value) return setToast("❌ 沒有可儲存的字幕內容");

  busySaving.value = true;
  try {
    const blob = new Blob([subtitleText.value], { type: "text/plain;charset=utf-8" });
    const filename = `${(selectedVideoTitle.value || "subtitle").replace(/\s+/g, "_")}_corrected.srt`;
    const file = new File([blob], filename, { type: "text/plain" });

    const fd = new FormData();
    fd.append("video_id", selectedVideoId.value);
    fd.append("file", file);

    const { ok, data } = await postFormData(`${API_BASE}/api/subtitle/upload`, fd);
    if (ok && data.ok) {
      setToast("✅ 已儲存為新版本");
      await loadVersions();
      selectedVersion.value = ""; // 回到目前版本
      await loadContent();
    } else {
      setToast(`❌ ${data.message || "儲存失敗"}`);
    }
  } catch (e) {
    setToast("❌ 儲存出錯");
  } finally {
    busySaving.value = false;
  }
}

function onFileSelected(e) {
  selectedFile.value = e.target.files?.[0] || null;
  errors.value = [];
}

async function uploadSubtitle() {
  if (!selectedFile.value || !selectedVideoId.value) return setToast("❌ 請先選擇影片和字幕檔案");
  uploading.value = true;

  const fd = new FormData();
  fd.append("video_id", selectedVideoId.value);
  fd.append("file", selectedFile.value);

  try {
    const { ok, data } = await postFormData(`${API_BASE}/api/subtitle/upload`, fd);
    if (ok && data.ok) {
      setToast("✅ 字幕檔案上傳成功");
      selectedFile.value = null;
      await loadVersions();

      if (data?.version != null) selectedVersion.value = String(data.version);
      else if (data?.new_version != null) selectedVersion.value = String(data.new_version);

      await loadContent();
    } else {
      setToast("❌ 上傳失敗");
    }
  } finally {
    uploading.value = false;
  }
}

async function setAsCurrent() {
  if (!selectedVideoId.value || !selectedVersion.value) return setToast("❌ 請先選擇版本");
  settingCurrent.value = true;
  try {
    const { ok, data } = await postJSON(`${API_BASE}/api/subtitle/set_current`, {
      video_id: selectedVideoId.value,
      version: Number(selectedVersion.value),
    });
    if (ok && data.ok) {
      setToast("✅ 已設為目前版本");
      await loadVersions();
      await loadContent();
    } else setToast(`❌ ${data.message || "設定失敗"}`);
  } finally {
    settingCurrent.value = false;
  }
}

function exportSrt() {
  if (!subtitleText.value) return setToast("❌ 無字幕可匯出");
  const name = (selectedVideoTitle.value || "subtitle").replace(/\s+/g, "_");
  const blob = new Blob([subtitleText.value], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${name}.srt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setToast("✅ 已匯出 SRT");
}

function exportJson() {
  const payload = { video_id: selectedVideoId.value, version: loadedVersion.value || null, text: subtitleText.value };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${(selectedVideoTitle.value || "subtitle").replace(/\s+/g, "_")}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setToast("✅ 已匯出 JSON");
}

function goVideoManage() {
  router.push("/admin/videos");
}

/** ====== 監看：單元/影片/版本同步（影片預覽 + 版本字幕）===== */
watch(selectedUnit, async () => {
  selectedVideoId.value = "";
  selectedVideoTitle.value = "";
  selectedVersion.value = "";
  loadedVersion.value = "";
  videoUrl.value = "";
  rows.value = [];
  subtitleText.value = "";
  versions.value = [];
  await loadVideos();
});

watch(selectedVideoId, async () => {
  const vid = String(selectedVideoId.value || "").trim();
  if (!vid) return;

  // sync title + video url
  const v = (videos.value || []).find((x) => String(x._id) === vid);
  selectedVideoTitle.value = v?.title || vid;

  const vPath = v?.video_url || v?.path || String(route.query.video_url || "").trim();
  videoUrl.value = makeUploadUrl(vPath);

  await nextTick();
  try {
    videoEl.value?.pause?.();
    videoEl.value && (videoEl.value.currentTime = 0);
  } catch {}

  await loadVersions();

  // 如果目前沒選版本：自動使用目前版本或最新
  if (!String(selectedVersion.value || "").trim() && versions.value.length) {
    const current = versions.value.find((x) => x.is_current);
    selectedVersion.value = String((current?.version ?? versions.value[versions.value.length - 1]?.version) ?? "");
  }

  await loadContent();
});

watch(selectedVersion, async () => {
  const vid = String(selectedVideoId.value || "").trim();
  if (!vid) return;
  await loadContent();
});

/** ====== 初始化：支援從 AdminUpload 點「字幕校正」帶入影片 ====== */
onMounted(async () => {
  await loadUnits();

  const qUnit = String(route.query.unit || "").trim();
  const qVideoId = String(route.query.video_id || "").trim();
  const qTitle = String(route.query.title || "").trim();
  const qVideoUrl = String(route.query.video_url || "").trim();

  if (qUnit) selectedUnit.value = qUnit;
  if (qTitle) selectedVideoTitle.value = qTitle;
  if (qVideoUrl) videoUrl.value = makeUploadUrl(qVideoUrl);

  if (selectedUnit.value) await loadVideos();

  if (qVideoId) {
    selectedVideoId.value = qVideoId; // 會觸發 watch(selectedVideoId) 自動載版本/字幕
  }
});
</script>

<style scoped>
/* ========= page layout ========= */
.tsPage {
  padding: 16px;
  background: #ffffff;
  min-height: 100%;
  box-sizing: border-box;
}

/* ========= header ========= */
.tsHeader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 2px solid #111;
  border-radius: 16px;
  padding: 14px 16px;
}

.tsHeaderLeft { display: flex; flex-direction: column; gap: 6px; }
.tsTitle { font-weight: 800; font-size: 20px; }
.tsTitleStrong { font-weight: 900; }
.tsMeta { display: flex; gap: 8px; flex-wrap: wrap; }

.pill {
  display: inline-flex;
  align-items: center;
  border: 1px solid #111;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  background: #fff;
}
.pillGhost { background: #f6f6f6; }

.tsHeaderRight { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

/* ========= controls row ========= */
.tsControls {
  margin-top: 12px;
  border: 2px solid #111;
  border-radius: 16px;
  padding: 12px;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.ctl { display: flex; flex-direction: column; gap: 6px; min-width: 170px; }
.ctl.grow { flex: 1; min-width: 280px; }
.ctl label { font-size: 12px; opacity: 0.85; }
.ctl select, .ctl input[type="file"] {
  border: 1px solid #111;
  border-radius: 10px;
  padding: 8px 10px;
  background: #fff;
}

.uploadRow { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

/* ========= main grid ========= */
.tsGrid {
  margin-top: 12px;
  display: grid;
  grid-template-columns: 1.2fr 0.8fr;
  gap: 14px;
}

.tsCard {
  border: 2px solid #111;
  border-radius: 16px;
  background: #fff;
  overflow: hidden;
}

.tsCardHead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 2px solid #111;
  background: #fff;
}

.headTitle { font-weight: 800; }
.headBtns { display: flex; gap: 10px; flex-wrap: wrap; }
.rightInfo { font-size: 14px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }

/* ========= rows editor ========= */
.rowsWrap { padding: 12px; }
.rows {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 560px;
  overflow: auto;
  padding-right: 4px;
}

.rowItem {
  border: 1px solid #ddd;
  border-radius: 14px;
  padding: 10px 10px 8px;
  background: #fff;
}
.rowItem.active { outline: 2px solid #111; }
.rowTop {
  display: grid;
  grid-template-columns: 26px 150px 24px 150px 1fr;
  gap: 10px;
  align-items: start;
}

.chk { width: 16px; height: 16px; margin-top: 8px; }

.time {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  border: 1px solid #111;
  border-radius: 10px;
  padding: 8px 10px;
  height: 36px;
  box-sizing: border-box;
}

.arrow { display: inline-flex; align-items: center; justify-content: center; margin-top: 6px; }

.txt {
  border: 1px solid #111;
  border-radius: 12px;
  padding: 8px 10px;
  width: 100%;
  resize: vertical;
  min-height: 54px;
  box-sizing: border-box;
}

.rowBottom {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.hint { margin-left: auto; font-size: 12px; opacity: 0.7; }

.empty {
  padding: 16px;
  opacity: 0.75;
}

.hiddenTa {
  position: absolute;
  left: -9999px;
  top: -9999px;
  width: 1px;
  height: 1px;
  opacity: 0;
}

/* ========= video ========= */
.videoBox { padding: 12px; }
.player {
  width: 100%;
  max-height: 360px;
  border-radius: 12px;
  background: #000;
}
.note {
  padding: 0 12px 12px;
  opacity: 0.8;
  font-size: 13px;
}

/* ========= buttons ========= */
.btn, .btnInfo, .btnGhost, .btnPrimary, .btnDanger, .mini {
  border: 1px solid #111;
  border-radius: 999px;
  padding: 8px 12px;
  background: #fff;
  cursor: pointer;
  font-weight: 700;
}
.btnPrimary {
  background: #1f67ff;
  color: #fff;
  border-color: #1f67ff;
}
.btnInfo { background: #f0f6ff; }
.btnGhost { background: #f6f6f6; }
.btnDanger { background: #ffecec; border-color: #ff4d4f; }
.mini {
  padding: 6px 10px;
  font-weight: 700;
  font-size: 12px;
}
.mini.ghost { background: #f6f6f6; }

.btn:disabled, .btnInfo:disabled, .btnGhost:disabled, .btnPrimary:disabled, .btnDanger:disabled, .mini:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ========= toast ========= */
.toast {
  position: fixed;
  right: 18px;
  bottom: 18px;
  background: rgba(20,20,20,0.92);
  color: #fff;
  padding: 10px 14px;
  border-radius: 12px;
  z-index: 50;
  max-width: 70vw;
  font-size: 14px;
}

/* ========= responsive ========= */
@media (max-width: 1100px) {
  .tsGrid { grid-template-columns: 1fr; }
}
</style>
