<template>
  <div class="layout">
    <!-- ===== 左側 Sidebar ===== -->
    <aside class="sidebar">
      <div class="profile">
        <div class="avatar">👩‍🏫</div>
        <div class="hello">
          <div class="hi">您好，管理員</div>
          <div class="sub">分析儀表板</div>
        </div>
      </div>

      <nav class="menu">
        <a class="item" href="/admin/dashboard">總覽</a>
        <a class="item" href="/admin/videos">影片管理</a>
        <a class="item" href="/admin/t5">AI管理生成紀錄檢視</a>
        <a class="item active" href="/admin/analyze">分析</a>
      </nav>
    </aside>

    <!-- ===== 右側內容 ===== -->
    <main class="content">
      <div class="pageTitleRow">
        <h1 class="pageTitle">分析儀表板</h1>
        <button class="btn" @click="refreshAll" :disabled="loading">重新整理</button>
      </div>

      <!-- ===== 測驗資料 ===== -->
      <section class="panel">
        <div class="panelTitle">測驗資料（parsons_test_attempts）</div>

        <div class="controls">
          <div class="field">
            <div class="label">test_cycle_id</div>
            <input class="input" v-model="testCycleId" placeholder="default" />
          </div>

          <div class="field">
            <div class="label">班級（class_name）</div>
            <input class="input" v-model="className" placeholder="例如：資工系 A班" />
          </div>

          <div class="field">
            <div class="label">顯示</div>
            <select class="input" v-model="viewMode">
              <option value="attempts">作答明細</option>
              <option value="summary">每位學生彙總</option>
            </select>
          </div>

          <div class="actions">
            <button class="btn primary" @click="downloadAttemptsCsv">下載作答 CSV</button>
          </div>
        </div>

        <div class="hint" v-if="errorMsg">{{ errorMsg }}</div>
      </section>

      <!-- ===== 學生帳號匯入/匯出 ===== -->
      <section class="panel">
        <div class="panelTitle">學生帳號（thesis_system.users）</div>

        <div class="controls">
          <div class="field">
            <div class="label">匯入 CSV</div>
            <input class="input" type="file" accept=".csv" @change="onPickStudentCsv" />
            <div class="subhint">欄位：student_id,name,class_name,password(optional)</div>
          </div>

          <div class="field">
            <div class="label">預設密碼（CSV 沒有 password 時）</div>
            <input class="input" v-model="defaultPassword" />
          </div>

          <div class="actions">
            <button class="btn" @click="uploadStudentsCsv" :disabled="!studentCsvFile || loading">上傳匯入</button>
            <button class="btn primary" @click="downloadStudentsCsv">下載學生 CSV</button>
          </div>
        </div>

        <div class="hint ok" v-if="importMsg">{{ importMsg }}</div>
      </section>

      <!-- ===== 表格 ===== -->
      <section class="panel">
        <div class="panelTitle">
          <span v-if="viewMode==='attempts'">作答明細</span>
          
          <span v-else>每位學生彙總</span>
          <button @click="mode='test'">測驗分析</button>
          <button @click="mode='practice'">練習分析</button>
        </div>

        <div class="tableWrap" v-if="viewMode==='attempts'">
          <table class="table">
            <thead>
              <tr>
                <th>student_id</th>
                <th>class_name</th>
                <th>name</th>
                <th>test_role</th>
                <th>is_correct</th>
                <th>score</th>
                <th>duration_sec</th>
                <th>wrong_indices</th>
                <th>submitted_at</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, idx) in attemptRows" :key="idx">
                <td>{{ r.student_id }}</td>
                <td>{{ r.class_name }}</td>
                <td>{{ r.name }}</td>
                <td>{{ r.test_role }}</td>
                <td>{{ r.is_correct }}</td>
                <td>{{ r.score }}</td>
                <td>{{ r.duration_sec }}</td>
                <td>{{ r.wrong_indices }}</td>
                <td>{{ r.submitted_at }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="tableWrap" v-else>
          <table class="table">
            <thead>
              <tr>
                <th>student_id</th>
                <th>class_name</th>
                <th>name</th>
                <th>pre_done</th>
                <th>pre_score</th>
                <th>pre_duration</th>
                <th>post_done</th>
                <th>post_score</th>
                <th>post_duration</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, idx) in summaryRows" :key="idx">
                <td>{{ r.student_id }}</td>
                <td>{{ r.class_name }}</td>
                <td>{{ r.name }}</td>
                <td>{{ r.pre_done }}</td>
                <td>{{ r.pre_score }}</td>
                <td>{{ r.pre_duration }}</td>
                <td>{{ r.post_done }}</td>
                <td>{{ r.post_score }}</td>
                <td>{{ r.post_duration }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup>

import { ref, computed, onMounted } from "vue";

// [新增] 統一後端 API Base（避免打到 Vite 5173 回傳 index.html 造成 JSON/CSV 解析錯誤）
const API_BASE = (import.meta?.env?.VITE_API_BASE || "http://127.0.0.1:5000").replace(/\/$/, ""); // [新增]

const loading = ref(false);
const errorMsg = ref("");
const importMsg = ref("");

// [刪除] const testCycleId = ref("default");
const testCycleId = ref(""); // [新增] 允許留空（留空=不以 test_cycle_id 過濾）

// [刪除] const className = ref("");
const className = ref("資工系A"); // [新增] 預設班級（模板不改，用值做預設）

const viewMode = ref("attempts");

const defaultPassword = ref("");
const studentCsvFile = ref(null);
// 測驗資料
const mode = ref("test")  // test | practice
// 資料
const users = ref([]); // {student_id,name,class_name}
const attempts = ref([]); // raw csv rows

function parseCsv(text) {
  // 簡易 CSV 解析：因為 wrong_indices 可能是 JSON 字串（含逗號）
  const lines = text.split(/\r?\n/).filter(Boolean);
  if (!lines.length) return [];
  const headers = lines[0].split(",");
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const cols = splitCsvLine(lines[i]);
    const row = {};
    headers.forEach((h, idx) => (row[h] = cols[idx] ?? ""));
    rows.push(row);
  }
  return rows;
}

