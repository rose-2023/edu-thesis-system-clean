<template>
  <div class="layout">
    <TeacherSidebar active="analyze" />

    <main class="content">
      <header class="pageHeader">
        <div>
          <h1>Parsons 學習分析</h1>
          <p>以 Parsons 作答紀錄與操作事件檢視班級、題目、概念與單一學生資料。</p>
        </div>
        <div class="headerActions">
          <input
            ref="studentCsvInput"
            class="hiddenFile"
            type="file"
            accept=".csv"
            @change="uploadStudentCsv"
          />
          <button class="btn" type="button" :disabled="csvBusy" @click="openStudentCsvPicker">
            匯入學生 CSV
          </button>
          <button class="btn" type="button" :disabled="csvBusy" @click="exportGroupLearningData">
            匯出組別學習歷程與作答紀錄
          </button>
          <button class="btn primary" type="button" :disabled="loading" @click="fetchAnalysis">
            {{ loading ? "載入中" : "重新整理" }}
          </button>
        </div>
      </header>

      <section class="panel">
        <div class="modeTabs" aria-label="分析模式">
          <button
            v-for="option in modeOptions"
            :key="option.value"
            class="modeBtn"
            :class="{ active: selectedMode === option.value }"
            type="button"
            @click="setMode(option.value)"
          >
            {{ option.label }}
          </button>
        </div>

        <div class="filters">
          <label class="field">
            <span>班級</span>
            <input v-model.trim="className" class="input" placeholder="全部班級" @keyup.enter="fetchAnalysis" />
          </label>
          <label class="field">
            <span>組別</span>
            <select v-model="groupType" class="input" @change="onGroupChange">
              <option value="">全部組別</option>
              <option value="control">控制組</option>
              <option value="experimental">實驗組</option>
              <option value="test_data">測試資料</option>
            </select>
          </label>
          <label class="field">
            <span>學生選擇</span>
            <select v-model="selectedStudentId" class="input" @change="fetchAnalysis">
              <option value="">選擇學生</option>
              <option v-for="row in studentOptions" :key="row.student_id" :value="row.student_id">
                {{ row.student_id }}{{ row.has_attempts ? " / 作答" : "" }}{{ row.has_logs ? " / 操作" : "" }}
              </option>
            </select>
          </label>
        </div>

        <p v-if="errorMsg" class="message error">{{ errorMsg }}</p>
        <p v-if="csvMessage" class="message ok">{{ csvMessage }}</p>
        <div v-if="invalidRows.length" class="invalidRows">
          <div v-for="row in invalidRows" :key="`${row.row}-${row.student_id}-${row.reason}`">
            row {{ row.row }} / {{ row.student_id || "-" }} / {{ row.reason }}
          </div>
        </div>
        <p class="note" :class="{ testModeNotice: groupType === 'test_data' }">
          {{ groupHint }}
        </p>

        <div class="viewTabs" role="tablist" aria-label="分析呈現方式">
          <button
            class="viewTab"
            :class="{ active: analysisView === 'table' }"
            type="button"
            role="tab"
            :aria-selected="analysisView === 'table'"
            @click="analysisView = 'table'"
          >
            資料表分析
          </button>
          <button
            class="viewTab"
            :class="{ active: analysisView === 'visual' }"
            type="button"
            role="tab"
            :aria-selected="analysisView === 'visual'"
            @click="analysisView = 'visual'"
          >
            視覺化分析
          </button>
        </div>
      </section>

      <template v-if="analysisView === 'table'">
      <section class="kpiGrid">
        <article v-for="item in kpiItems" :key="item.key" class="kpiCard">
          <div class="kpiLabel">{{ item.label }}</div>
          <div class="kpiValue">{{ item.value }}</div>
        </article>
      </section>

      <section class="panel">
        <div class="sectionHeader">
          <h2>學生作答總覽</h2>
          <span>{{ studentRows.length }} 位學生</span>
        </div>
        <div class="tableWrap">
          <table class="dataTable">
            <thead>
              <tr>
                <th>學生編號</th>
                <th>班級</th>
                <th>組別</th>
                <th>作答題數</th>
                <th>提交數</th>
                <th>答對題數</th>
                <th>平均嘗試次數</th>
                <th>平均有效作答時間</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in studentRows"
                :key="row.student_id"
                :class="{ selected: selectedStudentId === row.student_id }"
                @click="selectStudent(row.student_id)"
              >
                <td>{{ row.student_id }}</td>
                <td>{{ row.class_name || "-" }}</td>
                <td>{{ row.group_type || "-" }}</td>
                <td>{{ row.task_count }}</td>
                <td>{{ row.total_attempts }}</td>
                <td>{{ row.correct_task_count }}</td>
                <td>{{ formatNumber(row.avg_attempts_per_task) }}</td>
                <td>{{ formatSeconds(row.avg_duration_sec) }}</td>
              </tr>
              <tr v-if="!loading && studentRows.length === 0">
                <td class="empty" colspan="8">目前沒有符合條件的作答資料</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="analysisGrid">
        <article class="panel">
          <div class="sectionHeader">
            <h2>題目錯誤分析</h2>
            <span>{{ taskRows.length }} 題</span>
          </div>
          <div class="tableWrap analytics-table-wrapper">
            <table class="dataTable analytics-table task-error-table">
              <colgroup>
                <col style="width: 12%">
                <col style="width: 30%">
                <col style="width: 12%">
                <col style="width: 9%">
                <col style="width: 9%">
                <col style="width: 8%">
                <col style="width: 20%">
              </colgroup>
              <thead>
                <tr>
                  <th>題目ID</th>
                  <th>題目</th>
                  <th>概念</th>
                  <th class="center">提交數</th>
                  <th class="center">錯誤數</th>
                  <th class="center">答對率</th>
                  <th>常見錯誤</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in taskRows" :key="row.task_id">
                  <td class="mono task-id-cell">{{ row.task_id }}</td>
                  <td>
                    <div class="cell-task-title" :title="row.task_title || '-'">
                      {{ row.task_title || "-" }}
                    </div>
                  </td>
                  <td><span class="concept-badge">{{ row.target_concept || "unknown" }}</span></td>
                  <td class="center compact">{{ row.total_attempts }}</td>
                  <td class="center compact">{{ row.wrong_attempts }}</td>
                  <td class="center compact">{{ formatPercent(row.correct_rate) }}</td>
                  <td>
                    <span
                      v-for="item in row.common_error_types || []"
                      :key="`${row.task_id}-${item.type}`"
                      class="error-badge"
                    >
                      {{ formatCommonErrorBadge(item) }}
                    </span>
                    <span v-if="!row.common_error_types || row.common_error_types.length === 0">-</span>
                  </td>
                </tr>
                <tr v-if="!loading && taskRows.length === 0">
                  <td class="empty" colspan="7">目前沒有題目錯誤資料</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>

        <article class="panel">
          <div class="sectionHeader">
            <h2>概念錯誤分析</h2>
            <span>{{ conceptRows.length }} 個概念</span>
          </div>
          <div class="tableWrap">
            <table class="dataTable">
              <thead>
                <tr>
                  <th>概念</th>
                  <th>提交數</th>
                  <th>錯誤數</th>
                  <th>答對率</th>
                  <th>重複錯誤數</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in conceptRows" :key="row.target_concept">
                  <td>{{ row.target_concept || "unknown" }}</td>
                  <td>{{ row.total_attempts }}</td>
                  <td>{{ row.wrong_attempts }}</td>
                  <td>{{ formatPercent(row.correct_rate) }}</td>
                  <td>{{ row.repeated_error_count }}</td>
                </tr>
                <tr v-if="!loading && conceptRows.length === 0">
                  <td class="empty" colspan="5">目前沒有概念錯誤資料</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>
      </section>

      <section class="panel">
        <div class="sectionHeader">
          <h2>單一學生學習資料</h2>
          <span>{{ selectedStudentId || "尚未選擇學生" }}</span>
        </div>

        <div class="studentDetailGrid">
          <article class="detailBlock">
            <h3>作答紀錄</h3>
            <div class="tableWrap analytics-table-wrapper">
              <table class="dataTable analytics-table student-attempt-table">
                <colgroup>
                  <col style="width: 11%">
                  <col style="width: 10%">
                  <col style="width: 26%">
                  <col style="width: 10%">
                  <col style="width: 7%">
                  <col style="width: 7%">
                  <col style="width: 6%">
                  <col style="width: 11%">
                  <col style="width: 7%">
                  <col style="width: 5%">
                </colgroup>
                <thead>
                  <tr>
                    <th>提交時間</th>
                    <th>題目ID</th>
                    <th>題目</th>
                    <th>概念</th>
                    <th class="center">次數</th>
                    <th class="center">是否答對</th>
                    <th class="center">分數</th>
                    <th>錯誤類型</th>
                    <th>錯誤位置</th>
                    <th class="center">作答時間</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, index) in studentAttempts" :key="`${row.task_id}-${row.attempt_no}-${index}`">
                    <td class="compact">{{ formatTaipeiDateTime(row.submitted_at) }}</td>
                    <td class="mono">{{ row.task_id || "-" }}</td>
                    <td>
                      <div class="cell-task-title" :title="row.task_title || '-'">
                        {{ row.task_title || "-" }}
                      </div>
                    </td>
                    <td><span class="concept-badge">{{ row.target_concept || "unknown" }}</span></td>
                    <td class="center compact">{{ row.attempt_no ?? "-" }}</td>
                    <td class="center compact">{{ formatCorrectness(row.is_correct) }}</td>
                    <td class="center compact">{{ formatNumber(row.score) }}</td>
                    <td>
                      <span
                        v-for="errorType in row.error_types || []"
                        :key="`${row.task_id}-${row.attempt_no}-${errorType}`"
                        class="error-badge"
                      >
                        {{ errorType }}
                      </span>
                      <span v-if="!row.error_types || row.error_types.length === 0">-</span>
                    </td>
                    <td class="slot-list">{{ formatArray(row.wrong_slots) }}</td>
                    <td class="center compact">{{ formatSeconds(row.duration_sec) }}</td>
                  </tr>
                  <tr v-if="!loading && selectedStudentId && studentAttempts.length === 0">
                    <td class="empty" colspan="10">此學生目前沒有符合模式的作答紀錄</td>
                  </tr>
                  <tr v-if="!selectedStudentId">
                    <td class="empty" colspan="10">請先選擇學生</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </article>

          <article class="detailBlock">
            <h3>操作歷程</h3>
            <div class="tableWrap analytics-table-wrapper">
              <table class="dataTable analytics-table logTable student-log-table">
                <colgroup>
                  <col style="width: 13%">
                  <col style="width: 11%">
                  <col style="width: 8%">
                  <col style="width: 13%">
                  <col style="width: 13%">
                  <col style="width: 7%">
                  <col style="width: 11%">
                  <col style="width: 24%">
                </colgroup>
                <thead>
                  <tr>
                    <th>事件時間</th>
                    <th>事件類型</th>
                    <th>頁面</th>
                    <th>題目ID</th>
                    <th>作答紀錄ID</th>
                    <th class="center">次數</th>
                    <th>概念</th>
                    <th>事件摘要</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, index) in studentLogs" :key="`${row.event_at}-${index}`">
                    <td class="compact">{{ formatTaipeiDateTime(row.event_at) }}</td>
                    <td>
                      <span class="event-badge" :title="row.event_type || ''">
                        {{ formatEventType(row.event_type) }}
                      </span>
                    </td>
                    <td class="compact">{{ row.page || "-" }}</td>
                    <td class="mono">{{ row.task_id || "-" }}</td>
                    <td class="mono">{{ row.attempt_id || "-" }}</td>
                    <td class="center compact">{{ row.attempt_no ?? "-" }}</td>
                    <td><span class="concept-badge">{{ row.target_concept || "-" }}</span></td>
                    <td>
                      <div class="metadata-summary">{{ formatMetadataSummary(row) }}</div>
                      <details v-if="hasMetadata(row.metadata)" class="metadata-details">
                        <summary>查看詳細</summary>
                        <pre>{{ formatMetadata(row.metadata) }}</pre>
                      </details>
                    </td>
                  </tr>
                  <tr v-if="!loading && selectedStudentId && studentLogs.length === 0">
                    <td class="empty" colspan="8">此學生目前沒有符合模式的操作歷程</td>
                  </tr>
                  <tr v-if="!selectedStudentId">
                    <td class="empty" colspan="8">請先選擇學生</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </article>
        </div>
      </section>
      </template>

      <template v-else>
        <div v-if="groupType === 'test_data'" class="visualTestNotice" role="status">
          目前為測試資料模式，圖表僅供系統測試，不納入正式研究分析。
        </div>

        <section class="visualizationGrid">
          <article class="panel chartPanel">
            <div class="sectionHeader">
              <h2>概念錯誤分布</h2>
              <span>{{ conceptChartItems.length }} 個概念</span>
            </div>
            <AnalyticsBarChart
              :items="conceptChartItems"
              color="#c2413b"
              aria-label="各概念錯誤次數長條圖"
            />
          </article>

          <article class="panel chartPanel">
            <div class="sectionHeader">
              <h2>題目答對率</h2>
              <span>{{ taskCorrectRateChartItems.length }} 題</span>
            </div>
            <AnalyticsBarChart
              :items="taskCorrectRateChartItems"
              :max-value="100"
              value-suffix="%"
              color="#2563a6"
              aria-label="各題目答對率長條圖"
            />
          </article>

          <article class="panel chartPanel">
            <div class="sectionHeader">
              <h2>錯誤類型分布</h2>
              <span>{{ errorTypeChartItems.length }} 種錯誤</span>
            </div>
            <AnalyticsBarChart
              :items="errorTypeChartItems"
              color="#b45309"
              aria-label="錯誤類型出現次數長條圖"
            />
          </article>

          <article class="panel chartPanel">
            <div class="sectionHeader">
              <h2>學生平均嘗試次數</h2>
              <span>{{ studentAttemptChartItems.length }} 位學生</span>
            </div>
            <AnalyticsBarChart
              :items="studentAttemptChartItems"
              color="#146c64"
              aria-label="各學生平均每題嘗試次數長條圖"
            />
          </article>
        </section>

        <section v-if="selectedStudentId" class="panel timelinePanel">
          <div class="sectionHeader">
            <h2>單一學生操作歷程</h2>
            <span>{{ selectedStudentId }}</span>
          </div>

          <ol v-if="timelineEvents.length" class="timelineList">
            <li
              v-for="(event, index) in timelineEvents"
              :key="`${event.event_at}-${event.event_type}-${index}`"
              class="timelineItem"
            >
              <div class="timelineMarker" aria-hidden="true"></div>
              <div class="timelineContent">
                <div class="timelineHeader">
                  <span class="event-badge" :title="event.event_type || ''">
                    {{ formatEventType(event.event_type) }}
                  </span>
                  <time>{{ formatTaipeiDateTime(event.event_at) }}</time>
                </div>
                <div class="timelineContext">
                  <span v-if="event.task_id">題目ID：{{ event.task_id }}</span>
                  <span v-if="event.attempt_no != null">第 {{ event.attempt_no }} 次提交</span>
                </div>
                <div v-if="hasMetadata(event.metadata)" class="timelineSummary">
                  {{ formatMetadataSummary(event) }}
                </div>
              </div>
            </li>
          </ol>
          <div v-else class="visualizationEmpty">目前沒有符合條件的資料可視覺化。</div>
        </section>
      </template>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import AnalyticsBarChart from "../components/AnalyticsBarChart.vue";
