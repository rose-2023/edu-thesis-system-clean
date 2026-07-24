<!-- 教師端前測問卷管理頁面 -->
<template>
  <div class="questionnaire-admin">
    <div class="layout">
      <TeacherSidebar active="pretest-surveys" />

      <main class="main">
        <header class="page-header">
          <div>
            <p class="eyebrow">前測前固定問卷</p>
            <h1>學生前測問卷</h1>
            <p>資料來源：<code>student_questionnaire_responses</code>。問卷資料不納入 Parsons 作答分析。</p>
            <p>群組依 <code>users.group_type</code> 判定；值為空時顯示為測試資料。</p>
          </div>
        </header>

        <section class="card controls">
          <label>
            <span>群組篩選</span>
            <select v-model="selectedGroup" :disabled="state.loading" @change="loadSurveys(1)">
              <option value="">全部組別</option>
              <option value="control">控制組</option>
              <option value="experimental_1">B 組（AI 增強型結構化回饋）</option>
              <option value="experimental_2">C 組（結構化錯誤回饋）</option>
              <option value="test_data">測試資料</option>
            </select>
          </label>
          <button class="button secondary" type="button" :disabled="state.loading" @click="loadSurveys(page)">
            {{ state.loading ? '載入中…' : '重新整理' }}
          </button>
          <button class="button export" type="button" :disabled="state.loading || exporting" @click="exportCsv">
            {{ exporting ? '匯出中…' : '匯出 CSV' }}
          </button>
        </section>

        <p v-if="state.error" class="message error">{{ state.error }}</p>

        <section class="summary-grid" aria-label="問卷完成摘要">
          <article class="summary-card">
            <span>已提交</span>
            <strong>{{ completion.submitted }} / {{ completion.total }}</strong>
          </article>
          <article class="summary-card">
            <span>完成率</span>
            <strong>{{ completionRate }}%</strong>
          </article>
          <article class="summary-card">
            <span>目前篩選</span>
            <strong>{{ currentFilterLabel }}</strong>
          </article>
        </section>

        <section class="card table-card">
          <div class="table-heading">
            <h2>學生提交狀態</h2>
            <span>{{ total }} 位學生</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>群組</th>
                  <th>班級</th>
                  <th>學號</th>
                  <th>姓名</th>
                  <th>測驗批次</th>
                  <th>問卷狀態</th>
                  <th>問卷版本</th>
                  <th>提交時間</th>
                  <th class="center">問卷內容</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!state.loading && !students.length">
                  <td colspan="9" class="empty">資料不足：此篩選條件下尚無學生資料。</td>
                </tr>
                <tr v-for="student in students" :key="student.student_id">
                  <td>{{ groupLabel(student.group_type) }}</td>
                  <td>{{ student.class_name || '-' }}</td>
                  <td class="mono">{{ student.student_id }}</td>
                  <td>{{ student.name || '-' }}</td>
                  <td class="mono">{{ student.test_cycle_id || '未指派' }}</td>
                  <td>
                    <span class="status" :class="student.submitted ? 'submitted' : 'pending'">
                      {{ student.submitted ? '已提交' : '未提交' }}
                    </span>
                  </td>
                  <td class="mono">{{ student.form_version || '-' }}</td>
                  <td>{{ formatTime(student.submitted_at) }}</td>
                  <td>
                    <button class="button link" type="button" @click="openDetail(student)">查看問卷內容</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <footer class="pagination">
            <button class="button secondary" type="button" :disabled="page <= 1 || state.loading" @click="loadSurveys(page - 1)">上一頁</button>
            <span>第 {{ page }} 頁</span>
            <button class="button secondary" type="button" :disabled="page * pageSize >= total || state.loading" @click="loadSurveys(page + 1)">下一頁</button>
          </footer>
        </section>
      </main>
    </div>

    <div v-if="detail.open" class="dialog-backdrop" @click.self="closeDetail">
      <section class="dialog" role="dialog" aria-modal="true" aria-labelledby="detail-title">
        <header class="dialog-header">
          <div>
            <p class="eyebrow">學生問卷內容</p>
            <h2 id="detail-title">{{ detail.student?.name || '未命名學生' }}</h2>
            <p>
              學號：{{ detail.student?.student_id || '-' }}　班級：{{ detail.student?.class_name || '-' }}
              　群組：{{ groupLabel(detail.student?.group_type) }}　問卷版本：{{ detail.formVersion || '-' }}
            </p>
          </div>
          <button class="icon-button" type="button" aria-label="關閉" @click="closeDetail">×</button>
        </header>

        <p v-if="detail.loading" class="message">載入問卷內容中…</p>
        <p v-else-if="detail.error" class="message error">{{ detail.error }}</p>
        <p v-else-if="!detail.submitted" class="message">此學生尚未提交前測問卷。</p>
        <div v-else class="answer-list">
          <p class="submitted-time">提交時間：{{ formatTime(detail.submittedAt) }}</p>
          <article v-for="answer in detail.answers" :key="answer.question_id" class="answer-row">
            <h3>{{ answer.question_label }}</h3>
            <p>{{ answer.answer_label }}</p>
          </article>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import TeacherSidebar from "../components/TeacherSidebar.vue";
