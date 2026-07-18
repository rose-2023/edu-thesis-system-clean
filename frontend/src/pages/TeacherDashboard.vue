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
          <div class="card-head">
            <div>
              <div class="card-title">數據概覽</div>
              <div class="card-subtitle">{{ dateRangeText }}</div>
            </div>
            <div class="date-filter">
              <label>
                <span>開始日期</span>
                <input v-model="startDate" type="date" @change="loadDashboard" />
              </label>
              <label>
                <span>結束日期</span>
                <input v-model="endDate" type="date" @change="loadDashboard" />
              </label>
              <button class="btn small" type="button" :disabled="dashboardLoading" @click="loadDashboard">
                {{ dashboardLoading ? "查詢中" : "查詢" }}
              </button>
            </div>
          </div>
          <p v-if="dashboardError" class="error-text">{{ dashboardError }}</p>
          <div class="kpis">
            <div class="kpi">
              <div class="kpi-label">期間學習人數</div>
              <div class="kpi-value">{{ periodLearners }}</div>
              <div class="kpi-note">依 learning_logs 操作歷程統計</div>
            </div>

            <div class="kpi">
              <div class="kpi-label">單元數</div>
              <div class="kpi-value">{{ unitsCount }}</div>
            </div>

            <div class="kpi">
              <div class="kpi-label">影片總數</div>
              <div class="kpi-value">{{ totalVideos }}</div>
            </div>

            <div class="kpi">
              <div class="kpi-label">練習題總數</div>
              <div class="kpi-value">{{ totalPractices }}</div>
            </div>

            <div class="kpi">
              <div class="kpi-label">前測題總數</div>
              <div class="kpi-value">{{ pretestQuestionCount }}</div>
            </div>

            <div class="kpi">
              <div class="kpi-label">後測題總數</div>
              <div class="kpi-value">{{ posttestQuestionCount }}</div>
            </div>
          </div>
        </section>

        <!-- 單元與題庫 -->
        <section class="card card-wide">
          <div class="card-head dashboard-head">
            <div>
              <div class="card-title">單元與題庫</div>
              <div class="card-subtitle">名稱沿用 TeacherT5AgentLog.vue 的單元命名</div>
            </div>
            <div class="chips">
              <span class="chip">{{ selectedUnitRecord?.name || "請先選擇單元" }}</span>
              <span class="chip">影片 {{ selectedUnitRecord?.videos_count || 0 }}</span>
              <span class="chip">題庫 {{ selectedUnitRecord?.practices_count || 0 }}</span>
            </div>
          </div>

          <div class="dashboard-split">
            <div class="unit-panel">
              <div class="unit-row header">
                <div>單元</div><div>影片</div><div>題庫</div><div></div>
              </div>

              <div
                v-for="u in units"
                :key="u.unit"
                class="unit-row"
                :class="{ selected: selectedUnit === u.unit }"
                @click="selectedUnit = u.unit"
              >
                <div class="unit-name">
                  <div class="unit-title">{{ u.name }}</div>
                  <div class="unit-raw">{{ u.unit }}</div>
                </div>
                <div>影片({{ u.videos_count }})</div>
                <div>題庫({{ u.practices_count }})</div>

                <div class="unit-actions">
                  <button class="btn small ghost2" type="button" @click.stop="goUploadUnit(u.unit)">影片</button>
                  <button class="btn small" type="button" @click.stop="goBankUnit(u.unit)">題庫</button>
                  <button class="btn small" type="button" @click.stop="manageUnit(u.unit)">管理</button>
                </div>
              </div>

              <div v-if="!units.length" class="muted">尚未建立單元</div>
            </div>

            <div class="preview-panel">
              <div class="preview-head">
                <div>
                  <div class="preview-title">題庫預覽</div>
                  <div class="preview-subtitle">{{ selectedUnitRecord?.name || "請先點選左側單元" }}</div>
                </div>
                <button
                  class="btn small"
                  type="button"
                  :disabled="!selectedUnit"
                  @click="goBankUnit(selectedUnit)"
                >
                  查看題庫
                </button>
              </div>

              <div v-if="selectedUnitRecord?.task_preview?.length" class="preview-list">
                <div class="preview-row preview-row-header">
                  <div>題目</div>
                  <div>狀態</div>
                  <div>建立</div>
                </div>

                <div v-for="task in selectedUnitRecord.task_preview" :key="task.task_id" class="preview-row">
                  <div class="task-title">
                    <div class="mono">{{ task.task_code || task.task_id }}</div>
                    <div class="task-sub">{{ task.title || "未命名題目" }}</div>
                  </div>
                  <div>
                    <span class="status-pill" :class="task.enabled ? 'status-on' : 'status-off'">
                      {{ task.enabled ? "啟用" : "停用" }}
                    </span>
                  </div>
                  <div class="task-time">{{ task.created_at || "—" }}</div>
                </div>
              </div>

              <div v-else class="muted">這個單元目前沒有題庫預覽</div>
            </div>
          </div>
        </section>
      </main>
    </div>

  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import TeacherSidebar from "../components/TeacherSidebar.vue";