import TeacherSidebar from "../components/TeacherSidebar.vue";
import { formatTaipeiDateTime } from "../utils/dateTime.js";

const API_BASE = (import.meta?.env?.VITE_API_BASE || "http://127.0.0.1:5000").replace(/\/$/, "");

const modeOptions = [
  { value: "practice", label: "平時練習", activityType: "practice", testRole: null },
  { value: "pretest", label: "前測", activityType: "test", testRole: "pretest" },
  { value: "posttest", label: "後測", activityType: "test", testRole: "posttest" },
];

const selectedMode = ref("practice");
const analysisView = ref("table");
const className = ref("");
const groupType = ref("");
const selectedStudentId = ref("");
const loading = ref(false);
const csvBusy = ref(false);
const errorMsg = ref("");
const csvMessage = ref("");
const invalidRows = ref([]);
const studentCsvInput = ref(null);
const analysis = ref(emptyAnalysis());
const studentOptions = ref([]);

const selectedModeConfig = computed(() => (
  modeOptions.find((option) => option.value === selectedMode.value) || modeOptions[0]
));
const kpis = computed(() => analysis.value.kpis || {});
const studentRows = computed(() => analysis.value.student_overview || []);
const taskRows = computed(() => analysis.value.task_error_analysis || []);
const conceptRows = computed(() => analysis.value.concept_error_analysis || []);
const studentAttempts = computed(() => analysis.value.student_attempts || []);
const studentLogs = computed(() => analysis.value.student_logs || []);
const conceptChartItems = computed(() => (
  [...conceptRows.value]
    .sort((a, b) => Number(b.wrong_attempts || 0) - Number(a.wrong_attempts || 0))
    .map((row, index) => {
      const concept = row.target_concept || "unknown";
      const wrongAttempts = Number(row.wrong_attempts) || 0;
      return {
        key: `${concept}-${index}`,
        label: concept,
        value: wrongAttempts,
        displayValue: String(wrongAttempts),
        tooltip: [
          `概念：${concept}`,
          `錯誤次數：${wrongAttempts}`,
          `提交數：${Number(row.total_attempts) || 0}`,
          `答對率：${formatPercent(row.correct_rate)}`,
        ].join(" | "),
      };
    })
));
const taskCorrectRateChartItems = computed(() => (
  [...taskRows.value]
    .sort((a, b) => Number(a.correct_rate || 0) - Number(b.correct_rate || 0))
    .map((row, index) => {
      const taskId = String(row.task_id || "-");
      const taskTitle = String(row.task_title || taskId);
      const ratePercent = Math.max(0, Math.min(100, (Number(row.correct_rate) || 0) * 100));
      return {
        key: `${taskId}-${index}`,
        label: shortChartLabel(taskTitle, taskId),
        value: ratePercent,
        displayValue: `${Math.round(ratePercent * 10) / 10}%`,
        tooltip: [
          `題目：${taskTitle}`,
          `題目ID：${taskId}`,
          `提交數：${Number(row.total_attempts) || 0}`,
          `答對率：${Math.round(ratePercent * 10) / 10}%`,
        ].join(" | "),
      };
    })
));
const errorTypeChartItems = computed(() => {
  const counts = new Map();
  for (const task of taskRows.value) {
    for (const item of task.common_error_types || []) {
      const type = String(item?.type || "").trim();
      if (!type) continue;
      counts.set(type, (counts.get(type) || 0) + (Number(item.count) || 0));
    }
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([type, count]) => ({
      key: type,
      label: type,
      value: count,
      displayValue: String(count),
      tooltip: `錯誤類型：${type} | 出現次數：${count}`,
    }));
});
const studentAttemptChartItems = computed(() => (
  [...studentRows.value]
    .sort((a, b) => Number(b.avg_attempts_per_task || 0) - Number(a.avg_attempts_per_task || 0))
    .map((row) => {
      const studentId = String(row.student_id || "-");
      const average = Number(row.avg_attempts_per_task) || 0;
      return {
        key: studentId,
        label: studentId,
        value: average,
        displayValue: formatNumber(average),
        tooltip: [
          `學生：${studentId}`,
          `作答題數：${Number(row.task_count) || 0}`,
          `總提交次數：${Number(row.total_attempts) || 0}`,
          `平均每題提交：${formatNumber(average)}`,
        ].join(" | "),
      };
    })
));
const timelineEvents = computed(() => {
  const allowed = new Set([
    "task_open",
    "task_start",
    "answer_submit",
    "review_open",
    "review_close",
    "return_to_task",
  ]);
  return studentLogs.value
    .filter((event) => allowed.has(event.event_type))
    .slice()
    .sort((a, b) => new Date(a.event_at || 0) - new Date(b.event_at || 0));
});
const groupHint = computed(() => {
  if (groupType.value === "control") return "目前顯示控制組正式資料，已排除測試資料。";
  if (groupType.value === "experimental") return "目前顯示實驗組正式資料，已排除測試資料。";
  if (groupType.value === "test_data") {
    return "目前為測試資料模式，僅供系統測試，不納入正式研究分析。";
  }
  return "目前顯示正式資料中的控制組與實驗組，不包含測試資料。";
});
const kpiItems = computed(() => [
  { key: "active_students", label: "參與學生數", value: kpis.value.active_students ?? 0 },
  { key: "total_attempts", label: "總提交次數", value: kpis.value.total_attempts ?? 0 },
  {
    key: "first_try_correct_rate",
    label: "首次答對率",
    value: formatPercent(kpis.value.first_try_correct_rate),
  },
  {
    key: "final_correct_rate",
    label: "最終答對率",
    value: formatPercent(kpis.value.final_correct_rate),
  },
  {
    key: "avg_attempts_per_task",
    label: "平均嘗試次數",
    value: formatNumber(kpis.value.avg_attempts_per_task),
  },
  {
    key: "avg_duration_sec",
    label: "平均有效作答時間",
    value: formatSeconds(kpis.value.avg_duration_sec),
  },
]);

