<template>
  <div class="t2">
    <!-- Top bar -->
    <div class="topbar">
      <div class="left">老師：{{ teacher?.name || "—" }}</div>
      <div class="right">
        <button class="btn" @click="openCreateUnit">新增單元</button>
        <button class="btn ghost" @click="logout">登出</button>
      </div>
    </div>

    <div class="layout">
      <!-- Sidebar -->
      <aside class="sidebar">
        <div class="navitem active">總覽</div>
        <div class="navitem" @click="goUpload()">📁 影片管理</div>
        <div class="navitem" @click="go('/admin/subtitles')">字幕/逐字稿</div>
        <div class="navitem" @click="go('/admin/bank')">題庫</div>
        <div class="navitem" @click="go('/admin/analytics')">分析</div>
      </aside>

      <!-- Main -->
      <main class="main">
        <!-- ✅ ① 快速功能（新增：不改你原本內容） -->
        <section class="card">
          <div class="card-title">快速功能</div>
          <div class="quick">
            <button class="qcard" type="button" @click="goUpload()">
              <div class="qtitle">📁 影片管理</div>
              <div class="qdesc">上傳影片、啟用/停用、管理縮圖與字幕</div>
            </button>

            <button class="qcard" type="button" @click="go('/admin/subtitles')">
              <div class="qtitle">📝 字幕/逐字稿</div>
              <div class="qdesc">檢查時間軸、修正後再上傳</div>
            </button>

            <button class="qcard" type="button" @click="go('/admin/bank')">
              <div class="qtitle">🧩 題庫</div>
              <div class="qdesc">管理 Parsons 題目與干擾片段</div>
            </button>

            <button class="qcard" type="button" @click="go('/admin/analytics')">
              <div class="qtitle">📊 分析</div>
              <div class="qdesc">學習成效、常見錯誤概念與趨勢</div>
            </button>
          </div>
        </section>

        <!-- Overview（保留你原本，並加入② KPI） -->
        <section class="card">
          <div class="card-title">數據概覽</div>
          <div class="kpis">
            <div class="kpi">
              <div class="kpi-label">本週學習人次</div>
              <div class="kpi-value">{{ overview.weekly_sessions }}</div>
            </div>

            <div class="kpi">
              <div class="kpi-label">平均正確率</div>
              <div class="kpi-value">{{ overview.avg_accuracy }}%</div>
            </div>

            <!-- ✅ ② 新增 KPI：單元數/影片總數/練習總數 -->
            <div class="kpi">
              <div class="kpi-label">單元數</div>
              <div class="kpi-value">{{ unitsCount }}</div>
            </div>

            <div class="kpi">
              <div class="kpi-label">影片總數</div>
              <div class="kpi-value">{{ totalVideos }}</div>
            </div>

            <div class="kpi">
              <div class="kpi-label">練習總數</div>
              <div class="kpi-value">{{ totalPractices }}</div>
            </div>

            <div class="kpi wide">
              <div class="kpi-label">常見錯誤概念</div>
              <div class="chips">
                <span v-for="(x,i) in overview.top_misconceptions" :key="i" class="chip">
                  {{ mapTag(x) }}
                </span>
                <span v-if="!overview.top_misconceptions?.length" class="muted">尚無資料</span>
              </div>
            </div>
          </div>
        </section>

        <!-- Units（保留你原本，並加入③ 操作入口） -->
        <section class="card">
          <div class="card-title">單元管理</div>

          <div class="unit-row header">
            <div>單元</div><div>影片</div><div>練習</div><div></div>
          </div>

          <div v-for="u in units" :key="u.unit" class="unit-row">
            <div class="unit-name">{{ u.unit }}｜{{ u.title }}</div>
            <div>影片({{ u.videos_count }})</div>
            <div>練習({{ u.practices_count }})</div>

            <!-- ✅ ③ 新增：影片/題庫 快捷入口（不移除你原本的管理） -->
            <div class="unit-actions">
              <button class="btn small ghost2" type="button" @click="goUploadUnit(u.unit)">影片</button>
              <button class="btn small" type="button" @click="goBankUnit(u.unit)">題庫</button>

              <!-- ✅ 你原本的管理按鈕保留 -->
              <button class="btn small" type="button" @click="manageUnit(u.unit)">管理</button>
            </div>
          </div>

          <div v-if="!units.length" class="muted">尚未建立單元</div>
        </section>
      </main>
    </div>

    <!-- Create Unit Modal (簡易版) -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate=false">
      <div class="modal">
        <div class="modal-title">新增單元</div>
        <div class="form">
          <label>Unit（例如 U3）</label>
          <input v-model="form.unit" placeholder="U3" />
          <label>標題</label>
          <input v-model="form.title" placeholder="條件判斷" />
          <label>描述（可選）</label>
          <textarea v-model="form.description" rows="3"></textarea>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" @click="showCreate=false">取消</button>
          <button class="btn" @click="createUnit">建立</button>
        </div>
        <div v-if="err" class="err">{{ err }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { useRouter, useRoute } from "vue-router";

const API_BASE = "http://127.0.0.1:5000";
const router = useRouter();
const route = useRoute();

const teacher = ref(null);
const overview = reactive({ weekly_sessions: 0, avg_accuracy: 0, top_misconceptions: [] });
const units = ref([]);

const showCreate = ref(false);
const form = reactive({ unit: "", title: "", description: "" });
const err = ref("");
const selectedUnit = computed(() => route.query.unit || "");

// ✅ 你原本註解掉的 watch / fetchVideos 我不動（照你的要求）
// watch(
//   () => route.query.unit,
//   () => fetchVideos(),
//   { immediate: true }
// );

function mapTag(tag) {
  const map = {
    "float_vs_int": "輸入型別（float/int）",
    "need_2dp": "輸出格式（小數兩位）",
    "perimeter_missing_2": "周長乘以2",
    "loop_condition": "迴圈條件",
    "divmod": "整除/餘數",
  };
  return map[tag] || tag;
}

function go(path) { router.push(path); }

function goUnitDetail(unit) {
  router.push(`/admin/units/${unit}`);
}

function openCreateUnit() {
  err.value = "";
  form.unit = ""; form.title = ""; form.description = "";
  showCreate.value = true;
}

async function loadDashboard() {
  const res = await fetch(`${API_BASE}/api/teacher_dashboard?range=week`);
  const data = await res.json();
  if (!data.ok) return;

  teacher.value = data.teacher;
  overview.weekly_sessions = data.overview.weekly_sessions || 0;
  overview.avg_accuracy = data.overview.avg_accuracy || 0;
  overview.top_misconceptions = data.overview.top_misconceptions || [];
  units.value = data.units || [];
}

/** ✅ ② KPI 新增：不改你原本資料結構，直接由 units 計算 */
const unitsCount = computed(() => units.value.length);

const totalVideos = computed(() =>
  units.value.reduce((sum, u) => sum + Number(u.videos_count || 0), 0)
);

const totalPractices = computed(() =>
  units.value.reduce((sum, u) => sum + Number(u.practices_count || 0), 0)
);

async function createUnit() {
  err.value = "";
  const res = await fetch(`${API_BASE}/api/teacher_dashboard/units`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(form),
  });
  const data = await res.json();
  if (!data.ok) {
    err.value = data.message || "建立失敗";
    return;
  }
  showCreate.value = false;
  await loadDashboard();
}

