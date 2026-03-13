<template>
  <div class="t2">
    <div class="layout">
      <!-- Sidebar -->
      <TeacherSidebar active="dashboard" />

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

            <button class="qcard" type="button" @click="goSubtitle()">
              <div class="qtitle">📝 字幕/逐字稿</div>
              <div class="qdesc">檢查時間軸、修正後再上傳</div>
            </button>

            <button class="qcard" type="button" @click="goAgentlog()">
              <div class="qtitle">🧩 AI 管理生成紀錄檢視</div>
              <div class="qdesc">管理 AI 生成的 Parsons 題目與干擾片段</div>
            </button>

            <button class="qcard" type="button" @click="goAnalyze()">
              <div class="qtitle">📊 學生作答紀錄分析</div>
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

  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import TeacherSidebar from "../components/TeacherSidebar.vue";

const API_BASE = "http://127.0.0.1:5000";
const router = useRouter();

const overview = reactive({ weekly_sessions: 0, avg_accuracy: 0, top_misconceptions: [] });
const units = ref([]);

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

function goSubtitle() {
  router.push("/admin/subtitle");
}

function goAgentlog() {
  router.push("/admin/agentlog");
}

function goAnalyze() {
  router.push("/admin/analyze");
}

function goUnitDetail(unit) {
  router.push({ path: "/admin/upload", query: { unit } });
}

async function loadDashboard() {
  const res = await fetch(`${API_BASE}/api/teacher_dashboard?range=week`);
  const data = await res.json();
  if (!data.ok) return;

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
  router.push({ path: "/admin/agentlog", query: { unit } });
}

/** ✅ 保留你 template 的 manageUnit(u.unit) 呼叫：提供預設實作避免報錯
 * 你如果原本有自己的「單元管理頁」，把這裡改成你的路徑即可。
 */
function manageUnit(unit) {
  // 導向影片管理並帶入單元，避免跳到不存在的路由
  router.push({ path: "/admin/upload", query: { unit } });
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
.t2 {
  min-height: 100vh;
  background: #f5f6f8;
  padding: 0;
}

.layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 16px;
  margin: 0px;
  padding: 0px;
}

.navitem {
  padding: 11px 12px;
  border-radius: 10px;
  cursor: pointer;
  font-weight: 800;
  color: #1b1b1b;
  transition: background 0.15s ease;
}

.navitem:hover {
  background: rgba(255, 255, 255, 0.35);
}

.navitem.active {
  background: rgba(255, 255, 255, 0.5);
}

.main {
  display: grid;
  gap: 16px;
  align-content: start;
}

.card {
  background: #fff;
  border-radius: 18px;
  border: 1px solid #e4e4e4;
  padding: 16px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.05);
}

.card-title {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 900;
  color: #111;
}

.kpis {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.kpi {
  border: 1px solid #e6e6e6;
  border-radius: 12px;
  padding: 12px;
  background: #fafafa;
}

.kpi.wide {
  grid-column: 1 / -1;
}

.kpi-label {
  color: #666;
  font-weight: 700;
  font-size: 12px;
}

.kpi-value {
  font-size: 24px;
  font-weight: 900;
  margin-top: 6px;
  color: #0f172a;
}

.chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}

.chip {
  background: #fff7d6;
  border: 1px solid #f0d27a;
  border-radius: 999px;
  padding: 5px 10px;
  font-weight: 800;
  font-size: 12px;
  color: #7a5600;
}

.muted {
  color: #777;
  font-weight: 700;
}

.unit-row {
  display: grid;
  grid-template-columns: 1.6fr 0.8fr 0.8fr 0.8fr;
  gap: 10px;
  align-items: center;
  padding: 12px 0;
  border-top: 1px solid #ededed;
}

.unit-row.header {
  border-top: 0;
  color: #666;
  font-weight: 900;
  padding-top: 0;
}

.unit-name {
  font-weight: 900;
}

.btn {
  border: 1px solid rgba(0, 0, 0, 0.1);
  background: #f2c266;
  color: #1c1c1c;
  padding: 9px 13px;
  border-radius: 10px;
  cursor: pointer;
  font-weight: 900;
  transition: background 0.15s ease;
}

.btn:hover {
  background: #ebb447;
}

.btn.ghost {
  background: #fff;
  color: #111;
}

.btn.ghost:hover {
  background: #f3f4f6;
}

.btn.small {
  padding: 6px 10px;
  font-size: 12px;
}

.unit-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  flex-wrap: wrap;
}

.btn.ghost2 {
  background: #fff;
  color: #111;
  border: 1px solid #ddd;
}

.btn.ghost2:hover {
  background: #f7f7f7;
}

.quick {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.qcard {
  text-align: left;
  border: 1px solid #e5e7eb;
  background: #fff;
  border-radius: 14px;
  padding: 12px;
  cursor: pointer;
  transition: transform 0.12s ease, box-shadow 0.12s ease, background 0.12s ease;
}

.qcard:hover {
  background: #fafafa;
  transform: translateY(-1px);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.06);
}

.qtitle {
  font-weight: 900;
  font-size: 15px;
  color: #111827;
}

.qdesc {
  color: #6b7280;
  font-weight: 700;
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.45;
}

input,
textarea {
  width: 100%;
  padding: 10px;
  border-radius: 10px;
  border: 1px solid #ddd;
  font-size: 14px;
}

@media (max-width: 1180px) {
  .kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .sidebar {
    height: auto;
    position: static;
    display: flex;
    gap: 10px;
  }
}

@media (max-width: 760px) {
  .layout {
    margin-left: 12px;
    margin-right: 12px;
  }

  .quick,
  .kpis {
    grid-template-columns: 1fr;
  }

  .unit-row {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .unit-actions {
    justify-content: flex-start;
  }
}
</style>