function emptyAnalysis() {
  return {
    kpis: {
      active_students: 0,
      total_attempts: 0,
      first_try_correct_rate: 0,
      final_correct_rate: 0,
      avg_attempts_per_task: 0,
      avg_duration_sec: null,
    },
    student_overview: [],
    task_error_analysis: [],
    concept_error_analysis: [],
    student_attempts: [],
    student_logs: [],
  };
}

function shortChartLabel(value, fallback = "-") {
  const text = String(value || fallback).trim() || fallback;
  return text.length > 14 ? `${text.slice(0, 13)}…` : text;
}

function formatPercent(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0%";
  return `${Math.round(n * 1000) / 10}%`;
}

function formatNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return n.toFixed(2).replace(/\.00$/, "");
}

function formatSeconds(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `${Math.round(n * 10) / 10}s`;
}

function formatErrorTypes(value) {
  if (!Array.isArray(value) || value.length === 0) return "-";
  return value.map((item) => `${item.type}(${item.count})`).join(", ");
}

function formatCommonErrorBadge(item) {
  if (!item || typeof item !== "object") return "-";
  return item.count ? `${item.type} (${item.count})` : item.type;
}

function formatArray(value) {
  return Array.isArray(value) && value.length ? value.join(", ") : "-";
}

const eventTypeLabels = {
  session_start: "工作階段開始",
  session_end: "工作階段結束",
  page_view: "進入頁面",
  task_open: "開啟題目",
  task_start: "開始作答",
  answer_submit: "提交答案",
  review_open: "開啟提示",
  review_close: "關閉提示",
  return_to_task: "返回題目",
  idle_detected: "偵測閒置",
  heartbeat: "活躍確認",
};