const API_BASE = import.meta.env.VITE_API_BASE || "";
const router = useRouter();

const overview = reactive({
  weekly_sessions: 0,
  active_learners: 0,
});
const resourceCounts = reactive({
  unit_count: 0,
  video_count: 0,
  practice_task_count: 0,
  pretest_question_count: 0,
  posttest_question_count: 0,
});
const units = ref([]);
const selectedUnit = ref("");
const startDate = ref("");
const endDate = ref("");
const dashboardLoading = ref(false);
const dashboardError = ref("");

const selectedUnitRecord = computed(() => (
  units.value.find((u) => String(u.unit || "") === String(selectedUnit.value || "")) || null
));

// ✅ 你原本註解掉的 watch / fetchVideos 我不動（照你的要求）
// watch(
//   () => route.query.unit,
//   () => fetchVideos(),
//   { immediate: true }
// );

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

function dateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function setDefaultDateRange() {
  const today = new Date();
  const start = new Date(today);
  start.setDate(today.getDate() - 6);
  startDate.value = dateInputValue(start);
  endDate.value = dateInputValue(today);
}

async function loadDashboard() {
  dashboardLoading.value = true;
  dashboardError.value = "";
  try {
    const params = new URLSearchParams();
    if (startDate.value) params.set("start_date", startDate.value);
    if (endDate.value) params.set("end_date", endDate.value);
    if (!startDate.value && !endDate.value) params.set("range", "week");
    params.set("_ts", String(Date.now()));

    const res = await fetch(`${API_BASE}/api/teacher_dashboard?${params.toString()}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error(data?.message || `Dashboard 載入失敗：${res.status}`);
    }
    if (!data.date_range || !data.resource_counts) {
      throw new Error("Dashboard 後端仍是舊版，請重新啟動後端服務後再查詢。");
    }

    overview.weekly_sessions = data.overview.weekly_sessions || 0;
    overview.active_learners = data.overview.active_learners ?? overview.weekly_sessions;
    resourceCounts.unit_count = data.resource_counts.unit_count || 0;
    resourceCounts.video_count = data.resource_counts.video_count || 0;
    resourceCounts.practice_task_count = data.resource_counts.practice_task_count || 0;
    resourceCounts.pretest_question_count = data.resource_counts.pretest_question_count || 0;
    resourceCounts.posttest_question_count = data.resource_counts.posttest_question_count || 0;
    units.value = (data.units || []).map((u) => ({
      ...u,
      unit: u.unit || u.raw_name || "",
      raw_name: u.raw_name || u.unit || "",
      name: u.name || u.unit_label || u.title || u.unit || "",
      unit_label: u.unit_label || u.name || u.title || u.unit || "",
      task_preview: Array.isArray(u.task_preview) ? u.task_preview : [],
    }));

    if (!selectedUnit.value || !units.value.some((u) => String(u.unit) === String(selectedUnit.value))) {
      selectedUnit.value = units.value[0]?.unit || "";
    }

    if (data.date_range?.start_date) startDate.value = data.date_range.start_date;
    if (data.date_range?.end_date) endDate.value = data.date_range.end_date;
  } catch (error) {
    dashboardError.value = error?.message || String(error);
  } finally {
    dashboardLoading.value = false;
  }
}

const unitsCount = computed(() => resourceCounts.unit_count || units.value.length);

const totalVideos = computed(() => resourceCounts.video_count || 0);

const totalPractices = computed(() => resourceCounts.practice_task_count || 0);

const pretestQuestionCount = computed(() => resourceCounts.pretest_question_count || 0);

const posttestQuestionCount = computed(() => resourceCounts.posttest_question_count || 0);

const periodLearners = computed(() => overview.active_learners ?? overview.weekly_sessions ?? 0);

const dateRangeText = computed(() => (
  startDate.value && endDate.value
    ? `目前統計期間：${startDate.value} 至 ${endDate.value}`
    : "目前統計期間：最近 7 天"
));

onMounted(() => {
  setDefaultDateRange();
  loadDashboard();
});

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

.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.card-head .card-title {
  margin-bottom: 4px;
}

.card-subtitle {
  color: #64748b;
  font-size: 13px;
  font-weight: 700;
}

.card-wide {
  grid-column: 1 / -1;
}

.dashboard-head {
  align-items: center;
}

.dashboard-split {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
  gap: 14px;
}

.unit-panel,
.preview-panel {
  border: 1px solid #e8ecf2;
  border-radius: 14px;
  background: #fcfdff;
  padding: 12px;
}

.preview-panel {
  display: grid;
  gap: 12px;
  align-content: start;
}

.preview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.preview-title {
  font-weight: 900;
  color: #111;
}

.preview-subtitle {
  margin-top: 4px;
  font-size: 12px;
  font-weight: 700;
  color: #64748b;
}

.preview-list {
  display: grid;
  gap: 8px;
}

.preview-row {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) 110px 120px;
  gap: 10px;
  align-items: center;
  padding: 10px 0;
  border-top: 1px solid #eef1f5;
}

.preview-row-header {
  border-top: 0;
  padding-top: 0;
  font-size: 12px;
  font-weight: 900;
  color: #64748b;
}

.task-title {
  min-width: 0;
}

.task-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
  line-height: 1.35;
}

.task-time {
  font-size: 12px;
  color: #475569;
  font-weight: 700;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 64px;
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 900;
}

.status-on {
  background: #ecfdf3;
  color: #15803d;
  border: 1px solid #b7ebc6;
}

.status-off {
  background: #fff1f2;
  color: #b42318;
  border: 1px solid #fecdd3;
}

.date-filter {
  display: flex;
  align-items: flex-end;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.date-filter label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.date-filter input {
  border: 1px solid #d7dde7;
  border-radius: 10px;
  padding: 7px 9px;
  min-height: 32px;
  box-sizing: border-box;
}

.error-text {
  margin: 0 0 12px;
  color: #b42318;
  background: #fff1f0;
  border: 1px solid #ffccc7;
  border-radius: 10px;
  padding: 9px 12px;
  font-weight: 800;
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

.kpi-note {
  margin-top: 6px;
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.4;
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
  cursor: pointer;
}

.unit-row.header {
  border-top: 0;
  color: #666;
  font-weight: 900;
  padding-top: 0;
  cursor: default;
}

.unit-row.selected {
  background: #f7fbff;
}

.unit-title {
  font-weight: 900;
  color: #111;
}

.unit-raw {
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
}

.unit-row.header .unit-actions {
  justify-content: flex-end;
}

@media (max-width: 1100px) {
  .dashboard-split {
    grid-template-columns: 1fr;
  }

  .preview-row {
    grid-template-columns: 1fr;
  }
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

  .card-head {
    flex-direction: column;
  }

  .date-filter {
    width: 100%;
    justify-content: flex-start;
  }

  .date-filter label {
    flex: 1;
    min-width: 140px;
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