import { api } from "../api";

const selectedGroup = ref("");
const students = ref([]);
const page = ref(1);
const pageSize = ref(25);
const total = ref(0);
const completion = reactive({ submitted: 0, total: 0 });
const state = reactive({ loading: false, error: "" });
const exporting = ref(false);
const detail = reactive({
  open: false,
  loading: false,
  error: "",
  student: null,
  submitted: false,
  formVersion: null,
  submittedAt: null,
  answers: [],
});

const completionRate = computed(() => {
  if (!completion.total) return 0;
  return Math.round((completion.submitted / completion.total) * 100);
});

const currentFilterLabel = computed(() => (
  selectedGroup.value ? groupLabel(selectedGroup.value) : "全部組別"
));

function groupLabel(groupType) {
  if (groupType === "control") return "控制組";
  if (groupType === "experimental_1") return "B 組（AI 增強型結構化回饋）";
  if (groupType === "experimental_2") return "C 組（結構化錯誤回饋）";
  return "測試資料";
}

function formatTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-TW", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

async function loadSurveys(nextPage = 1) {
  state.loading = true;
  state.error = "";
  try {
    const { data } = await api.get("/api/records/pretest-surveys", {
      params: {
        group_type: selectedGroup.value || undefined,
        page: nextPage,
        page_size: pageSize.value,
      },
    });
    if (!data?.ok) throw new Error(data?.message || "無法載入問卷資料。");
    students.value = Array.isArray(data.students) ? data.students : [];
    page.value = Number(data.page || nextPage || 1);
    pageSize.value = Number(data.page_size || pageSize.value);
    total.value = Number(data.total || 0);
    completion.submitted = Number(data.completion?.submitted || 0);
    completion.total = Number(data.completion?.total || 0);
  } catch (error) {
    state.error = error?.response?.data?.message || error?.message || "無法載入問卷資料。";
  } finally {
    state.loading = false;
  }
}