const metadataValueLabels = {
  ai_hint: "AI 提示",
  click_ai_hint: "點擊 AI 提示",
  click_blank_area: "點擊空白處",
  click_ai_hint_again: "再次點擊 AI 提示",
  click_close_button: "點擊關閉按鈕",
};

function formatCorrectness(value) {
  if (value === true) return "是";
  if (value === false) return "否";
  return "-";
}

function formatEventType(value) {
  return eventTypeLabels[value] || value || "-";
}

function formatMetadataValue(value) {
  return metadataValueLabels[value] || value;
}

function hasMetadata(value) {
  return Boolean(value && typeof value === "object" && Object.keys(value).length);
}

function formatMetadataSummary(row) {
  const metadata = row?.metadata;
  if (!hasMetadata(metadata)) return "-";

  const parts = [];
  if (row.event_type === "answer_submit") {
    if (typeof metadata.is_correct === "boolean") {
      parts.push(`是否答對：${formatCorrectness(metadata.is_correct)}`);
    }
    if (metadata.score !== null && metadata.score !== undefined) {
      parts.push(`分數：${formatNumber(metadata.score)}`);
    }
    if (Array.isArray(metadata.error_types)) {
      parts.push(`錯誤類型：${metadata.error_types.length ? metadata.error_types.join("、") : "無"}`);
    }
  } else if (row.event_type === "review_open") {
    if (metadata.hint_no !== null && metadata.hint_no !== undefined) {
      parts.push(`第 ${metadata.hint_no} 次提示`);
    }
    if (metadata.review_type) {
      parts.push(`提示類型：${formatMetadataValue(metadata.review_type)}`);
    }
    if (metadata.hint_limit_reached === true) parts.push("已達提示上限");
  } else if (row.event_type === "review_close") {
    if (metadata.close_method) {
      parts.push(`關閉方式：${formatMetadataValue(metadata.close_method)}`);
    }
  } else if (row.event_type === "return_to_task") {
    const returnMethod = metadata.return_source
      ?? metadata.return_method
      ?? metadata.source
      ?? metadata.from_page
      ?? metadata.from;
    if (returnMethod) parts.push(`返回來源／方式：${formatMetadataValue(returnMethod)}`);
  }

  return parts.length ? parts.join("；") : "有事件詳細資料";
}

