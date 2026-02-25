<template>
  <div class="page">
    <h1>教學分析儀表板</h1>

    <!-- 篩選區 -->
    <div class="filter-section">
      <div class="filter-row">
        <div class="filter-group">
          <label>班級代碼</label>
          <input v-model="filters.classId" placeholder="例如：A班 / IM01" />
        </div>

        <div class="filter-group">
          <label>單元</label>
          <select v-model="filters.unit">
            <option value="">全部</option>
            <option value="U1">U1 - 算數運算</option>
            <option value="U2">U2</option>
            <option value="U3">U3</option>
          </select>
        </div>

        <button class="btn-primary" @click="onSearch">查詢分析</button>
      </div>
    </div>

    <!-- 統計卡片 -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">總學生數</div>
        <div class="stat-value">{{ totalStudents }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">完成率</div>
        <div class="stat-value">{{ completionRate }}%</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">平均得分</div>
        <div class="stat-value">{{ safeAvgScore }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Parsons 完成</div>
        <div class="stat-value">{{ parsonsCompleted }}</div>
      </div>
    </div>

    <!-- 詳細分析表格 -->
    <div class="analysis-section">
      <h2>學生學習進度</h2>

      <div class="table-wrapper">
        <table class="analysis-table">
          <thead>
            <tr>
              <th>學號</th>
              <th>姓名</th>
              <th>班級</th>
              <th class="center">影片觀看</th>
              <th class="center">Parsons 提交</th>
              <th class="center">正確率</th>
              <th>最後活動</th>
              <th class="center">操作</th>
            </tr>
          </thead>

          <tbody v-if="students.length">
            <tr v-for="s in students" :key="s.participant_id || s.student_id">
              <td>{{ s.student_id || "-" }}</td>
              <td>{{ s.name || "-" }}</td>
              <td>{{ s.class_id || "-" }}</td>
              <td class="center">{{ s.videos_watched ?? 0 }}</td>
              <td class="center">{{ s.parsons_submitted ?? 0 }}</td>
              <td class="center">
                <span class="badge" :class="riskClass(s.accuracy)">
                  {{ Math.round(((s.accuracy ?? 0) * 100)) }}%
                </span>
              </td>
              <td>{{ formatDate(s.last_activity) }}</td>
              <td class="center">
                <button class="btn-small" @click="viewDetail(s)">查看</button>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="!students.length" class="empty-message">
          尚無資料（請確認後端 /api/records/students 有回傳 students 與 total）
        </div>
      </div>

      <!-- 分頁 -->
      <div class="pagination">
        <button :disabled="page <= 1" @click="page--">上一頁</button>
        <span>第 {{ page }} 頁 / 共 {{ totalPages }} 頁</span>
        <button :disabled="page >= totalPages" @click="page++">下一頁</button>
      </div>
    </div>

    <!-- Modal：學生詳情 -->
    <div v-if="showDetail" class="modal" @click.self="closeDetail">
      <div class="modal-card">
        <div class="modal-header">
          <h3>
            學生詳情：{{ currentStudent && currentStudent.name ? currentStudent.name : "未命名" }}
          </h3>
          <button class="close-btn" @click="closeDetail">×</button>
        </div>

        <div class="modal-body">
          <div class="detail-group">
            <label>基本資料</label>
            <div class="detail-row">
              <div>學號：{{ currentStudent && currentStudent.student_id ? currentStudent.student_id : "-" }}</div>
              <div>班級：{{ currentStudent && currentStudent.class_id ? currentStudent.class_id : "-" }}</div>
              <div>Participant：{{ currentStudent && currentStudent.participant_id ? currentStudent.participant_id : "-" }}</div>
            </div>
          </div>

          <div class="detail-group">
            <label>近期學習紀錄</label>
            <ul class="activity-list" v-if="recentEvents.length">
              <li v-for="(e, idx) in recentEvents" :key="idx">
                <span class="event-type">{{ e.type || "event" }}</span>
                <span class="event-time">{{ formatDate(e.created_at) }}</span>
                <span class="event-meta">
                  {{ e.meta ? JSON.stringify(e.meta) : "" }}
                </span>
              </li>
            </ul>
            <div v-else style="color:#666;">（尚無事件）</div>
          </div>
        </div>

        <div class="modal-footer">
          <button class="btn-secondary" @click="closeDetail">關閉</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch, computed } from "vue";
import { api } from "../api";

const filters = reactive({
  classId: "",
  unit: ""
});

const page = ref(1);
const pageSize = ref(15);

const students = ref([]);
const totalStudents = ref(0);
const completionRate = ref(0);
const avgScore = ref(0);
const parsonsCompleted = ref(0);

const showDetail = ref(false);
const currentStudent = ref(null);
const recentEvents = ref([]);

const totalPages = computed(() => {
  const t = Math.ceil((totalStudents.value || 0) / (pageSize.value || 1));
  return Math.max(1, t);
});

const safeAvgScore = computed(() => {
  const v = Number(avgScore.value);
  if (!Number.isFinite(v)) return "0.0";
  return v.toFixed(1);
});

function formatDate(dateStr) {
  if (!dateStr) return "-";
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString("zh-TW") + " " + d.toLocaleTimeString("zh-TW");
  } catch {
    return String(dateStr);
  }
}