function logout() {
  localStorage.removeItem("user");
  router.push("/login");
}

onMounted(loadDashboard);

function goVideos() {
  router.push("/admin/upload");
}

function goUpload() {
  router.push("/admin/upload");
}

/** ✅ ③ 單元快捷入口（新增，不影響你原本流程） */
function goUploadUnit(unit) {
  router.push({ path: "/admin/upload", query: { unit } });
}

function goBankUnit(unit) {
  router.push({ path: "/admin/bank", query: { unit } });
}

/** ✅ 保留你 template 的 manageUnit(u.unit) 呼叫：提供預設實作避免報錯
 * 你如果原本有自己的「單元管理頁」，把這裡改成你的路徑即可。
 */
function manageUnit(unit) {
  // 最保守：先導到單元詳細頁（你已有 goUnitDetail 的概念）
  router.push({ path: `/admin/units/${unit}` });
}

// async function fetchVideos() {
//   const unit = selectedUnit.value;
//   const url = unit
//     ? `${API_BASE}/api/admin_upload/videos?unit=${encodeURIComponent(unit)}`
//     : `${API_BASE}/api/admin_upload/videos`;

//   const res = await fetch(url);
//   const data = await res.json();
//   videos.value = data.videos || [];
// }
</script>

<style scoped>
.t2 { padding: 16px; }
.topbar{
  display:flex; justify-content:space-between; align-items:center;
  border:2px solid #000; border-radius:12px; padding:12px 14px;
}
.layout{ display:grid; grid-template-columns: 220px 1fr; gap:14px; margin-top:14px; }
.sidebar{
  border:2px solid #000; border-radius:12px; padding:10px;
  height: calc(100vh - 120px); position: sticky; top: 14px;
}
.navitem{ padding:10px 12px; border-radius:10px; cursor:pointer; font-weight:800; }
.navitem:hover{ background:#f3f3f3; }
.navitem.active{ background:#e9f3ff; }
.main{ display:grid; gap:14px; }

.card{ border:2px solid #000; border-radius:12px; padding:14px; }
.card-title{ font-size:18px; font-weight:900; margin-bottom:10px; }

.kpis{ display:grid; grid-template-columns: 1fr 1fr; gap:10px; }
.kpi{ border:1px solid #ddd; border-radius:12px; padding:12px; }
.kpi.wide{ grid-column: 1 / -1; }
.kpi-label{ color:#666; font-weight:700; }
.kpi-value{ font-size:26px; font-weight:900; margin-top:4px; }
.chips{ display:flex; gap:8px; flex-wrap:wrap; margin-top:6px; }
.chip{ background:#f2f2f2; border-radius:999px; padding:6px 10px; font-weight:800; }
.muted{ color:#777; font-weight:700; }

.unit-row{
  display:grid; grid-template-columns: 1.6fr .8fr .8fr .6fr;
  gap:10px; align-items:center;
  padding:10px 0; border-top:1px solid #eee;
}
.unit-row.header{ border-top:0; color:#666; font-weight:900; }
.unit-name{ font-weight:900; }

.btn{ padding:8px 12px; border:0; border-radius:10px; cursor:pointer; font-weight:900; background:#3b82f6; color:#fff; }
.btn.ghost{ background:#f2f2f2; color:#111; }
.btn.small{ padding:6px 10px; border-radius:10px; }

/* ✅ 新增：單元操作區排版 */
.unit-actions{
  display:flex;
  gap:8px;
  justify-content:flex-end;
  flex-wrap:wrap;
}

/* ✅ 新增：小按鈕 ghost 版 */
.btn.ghost2{
  background:#fff;
  color:#111;
  border:1px solid #ddd;
}

/* ✅ ① 快速功能區 */
.quick{
  display:grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.qcard{
  text-align:left;
  border:1px solid #e5e7eb;
  background:#fff;
  border-radius:14px;
  padding:12px;
  cursor:pointer;
}
.qcard:hover{ background:#f9fafb; }
.qtitle{ font-weight: 900; font-size: 16px; }
.qdesc{ color:#6b7280; font-weight:700; margin-top:6px; font-size: 13px; }

@media (max-width: 1100px){
  .quick{ grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px){
  .quick{ grid-template-columns: 1fr; }
}

.modal-mask{
  position:fixed; inset:0; background:rgba(0,0,0,.35);
  display:flex; align-items:center; justify-content:center;
}
.modal{
  width:420px; background:#fff; border-radius:14px;
  padding:14px; border:2px solid #000;
}
.modal-title{ font-weight:900; font-size:18px; margin-bottom:10px; }
.form{ display:grid; gap:8px; }
input, textarea{ padding:10px; border-radius:10px; border:1px solid #ddd; }
.modal-actions{ display:flex; justify-content:flex-end; gap:10px; margin-top:12px; }
.err{ margin-top:8px; color:#c00; font-weight:800; }
</style>