function formatMetadata(value) {
  if (!value || typeof value !== "object") return "-";
  return JSON.stringify(value, null, 2);
}

function selectedGroupFilter() {
  if (groupType.value === "control") return "control";
  if (groupType.value === "experimental") return "experimental";
  if (groupType.value === "test_data") return "test";
  return "all";
}

function buildStudentOptionsQuery() {
  const params = new URLSearchParams();
  if (className.value) params.set("class_name", className.value);
  params.set("group_filter", selectedGroupFilter());
  return params;
}

function buildFilterQuery() {
  const params = new URLSearchParams();
  const config = selectedModeConfig.value;
  params.set("activity_type", config.activityType);
  if (config.testRole) params.set("test_role", config.testRole);
  if (className.value) params.set("class_name", className.value);
  if (groupType.value) params.set("group_type", groupType.value);
  if (selectedStudentId.value) params.set("student_id", selectedStudentId.value);
  params.set("exclude_test_data", groupType.value === "test_data" ? "false" : "true");
  return params;
}

function buildGroupExportQuery() {
  const params = buildFilterQuery();
  params.delete("student_id");
  return params;
}

function buildQuery() {
  const params = buildFilterQuery();
  params.set("logs_limit", "120");
  return params;
}

function todayStamp() {
  const now = new Date();
  const year = String(now.getFullYear());
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}${month}${day}`;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function openStudentCsvPicker() {
  errorMsg.value = "";
  csvMessage.value = "";
  invalidRows.value = [];
  studentCsvInput.value?.click();
}

async function uploadStudentCsv(event) {
  const file = event?.target?.files?.[0];
  if (!file) return;
  try {
    csvBusy.value = true;
    errorMsg.value = "";
    csvMessage.value = "";
    invalidRows.value = [];

    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API_BASE}/api/teacher/import/users-csv`, {
      method: "POST",
      body: form,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data?.ok === false) {
      throw new Error(data?.message || `users csv import failed: ${response.status}`);
    }
    const invalidCount = Array.isArray(data.invalid_rows) ? data.invalid_rows.length : 0;
    csvMessage.value =
      `學生 CSV 匯入完成：新增 ${data.inserted_count || 0} 筆，更新 ${data.updated_count || 0} 筆，` +
      `跳過 ${data.skipped_count || 0} 筆，錯誤 ${invalidCount} 筆`;
    invalidRows.value = (data.invalid_rows || []).slice(0, 5);
    await fetchAnalysis();
  } catch (error) {
    errorMsg.value = error?.message || String(error);
  } finally {
    csvBusy.value = false;
    if (event?.target) event.target.value = "";
  }
}