async function openDetail(student) {
  detail.open = true;
  detail.loading = true;
  detail.error = "";
  detail.student = student;
  detail.submitted = false;
  detail.formVersion = null;
  detail.submittedAt = null;
  detail.answers = [];
  try {
    const { data } = await api.get(`/api/records/pretest-surveys/${encodeURIComponent(student.student_id)}`);
    if (!data?.ok) throw new Error(data?.message || "無法載入學生問卷。");
    detail.student = data.student || student;
    detail.submitted = !!data.submitted;
    detail.formVersion = data.form_version || null;
    detail.submittedAt = data.submitted_at || null;
    detail.answers = Array.isArray(data.answers) ? data.answers : [];
  } catch (error) {
    detail.error = error?.response?.data?.message || error?.message || "無法載入學生問卷。";
  } finally {
    detail.loading = false;
  }
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

async function exportCsv() {
  exporting.value = true;
  state.error = "";
  try {
    const { data } = await api.get("/api/records/pretest-surveys/export_csv", {
      params: {
        group_type: selectedGroup.value || undefined,
      },
      responseType: "blob",
    });
    const exportSuffix = `${selectedGroup.value || "all"}`
      .trim()
      .replace(/[\\/:*?\"<>|\s]+/g, "_");
    downloadBlob(data, `pretest_questionnaires_${exportSuffix || "all"}.csv`);
  } catch (error) {
    state.error = "問卷 CSV 匯出失敗，請稍後再試。";
  } finally {
    exporting.value = false;
  }
}

function closeDetail() {
  detail.open = false;
}

onMounted(() => loadSurveys());
</script>

<style scoped>
.questionnaire-admin { min-height: 100dvh; background: #f3f7fb; color: #172033; font-family: "Noto Sans TC", "Segoe UI", sans-serif; }
.layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 18px; width: 100%; max-width: none; margin: 0; }
.main { min-width: 0; }
.page-header { display: flex; align-items: start; justify-content: space-between; margin: 16px 8px 22px; }
.page-header h1, .table-heading h2, .dialog h2 { margin: 0; }
.page-header p { margin: 8px 0 0; color: #667085; }
.eyebrow { margin: 0; color: #0d765b; font-size: 14px; font-weight: 800; }
.card, .summary-card, .dialog { border: 1px solid #dce6ee; border-radius: 16px; background: #fff; box-shadow: 0 8px 24px rgba(50, 76, 100, 0.06); }
.controls { display: flex; align-items: end; gap: 14px; width: fit-content; max-width: 100%; padding: 18px; }
.controls label { display: grid; gap: 7px; font-weight: 700; }
select { min-width: 220px; padding: 10px 12px; border: 1px solid #b8c5d1; border-radius: 9px; background: #fff; font: inherit; }
.button { border: 0; border-radius: 9px; padding: 10px 14px; font: inherit; font-weight: 800; cursor: pointer; }
.button.secondary { background: #e7eef4; color: #344054; }
.button.export { background: #0d765b; color: #fff; }
.button.link { padding: 7px 10px; background: #e7f5ef; color: #096548; }
.button:disabled { cursor: not-allowed; opacity: .55; }
.message { margin: 18px 0; padding: 14px; border-radius: 10px; background: #eef5fa; }
.message.error { color: #b42318; background: #fef3f2; }
.summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin: 18px 0; }
.summary-card { display: grid; gap: 8px; padding: 18px; }
.summary-card span { color: #667085; font-size: 14px; }
.summary-card strong { font-size: 24px; overflow-wrap: anywhere; }
.table-card { padding: 18px; }
.table-heading, .pagination, .dialog-header { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.table-heading { margin-bottom: 14px; }
.table-heading span { color: #667085; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; min-width: 1040px; }
th, td { padding: 13px 12px; border-bottom: 1px solid #e7edf2; text-align: left; vertical-align: middle; }
th { color: #667085; font-size: 13px; }
.mono { font-family: Consolas, "Courier New", monospace; }
.status { display: inline-block; padding: 5px 9px; border-radius: 999px; font-size: 13px; font-weight: 800; }
.status.submitted { color: #096548; background: #daf3e8; }
.status.pending { color: #9a6700; background: #fff1c2; }
.empty { padding: 40px; color: #667085; text-align: center; }
.pagination { margin-top: 16px; }
.dialog-backdrop { position: fixed; z-index: 20; inset: 0; display: grid; place-items: center; padding: 18px; background: rgba(16, 24, 40, .48); }
.dialog { width: min(720px, 100%); max-height: min(760px, 90dvh); padding: 24px; overflow: auto; }
.dialog-header { align-items: flex-start; }
.dialog-header p { margin: 7px 0 0; color: #667085; }
.icon-button { width: 36px; height: 36px; border: 0; border-radius: 50%; background: #edf2f7; color: #344054; font-size: 26px; line-height: 1; cursor: pointer; }
.submitted-time { color: #667085; }
.answer-list { display: grid; gap: 12px; margin-top: 18px; }
.answer-row { padding: 15px; border: 1px solid #e1e8ef; border-radius: 10px; }
.answer-row h3 { margin: 0; font-size: 16px; }
.answer-row p { margin: 8px 0 0; color: #0d765b; font-weight: 800; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .summary-grid { grid-template-columns: 1fr; } }
@media (max-width: 560px) { .questionnaire-admin { padding: 8px; } .controls { width: auto; align-items: stretch; flex-direction: column; } select { width: 100%; } }
</style>