function riskClass(accuracy) {
  const a = Number(accuracy ?? 0);
  if (a >= 0.8) return "high";
  if (a >= 0.5) return "mid";
  return "low";
}

async function loadAnalytics() {
  try {
    // 1) 學生列表
    const studentsRes = await api.get("/records/students", {
      params: {
        class_id: filters.classId,
        page: page.value,
        page_size: pageSize.value
      }
    });

    students.value = studentsRes.data?.students || [];
    totalStudents.value = studentsRes.data?.total || 0;

    // 2) 統計（若後端有提供）
    try {
      const statsRes = await api.get("/records/analytics", {
        params: {
          class_id: filters.classId,
          unit: filters.unit
        }
      });

      completionRate.value = statsRes.data?.completion_rate || 0;
      avgScore.value = statsRes.data?.avg_score || 0;
      parsonsCompleted.value = statsRes.data?.parsons_completed || 0;
    } catch {
      // 後端沒做 analytics 就用簡易估算
      const denom = totalStudents.value || students.value.length || 1;

      completionRate.value = Math.round(
        (students.value.filter(s => (s.videos_watched ?? 0) > 0).length / denom) * 100
      );

      avgScore.value =
        students.value.reduce((sum, s) => sum + Number(s.accuracy ?? 0), 0) /
        Math.max(1, students.value.length);

      parsonsCompleted.value = students.value.filter(s => (s.parsons_submitted ?? 0) > 0).length;
    }
  } catch (error) {
    console.error("載入分析數據失敗:", error);
    students.value = [];
    totalStudents.value = 0;
  }
}

async function viewDetail(student) {
  currentStudent.value = student;
  recentEvents.value = [];
  showDetail.value = true;

  try {
    const eventsRes = await api.get("/records/learning_events", {
      params: {
        participant_id: student.participant_id,
        limit: 20
      }
    });

    recentEvents.value = eventsRes.data?.events || [];
  } catch (e) {
    console.warn("learning_events 取得失敗:", e);
    recentEvents.value = [];
  }
}

function closeDetail() {
  showDetail.value = false;
  currentStudent.value = null;
  recentEvents.value = [];
}

function onSearch() {
  page.value = 1;
  loadAnalytics();
}

// 分頁變動自動重抓
watch([page, pageSize], () => {
  // 保護：避免 page 超出 totalPages
  if (page.value > totalPages.value) page.value = totalPages.value;
  loadAnalytics();
});

onMounted(() => {
  loadAnalytics();
});
</script>

<style scoped>
.page {
  padding: 20px;
  background: #0f0f0f;
  min-height: 100vh;
}

h1 {
  color: #fff;
  margin-bottom: 24px;
  font-size: 28px;
}

h2 {
  color: #fff;
  margin-bottom: 16px;
  font-size: 20px;
}

/* 篩選區 */
.filter-section {
  background: #1e1e1e;
  padding: 16px;
  border-radius: 12px;
  margin-bottom: 24px;
  border: 1px solid #333;
}

.filter-row {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  flex-wrap: wrap;
}