async function exportGroupLearningData() {
  try {
    csvBusy.value = true;
    errorMsg.value = "";
    csvMessage.value = "";
    invalidRows.value = [];
    const response = await fetch(
      `${API_BASE}/api/teacher/export/group-learning-data.zip?${buildGroupExportQuery().toString()}`,
    );
    if (!response.ok) throw new Error(`group learning data export failed: ${response.status}`);
    const blob = await response.blob();
    downloadBlob(blob, `parsons_group_learning_data_${todayStamp()}.zip`);
    csvMessage.value = "已匯出目前班級與組別下全部學生的作答紀錄及學習歷程。";
  } catch (error) {
    errorMsg.value = error?.message || String(error);
  } finally {
    csvBusy.value = false;
  }
}

async function fetchStudentOptions() {
  const response = await fetch(`${API_BASE}/api/teacher/analytics/student-options?${buildStudentOptionsQuery().toString()}`);
  const data = await response.json().catch(() => []);
  if (!response.ok) throw new Error(data?.message || `student options failed: ${response.status}`);
  studentOptions.value = Array.isArray(data) ? data : [];
  if (
    selectedStudentId.value &&
    !studentOptions.value.some((row) => row.student_id === selectedStudentId.value)
  ) {
    selectedStudentId.value = "";
  }
}