// 支援 wrong_indices 內含逗號的情況（JSON 字串會含逗號）
function splitCsvLine(line) {
  const out = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (ch === "," && !inQuotes) {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

async function fetchUsers() {
  // 取全部學生（分析頁需要）
  const qs = new URLSearchParams();
  if (className.value) qs.set("class_name", className.value);
  qs.set("page", "1");
  qs.set("page_size", "500");

  // [修改] 使用 API_BASE，避免打到 5173
  const res = await fetch(`${API_BASE}/api/records/students?${qs.toString()}`); // [新增]
  const data = await res.json();
  if (!data.ok) throw new Error(data.message || "fetch students failed");
  users.value = data.students || [];
}

async function fetchAttemptsCsv() {
  const qs = new URLSearchParams();

  // [新增] test_cycle_id 可留空：留空=不過濾
  if ((testCycleId.value || "").trim()) qs.set("test_cycle_id", (testCycleId.value || "").trim()); // [新增]

  // [新增] 班級也可在後端先過濾（前端仍會再過濾一次）
  if ((className.value || "").trim()) qs.set("class_name", (className.value || "").trim()); // [新增]

  // [刪除] const res = await fetch(`/api/parsons/test/export_csv?${qs.toString()}`);
  // [新增] 正確 CSV 來源：records.py 匯出 parsons_test_attempts
  const res = await fetch(`${API_BASE}/api/records/test_attempts.csv?${qs.toString()}`); // [新增]

  if (!res.ok) throw new Error(`test_attempts.csv failed: ${res.status}`);
  const text = await res.text();
  attempts.value = parseCsv(text);
}

const userMap = computed(() => {
  const m = new Map();
  for (const u of users.value) m.set(String(u.student_id), u);
  return m;
});

const attemptRows = computed(() => {
  const rows = [];
  for (const a of attempts.value) {
    const sid = String(a.student_id || "");
    const u = userMap.value.get(sid);
    if (className.value && u && u.class_name !== className.value) continue;

    rows.push({
      ...a,
      class_name: u?.class_name || a.class_name || "",
      name: u?.name || a.name || "",
    });
  }
  return rows;
});

const summaryRows = computed(() => {
  // 將 attempts 依 student_id + role 彙總
  const byStudent = new Map();
  for (const a of attempts.value) {
    const sid = String(a.student_id || "");
    if (!sid) continue;

    const u = userMap.value.get(sid);
    if (className.value && u && u.class_name !== className.value) continue;

    if (!byStudent.has(sid)) {
      byStudent.set(sid, {
        student_id: sid,
        class_name: u?.class_name || a.class_name || "",
        name: u?.name || a.name || "",
        pre_done: false,
        pre_score: "",
        pre_duration: "",
        post_done: false,
        post_score: "",
        post_duration: "",
      });
    }
    const r = byStudent.get(sid);
    if (a.test_role === "pre") {
      r.pre_done = true;
      r.pre_score = a.score ?? "";
      r.pre_duration = a.duration_sec ?? "";
    }
    if (a.test_role === "post") {
      r.post_done = true;
      r.post_score = a.score ?? "";
      r.post_duration = a.duration_sec ?? "";
    }
  }

  // 若 users 有但 attempts 沒資料，也要顯示（方便看誰未作答）
  for (const u of users.value) {
    const sid = String(u.student_id || "");
    if (!sid) continue;
    if (className.value && u.class_name !== className.value) continue;
    if (!byStudent.has(sid)) {
      byStudent.set(sid, {
        student_id: sid,
        class_name: u.class_name || "",
        name: u.name || "",
        pre_done: false,
        pre_score: "",
        pre_duration: "",
        post_done: false,
        post_score: "",
        post_duration: "",
      });
    }
  }

  return Array.from(byStudent.values()).sort((a, b) => a.student_id.localeCompare(b.student_id));
});

function downloadFile(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function downloadAttemptsCsv() {
  try {
    errorMsg.value = "";
    const qs = new URLSearchParams();

    // [新增] test_cycle_id 可留空
    if ((testCycleId.value || "").trim()) qs.set("test_cycle_id", (testCycleId.value || "").trim()); // [新增]
    if ((className.value || "").trim()) qs.set("class_name", (className.value || "").trim()); // [新增]

    // [刪除] const res = await fetch(`/api/parsons/test/export_csv?${qs.toString()}`);
    const res = await fetch(`${API_BASE}/api/records/test_attempts.csv?${qs.toString()}`); // [新增]

    if (!res.ok) throw new Error(`下載失敗：${res.status}`);
    const blob = await res.blob();

    const suffix = (testCycleId.value || "").trim() ? (testCycleId.value || "").trim() : "all"; // [新增]
    downloadFile(blob, `parsons_test_attempts_${suffix}.csv`); // [新增]
  } catch (e) {
    errorMsg.value = e?.message || String(e);
  }
}

function onPickStudentCsv(e) {
  const f = e?.target?.files?.[0];
  studentCsvFile.value = f || null;
}

async function uploadStudentsCsv() {
  try {
    importMsg.value = "";
    errorMsg.value = "";
    if (!studentCsvFile.value) return;

    loading.value = true;
    const fd = new FormData();
    fd.append("file", studentCsvFile.value);

    // [刪除] fd.append("default_password", defaultPassword.value || "123456");
    // [新增] 不用預設密碼：只有在你手動填了才送；否則後端可用 student_id 當密碼
    if ((defaultPassword.value || "").trim()) fd.append("default_password", (defaultPassword.value || "").trim()); // [新增]

    // [新增] 班級預設：CSV 若沒有 class_name，後端用這個
    fd.append("default_class_name", (className.value || "").trim() || "資工系A"); // [新增]

    // [修改] 使用 API_BASE
    const res = await fetch(`${API_BASE}/api/records/students/import_csv`, { // [新增]
      method: "POST",
      body: fd,
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || "匯入失敗");
    importMsg.value =
      `匯入完成：新增 ${data.inserted} 筆、更新 ${data.updated} 筆` +
      (data.errors?.length ? `（有 ${data.errors.length} 筆錯誤）` : "");
    await refreshAll();
  } catch (e) {
    errorMsg.value = e?.message || String(e);
  } finally {
    loading.value = false;
  }
}

async function downloadStudentsCsv() {
  try {
    errorMsg.value = "";
    const qs = new URLSearchParams(); // [新增]
    if ((className.value || "").trim()) qs.set("class_name", (className.value || "").trim()); // [新增]

    // [修改] 使用 API_BASE
    const res = await fetch(`${API_BASE}/api/records/students/export_csv?${qs.toString()}`); // [新增]
    if (!res.ok) throw new Error(`下載失敗：${res.status}`);
    const blob = await res.blob();
    downloadFile(blob, "students.csv");
  } catch (e) {
    errorMsg.value = e?.message || String(e);
  }
}

async function refreshAll() {
  try {
    loading.value = true;
    errorMsg.value = "";
    await Promise.all([fetchUsers(), fetchAttemptsCsv()]);
  } catch (e) {
    errorMsg.value = e?.message || String(e);
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  refreshAll();
});
</script>

<style scoped>
.layout {
  display: flex;
  min-height: 100vh;
  background: #f5f5f5;
}
.sidebar {
  width: 260px;
  background: #caa74a;
  color: #1b1b1b;
  padding: 18px 14px;
  box-sizing: border-box;
}
.profile {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.15);
  margin-bottom: 14px;
}
.avatar {
  width: 46px;
  height: 46px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  background: rgba(255, 255, 255, 0.35);
  font-size: 20px;
}
.hello .hi {
  font-weight: 800;
  font-size: 16px;
}
.hello .sub {
  font-size: 12px;
  opacity: 0.9;
  margin-top: 2px;
}
.menu {
  display: grid;
  gap: 10px;
}
.item {
  display: block;
  text-decoration: none;
  color: #1b1b1b;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.15);
  font-weight: 700;
}
.item:hover {
  background: rgba(255, 255, 255, 0.25);
}
.item.active {
  background: rgba(255, 255, 255, 0.35);
}
.content {
  flex: 1;
  padding: 18px 18px 26px;
  box-sizing: border-box;
}
.pageTitleRow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.pageTitle {
  margin: 0;
  font-size: 24px;
}
.panel {
  background: #fff;
  border-radius: 14px;
  padding: 14px;
  border: 2px solid #000;
  margin-bottom: 14px;
}
.panelTitle {
  font-size: 16px;
  font-weight: 900;
  margin-bottom: 10px;
}
.controls {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr auto;
  gap: 12px;
  align-items: end;
}
.field .label {
  font-size: 12px;
  font-weight: 900;
  margin-bottom: 6px;
}
.input {
  width: 100%;
  padding: 10px 10px;
  border: 2px solid #000;
  border-radius: 10px;
  box-sizing: border-box;
  outline: none;
  background: #fff;
}
.subhint {
  font-size: 12px;
  opacity: 0.7;
  margin-top: 6px;
}
.actions {
  display: flex;
  gap: 10px;
}
.btn {
  padding: 10px 14px;
  border-radius: 10px;
  border: 2px solid #000;
  background: #fff;
  font-weight: 900;
  cursor: pointer;
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn.primary {
  background: #111;
  color: #fff;
}
.hint {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 2px dashed #000;
  background: #fff9db;
  font-weight: 800;
}
.hint.ok {
  background: #eaffea;
}
.tableWrap {
  overflow: auto;
}
.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.table th,
.table td {
  border: 2px solid #000;
  padding: 8px 10px;
  white-space: nowrap;
}
</style>