.filter-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.filter-group label {
  color: #999;
  font-weight: 600;
  font-size: 14px;
}

.filter-group input,
.filter-group select {
  width: 200px;
  padding: 8px 12px;
  background: #111;
  border: 1px solid #333;
  border-radius: 8px;
  color: #fff;
  font-size: 14px;
}

.filter-group input::placeholder {
  color: #666;
}

.btn-primary {
  padding: 8px 16px;
  background: #0066cc;
  color: #fff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-weight: 600;
  transition: background 0.3s;
}

.btn-primary:hover {
  background: #0052a3;
}

/* 統計卡片 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  background: #1e1e1e;
  padding: 20px;
  border-radius: 12px;
  border: 1px solid #333;
  text-align: center;
}

.stat-label {
  color: #999;
  font-size: 14px;
  margin-bottom: 8px;
}

.stat-value {
  color: #0066cc;
  font-size: 32px;
  font-weight: 700;
}

/* 分析區 */
.analysis-section {
  background: #1e1e1e;
  padding: 16px;
  border-radius: 12px;
  border: 1px solid #333;
}

.table-wrapper {
  overflow-x: auto;
  margin-bottom: 16px;
}

.analysis-table {
  width: 100%;
  border-collapse: collapse;
  color: #fff;
  font-size: 14px;
}

.analysis-table thead {
  background: #262626;
  border-bottom: 2px solid #333;
}

.analysis-table th {
  padding: 12px;
  text-align: left;
  font-weight: 600;
  color: #ccc;
}

.analysis-table td {
  padding: 12px;
  border-bottom: 1px solid #333;
}

.analysis-table tbody tr:hover {
  background: #262626;
}

.center {
  text-align: center;
}

.badge {
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}

.badge.high {
  background: #1a5940;
  color: #4ade80;
}

.badge.mid {
  background: #5a4a1a;
  color: #fbbf24;
}

.badge.low {
  background: #5a1a1a;
  color: #f87171;
}

.btn-small {
  padding: 6px 12px;
  background: #0066cc;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
}

.btn-small:hover {
  background: #0052a3;
}

.empty-message {
  text-align: center;
  padding: 40px;
  color: #666;
}

/* 分頁 */
.pagination {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: center;
  margin-top: 16px;
}

.pagination button {
  padding: 8px 12px;
  background: #0066cc;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 600;
}

.pagination button:disabled {
  background: #444;
  cursor: not-allowed;
  opacity: 0.5;
}

.pagination span {
  color: #999;
  font-size: 14px;
}

/* Modal */
.modal {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-card {
  width: 90%;
  max-width: 600px;
  background: #1e1e1e;
  border-radius: 12px;
  border: 1px solid #333;
  max-height: 90vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.modal-header {
  padding: 16px 20px;
  border-bottom: 1px solid #333;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-header h3 {
  color: #fff;
  margin: 0;
  font-size: 18px;
}

.close-btn {
  background: none;
  border: none;
  color: #999;
  font-size: 24px;
  cursor: pointer;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  transition: background 0.2s;
}

.close-btn:hover {
  background: #262626;
  color: #fff;
}

.modal-body {
  padding: 20px;
  flex: 1;
}

.detail-group {
  margin-bottom: 20px;
}

.detail-group label {
  display: block;
  color: #0066cc;
  font-weight: 700;
  margin-bottom: 8px;
  font-size: 14px;
}

.detail-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: #ccc;
  font-size: 14px;
}

.activity-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.activity-list li {
  display: flex;
  gap: 12px;
  padding: 8px;
  background: #262626;
  border-radius: 6px;
  font-size: 13px;
  align-items: flex-start;
}

.event-type {
  background: #0066cc;
  color: #fff;
  padding: 2px 8px;
  border-radius: 4px;
  min-width: 80px;
}

.event-time {
  color: #999;
  flex: 1;
}

.event-meta {
  color: #666;
  font-size: 12px;
}

.modal-footer {
  padding: 16px 20px;
  border-top: 1px solid #333;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.btn-secondary {
  padding: 8px 16px;
  background: #333;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 600;
  transition: background 0.2s;
}

.btn-secondary:hover {
  background: #444;
}
</style>