async function fetchAnalysis() {
  try {
    loading.value = true;
    errorMsg.value = "";
    const [analysisResponse] = await Promise.all([
      fetch(`${API_BASE}/api/teacher/analysis/parsons?${buildQuery().toString()}`),
      fetchStudentOptions(),
    ]);
    const data = await analysisResponse.json().catch(() => ({}));
    if (!analysisResponse.ok || data?.ok === false) {
      throw new Error(data?.message || `analysis failed: ${analysisResponse.status}`);
    }
    analysis.value = {
      ...emptyAnalysis(),
      ...data,
    };
  } catch (error) {
    errorMsg.value = error?.message || String(error);
    analysis.value = emptyAnalysis();
    studentOptions.value = [];
  } finally {
    loading.value = false;
  }
}

function setMode(mode) {
  selectedMode.value = mode;
  selectedStudentId.value = "";
  fetchAnalysis();
}

function onGroupChange() {
  selectedStudentId.value = "";
  fetchAnalysis();
}

function selectStudent(studentId) {
  if (!studentId) return;
  selectedStudentId.value = studentId;
  fetchAnalysis();
}

onMounted(() => {
  fetchAnalysis();
});
</script>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 100vh;
  background: #f4f6f8;
}

.content {
  padding: 22px;
  min-width: 0;
}

.pageHeader {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.pageHeader h1 {
  margin: 0;
  color: #172033;
  font-size: 28px;
  font-weight: 900;
}

.pageHeader p {
  margin: 6px 0 0;
  color: #657085;
  font-size: 14px;
}

.headerActions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.hiddenFile {
  display: none;
}

.panel,
.kpiCard {
  background: #fff;
  border: 1px solid #d8dee9;
  border-radius: 8px;
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.04);
}

.panel {
  padding: 16px;
  margin-bottom: 14px;
}

.modeTabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.modeBtn,
.btn {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
  color: #1f2937;
  font-weight: 800;
  cursor: pointer;
}

.modeBtn {
  padding: 9px 14px;
}

.modeBtn.active,
.btn.primary {
  background: #146c64;
  border-color: #146c64;
  color: #fff;
}

.btn {
  padding: 10px 14px;
}

.btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.filters {
  display: grid;
  grid-template-columns: repeat(3, minmax(180px, 1fr));
  gap: 12px;
}

.field {
  display: grid;
  gap: 6px;
  color: #334155;
  font-size: 13px;
  font-weight: 800;
}

.input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 10px 11px;
  color: #172033;
  background: #fff;
  outline: none;
}

.message {
  margin: 12px 0 0;
  padding: 10px 12px;
  border-radius: 8px;
  font-weight: 800;
}

.message.error {
  color: #991b1b;
  background: #fee2e2;
  border: 1px solid #fecaca;
}

.message.ok {
  color: #166534;
  background: #dcfce7;
  border: 1px solid #bbf7d0;
}

.invalidRows {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  background: #fff7ed;
  color: #9a3412;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.note {
  margin: 12px 0 0;
  color: #657085;
  font-size: 13px;
}

.note.testModeNotice {
  padding: 11px 13px;
  border: 1px solid #fdba74;
  border-radius: 6px;
  color: #9a3412;
  background: #fff7ed;
  font-size: 14px;
  font-weight: 800;
}

.viewTabs {
  display: inline-flex;
  margin-top: 14px;
  padding: 3px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f1f5f9;
}

.viewTab {
  min-width: 112px;
  border: 0;
  border-radius: 6px;
  padding: 8px 13px;
  color: #475569;
  background: transparent;
  cursor: pointer;
  font-weight: 800;
}

.viewTab.active {
  color: #fff;
  background: #146c64;
}

.visualTestNotice {
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid #fdba74;
  border-radius: 6px;
  color: #9a3412;
  background: #fff7ed;
  font-size: 14px;
  font-weight: 800;
}

.kpiGrid {
  display: grid;
  grid-template-columns: repeat(6, minmax(130px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.kpiCard {
  padding: 14px;
}

.kpiLabel {
  color: #64748b;
  font-size: 12px;
  font-weight: 900;
}

.kpiValue {
  margin-top: 8px;
  color: #111827;
  font-size: 24px;
  font-weight: 900;
}

.analysisGrid,
.studentDetailGrid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 14px;
}

.analysisGrid {
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.8fr);
}

.visualizationGrid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

.visualizationGrid .panel {
  min-width: 0;
  margin-bottom: 0;
}

.chartPanel {
  min-height: 320px;
}

.timelinePanel {
  min-width: 0;
}

.timelineList {
  position: relative;
  display: grid;
  margin: 0;
  padding: 4px 0;
  gap: 0;
  list-style: none;
}

.timelineItem {
  position: relative;
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 10px;
  min-width: 0;
  padding-bottom: 18px;
}

.timelineItem:not(:last-child)::before {
  position: absolute;
  top: 15px;
  bottom: 0;
  left: 6px;
  width: 2px;
  background: #cbd5e1;
  content: "";
}

.timelineMarker {
  position: relative;
  z-index: 1;
  width: 12px;
  height: 12px;
  margin-top: 5px;
  border: 3px solid #d1fae5;
  border-radius: 50%;
  box-sizing: border-box;
  background: #146c64;
}

.timelineContent {
  min-width: 0;
  padding-bottom: 2px;
}

.timelineHeader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.timelineHeader time {
  color: #64748b;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.timelineContext {
  display: flex;
  margin-top: 4px;
  gap: 6px 14px;
  flex-wrap: wrap;
  color: #475569;
  font-size: 12px;
}

.timelineSummary {
  margin-top: 5px;
  color: #334155;
  font-size: 13px;
  line-height: 1.5;
}

.visualizationEmpty {
  display: grid;
  min-height: 160px;
  place-items: center;
  color: #64748b;
  font-size: 14px;
}

.detailBlock {
  min-width: 0;
}

.detailBlock h3 {
  margin: 4px 0 10px;
  color: #172033;
  font-size: 15px;
  font-weight: 900;
}

.sectionHeader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.sectionHeader h2 {
  margin: 0;
  color: #172033;
  font-size: 17px;
  font-weight: 900;
}

.sectionHeader span {
  color: #64748b;
  font-size: 13px;
  font-weight: 800;
}

.tableWrap {
  overflow-x: auto;
}

.analytics-table-wrapper {
  width: 100%;
  overflow-x: auto;
}

.dataTable {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.analytics-table {
  width: 100%;
  min-width: 980px;
  table-layout: fixed;
  border-collapse: collapse;
}

.student-attempt-table,
.student-log-table {
  min-width: 1280px;
}

.dataTable th,
.dataTable td {
  border-bottom: 1px solid #e5e7eb;
  padding: 9px 10px;
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}

.analytics-table th,
.analytics-table td {
  padding: 10px 12px;
  vertical-align: top;
  text-align: left;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
  line-height: 1.5;
  font-size: 14px;
}

.dataTable th {
  color: #475569;
  background: #f8fafc;
  font-size: 12px;
  font-weight: 900;
}

.analytics-table th {
  font-weight: 600;
}

.analytics-table td.compact,
.analytics-table th.compact {
  white-space: nowrap;
}

.analytics-table td.center,
.analytics-table th.center {
  text-align: center;
}

.dataTable tbody tr {
  cursor: default;
}

.dataTable tbody tr.selected,
.dataTable tbody tr:hover {
  background: #ecfdf5;
}

.titleCell {
  max-width: 280px;
  white-space: normal;
}

.cell-task-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.5;
  max-height: 3em;
}

.error-badge,
.concept-badge,
.event-badge {
  display: inline-block;
  margin: 2px 4px 2px 0;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 12px;
  background: #eef2f7;
  white-space: nowrap;
}

.concept-badge {
  background: #ecfdf5;
  color: #166534;
}

.event-badge {
  background: #eff6ff;
  color: #1d4ed8;
}

.slot-list {
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.metadata-summary {
  color: #334155;
  line-height: 1.5;
}

.metadata-details {
  margin-top: 6px;
}

.metadata-details summary {
  width: fit-content;
  color: #166534;
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
}

.metadata-details[open] summary {
  margin-bottom: 6px;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  white-space: normal;
  word-break: break-all;
}

.task-id-cell {
  white-space: normal;
  word-break: break-all;
  overflow-wrap: anywhere;
}

.empty {
  color: #64748b;
  text-align: center;
}

.logTable pre {
  margin: 0;
  max-width: 360px;
  max-height: 160px;
  overflow: auto;
  color: #1f2937;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  white-space: pre-wrap;
}

@media (max-width: 1280px) {
  .kpiGrid {
    grid-template-columns: repeat(3, minmax(160px, 1fr));
  }

  .analysisGrid {
    grid-template-columns: 1fr;
  }

  .visualizationGrid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .content {
    padding: 14px;
  }

  .pageHeader,
  .sectionHeader {
    align-items: stretch;
    flex-direction: column;
  }

  .headerActions {
    justify-content: flex-start;
  }

  .filters,
  .kpiGrid {
    grid-template-columns: 1fr;
  }
}
</style>
