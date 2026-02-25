<template>
  <div class="layout">
    <!-- ===== 左側 Sidebar ===== -->
    <aside class="sidebar">
      <div class="profile">
        <div class="avatar">👩‍🏫</div>
        <div class="hello">
          <div class="hello-title">您好，老師</div>
        </div>
      </div>

      <nav class="nav">
        <button class="nav-item"><span class="icon">📋</span><span>總覽</span></button>
        <button class="nav-item"><span class="icon">🎞️</span><span>影片管理</span></button>
        <button class="nav-item"><span class="icon">🤖</span><span>AI管理生成紀錄檢視</span></button>
        <button class="nav-item active"><span class="icon">📊</span><span>分析</span></button>
      </nav>

      <div class="sidebar-footer">
        <button class="logout">登出</button>
      </div>
    </aside>

    <!-- ===== 右側內容 ===== -->
    <main class="main">
      <header class="header">
        <h1 class="title">學習分析(學生錯誤類型、等級變動、前後測)</h1>

        <!-- 篩選列 -->
        <section class="filters">
          <div class="filter">
            <label>單元：</label>
            <select v-model="filters.unit">
              <option value="U1">U1</option>
              <option value="U2">U2</option>
              <option value="U3">U3</option>
            </select>
          </div>

          <div class="filter">
            <label>影片標題：</label>
            <select v-model="filters.video_id">
              <option value="">全部</option>
              <option v-for="v in videos" :key="v.video_id" :value="v.video_id">
                {{ v.title }}
              </option>
            </select>
          </div>

          <div class="filter">
            <label>班級：</label>
            <select v-model="filters.class_id">
              <option value="">全部</option>
              <option v-for="c in classOptions" :key="c.value" :value="c.value">
                {{ c.label }}
              </option>
            </select>
          </div>

          <div class="filter date">
            <input type="date" v-model="filters.from" />
            <span class="date-sep">-</span>
            <input type="date" v-model="filters.to" />
          </div>
        </section>
      </header>

      <!-- ===== 狀態列 ===== -->
      <div class="status-row" v-if="loading || errorMsg">
        <div v-if="loading" class="status loading">讀取分析資料中…</div>
        <div v-if="errorMsg" class="status error">⚠️ {{ errorMsg }}</div>
      </div>

      <!-- 卡片區 -->
      <section class="grid">
        <!-- 1. 前測 VS 後測（目前先顯示「前測平均答對/答錯」，後測之後再補） -->
        <div class="card">
          <div class="card-title">1. 學習成效：前測VS後測</div>
          <div class="card-body">
            <div class="chart-placeholder">
              <div class="bar-group">
                <div class="bar-label">答對</div>
                <div class="bar" :style="{ width: correctPct + '%' }"></div>
              </div>
              <div class="bar-group">
                <div class="bar-label">答錯</div>
                <div class="bar post" :style="{ width: wrongPct + '%' }"></div>
              </div>
            </div>

            <div class="legend">
              <span class="dot dot-pre"></span> 前測平均答對：{{ card1.avg_correct }}
              <span class="dot dot-post"></span> 前測平均答錯：{{ card1.avg_wrong }}
              <span class="muted">（樣本數 n={{ card1.n }}）</span>
            </div>

            <div class="hint">
              目前資料是「前測」；後測、前後測比較之後你新增 post session / post responses 就能加上。
            </div>
          </div>
        </div>

        <!-- 2. 等級變動（先保留雛型，不接 API） -->
        <div class="card">
          <div class="card-title">2. 學生難易度變動：L1-&gt;L2-&gt;L3</div>
          <div class="card-body">
            <div class="flow">
              <div class="level">
                <div class="pill l1">L1</div>
                <div class="count">L1 → L2（16 人）</div>
              </div>

              <div class="arrow">➡️</div>

              <div class="level">
                <div class="pill l2">L2</div>
                <div class="count">L2 → L3（6 人）</div>
                <div class="sub">L2 → L1（4 人）</div>
              </div>

              <div class="arrow">➡️</div>

              <div class="level">
                <div class="pill l3">L3</div>
              </div>
            </div>
            <div class="note">（雛型：之後等你有 L1/L2/L3 的 session 或 attempts，再接 API）</div>
          </div>
        </div>

        <!-- 3. 常見錯誤（接 API） -->
        <div class="card">
          <div class="card-title">3. 學生常見錯誤單元與題目</div>
          <div class="card-body">
            <div class="hbar">
              <div class="hbar-row" v-for="(x, i) in card3.by_category" :key="i">
                <div class="hbar-label">{{ x.category }}</div>
                <div class="hbar-track">
                  <div class="hbar-fill" :style="{ width: pctFromWrongCount(x.wrong_count) + '%' }"></div>
                </div>
                <div class="hbar-value">{{ x.wrong_count }}</div>
              </div>

              <div v-if="card3.by_category.length === 0" class="empty">
                （目前沒有答錯資料，或你篩選條件下沒有 responses）
              </div>
            </div>

            <div class="mini-table">
              <div class="mini-title">錯最多的題目（Top）</div>
              <ul>
                <li v-for="q in card3.top_wrong_questions" :key="q.question_id">
                  <span class="qid">#{{ q.question_id.slice(-6) }}</span>
                  <span class="stem">{{ q.stem }}</span>
                  <span class="badge">錯 {{ q.wrong_count }} 次</span>
                </li>
              </ul>

              <div v-if="card3.top_wrong_questions.length === 0" class="empty">
                （目前沒有 Top 錯題）
              </div>
            </div>
          </div>
        </div>

        <!-- 4. 認知負荷（保留雛型） -->
        <div class="card">
          <div class="card-title">4. 認知負荷問卷</div>
          <div class="card-body">
            <div class="line-placeholder">
              <div class="line-hint">（雛型：之後接 surveys 畫折線圖）</div>
              <div class="line-grid"></div>
            </div>
            <div class="subnote">1-7分（分數越高表示負荷越高）</div>
          </div>
        </div>

        <!-- 6. 學習行為（接 API） -->
        <div class="card">
          <div class="card-title">6. 學生學習行為指標</div>
          <div class="card-body">
            <div class="metrics">
              <div class="metric">
                <div class="m-label">平均學習秒數</div>
                <div class="m-value">{{ round1(card6.learning_logs.avg_duration_sec) }}</div>
              </div>
              <div class="metric">
                <div class="m-label">平均重新生成次數</div>
                <div class="m-value">{{ round2(card6.learning_logs.avg_regen_clicks) }}</div>
              </div>
              <div class="metric">
                <div class="m-label">作答總數</div>
                <div class="m-value">{{ card6.responses.total }}</div>
              </div>
              <div class="metric">
                <div class="m-label">正確率</div>
                <div class="m-value">{{ accuracyRate }}%</div>
              </div>
              <div class="metric">
                <div class="m-label">平均作答秒數</div>
                <div class="m-value">{{ round2(card6.responses.avg_time_spent) }}</div>
              </div>
              <div class="metric">
                <div class="m-label">提示使用率</div>
                <div class="m-value">{{ round1(card6.responses.hint_rate * 100) }}%</div>
              </div>
            </div>

            <div class="mini">
              <div>learning_logs 筆數：<b>{{ card6.learning_logs.n }}</b></div>
              <div>答對/答錯：<b>{{ card6.responses.correct }}</b> / <b>{{ card6.responses.wrong }}</b></div>
            </div>
          </div>
        </div>

        <!-- 5. 自我效能（保留雛型） -->
        <div class="card">
          <div class="card-title">5. 自我效能問卷</div>
          <div class="card-body">
            <div class="line-placeholder">
              <div class="line-hint">（雛型：之後接 surveys 畫折線圖）</div>
              <div class="line-grid"></div>
            </div>
            <div class="subnote">1-7分（分數越高表示自我效能越高）</div>
          </div>
        </div>
      </section>

      <footer class="actions">
        <button class="btn secondary" @click="onExportCSV">匯出CSV檔</button>
        <button class="btn primary" @click="onExportPDF">匯出PDF檔</button>
      </footer>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import axios from "axios";

/** ✅ 如果你有共用的 axios instance（你之前有 api），可以換成：
 * import { api } from "../api";
 * 然後把 axios.get(...) 改成 api.get(...)
 */
const BACKEND = "http://127.0.0.1:5000";

const filters = reactive({
  unit: "U1",
  video_id: "", // 全部
  class_id: "", // 全部
  from: "2026-01-01",
  to: "2026-02-01",
});

const loading = ref(false);
const errorMsg = ref("");

/** 影片下拉：先用「依 unit 撈 videos」的 API
 *  你目前有 /api/admin_upload/videos?status=active&unit=U1
 */
const videos = ref([]);

/** 班級：你目前 DB 可能還沒做 class，因此先用假選項
 *  之後你有 users.class_id / sessions.class_id 再接真 API
 */
const classOptions = ref([
  { value: "A", label: "甲班" },
  { value: "B", label: "乙班" },
]);

/** Analytics 回傳資料容器 */
const data = reactive({
  cards: {
    card1_pre: { avg_correct: 0, avg_wrong: 0, n: 0 },
    card3_errors: { by_category: [], top_wrong_questions: [] },
    card6_behavior: {
      learning_logs: { avg_duration_sec: 0, avg_regen_clicks: 0, n: 0, understood_false: 0, understood_true: 0 },
      responses: { avg_hint_count: 0, avg_time_spent: 0, correct: 0, hint_rate: 0, total: 0, wrong: 0 },
    },
  },
});

/** ====== computed 對應卡片 ====== */
const card1 = computed(() => data.cards.card1_pre || { avg_correct: 0, avg_wrong: 0, n: 0 });
const card3 = computed(() => data.cards.card3_errors || { by_category: [], top_wrong_questions: [] });
const card6 = computed(() => data.cards.card6_behavior || { learning_logs: {}, responses: {} });

/** 卡 1 bar 比例：用 (答對/答錯) 在 (答對+答錯) 的比例 */
const correctPct = computed(() => {
  const c = Number(card1.value.avg_correct || 0);
  const w = Number(card1.value.avg_wrong || 0);
  const total = c + w;
  if (total <= 0) return 0;
  return Math.round((c / total) * 100);
});
const wrongPct = computed(() => {
  const c = Number(card1.value.avg_correct || 0);
  const w = Number(card1.value.avg_wrong || 0);
  const total = c + w;
  if (total <= 0) return 0;
  return Math.round((w / total) * 100);
});

const accuracyRate = computed(() => {
  const total = Number(card6.value.responses?.total || 0);
  const correct = Number(card6.value.responses?.correct || 0);
  if (total <= 0) return 0;
  return Math.round((correct / total) * 100);
});

/** 卡 3 bar：用最大 wrong_count 當 100% */
function pctFromWrongCount(wrongCount) {
  const arr = card3.value.by_category || [];
  const max = arr.reduce((m, x) => Math.max(m, Number(x.wrong_count || 0)), 0);
  if (!max) return 0;
  return Math.round((Number(wrongCount || 0) / max) * 100);
}

/** 取整 */
function round1(x) {
  const n = Number(x || 0);
  return Math.round(n * 10) / 10;
}
function round2(x) {
  const n = Number(x || 0);
  return Math.round(n * 100) / 100;
}

/** ====== API 呼叫 ====== */
async function fetchVideos() {
  try {
    // 你之前截圖有成功：/api/admin_upload/videos?status=active&unit=U1&title=&page=1&per_page=9999
    const url = `${BACKEND}/api/admin_upload/videos`;
    const res = await axios.get(url, {
      params: {
        status: "active",
        unit: filters.unit,
        title: "",
        page: 1,
        per_page: 9999,
      },
    });
    // 這裡不確定你回傳格式，先做容錯：
    const items = res.data?.items || res.data?.videos || res.data || [];
    videos.value = (Array.isArray(items) ? items : []).map((v) => ({
      video_id: v._id || v.video_id || v.id || "",
      title: v.title || v.original_name || v.filename || "未命名影片",
    }));
  } catch (e) {
    // 影片下拉失敗不擋分析頁
    videos.value = [];
  }
}

async function fetchAnalytics() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const url = `${BACKEND}/api/analytics/analytics`;
    const res = await axios.get(url, {
      params: {
        unit: filters.unit || undefined,
        from: filters.from || undefined,
        to: filters.to || undefined,
        video_id: filters.video_id || undefined,
        class_id: filters.class_id || undefined,
      },
    });

    // 你的回傳目前長這樣：{ cards: { card1_pre:..., card3_errors:..., card6_behavior:... }, filters_used:... }
    if (!res.data || !res.data.cards) {
      throw new Error("API 回傳格式不含 cards");
    }

    // 安全塞入
    data.cards = {
      ...data.cards,
      ...res.data.cards,
    };
  } catch (e) {
    errorMsg.value =
      e?.response?.data?.message ||
      e?.message ||
      "讀取分析資料失敗（請確認後端 /api/analytics/analytics 有啟動）";
  } finally {
    loading.value = false;
  }
}

/** 篩選變動就重新抓 */
watch(
  () => ({ ...filters }),
  async () => {
    // unit 變更時，影片清單也要更新
    await fetchVideos();
    await fetchAnalytics();
  },
  { deep: true }
);

onMounted(async () => {
  await fetchVideos();
  await fetchAnalytics();
});

function onExportCSV() {
  alert("下一步：我會幫你做 /api/analytics/export/csv，並把 filters 帶過去");
}
function onExportPDF() {
  alert("下一步：我會幫你做 /api/analytics/export/pdf 或前端列印成 PDF");
}
</script>

<style scoped>
/* ===== Layout ===== */
.layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 100vh;
  background: #f6f6f6;
  font-family: "Microsoft JhengHei", system-ui, sans-serif;
}

/* ===== Sidebar ===== */
.sidebar {
  background: #d7b15e;
  padding: 18px 14px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.profile {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px;
  background: rgba(255, 255, 255, 0.25);
  border-radius: 14px;
}
.avatar {
  width: 44px;
  height: 44px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.55);
  display: grid;
  place-items: center;
  font-size: 22px;
}
.hello-title {
  font-weight: 900;
  font-size: 18px;
}
.nav {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 6px;
}
.nav-item {
  border: 0;
  border-radius: 14px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.25);
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  text-align: left;
}
.nav-item.active {
  background: rgba(255, 255, 255, 0.5);
  font-weight: 900;
}
.icon {
  width: 24px;
  text-align: center;
}
.sidebar-footer {
  margin-top: auto;
}
.logout {
  width: 100%;
  border: 2px solid rgba(0, 0, 0, 0.2);
  background: rgba(255, 255, 255, 0.2);
  border-radius: 14px;
  padding: 10px 12px;
  cursor: pointer;
}

/* ===== Main ===== */
.main {
  padding: 18px 18px 26px;
}
.header {
  background: #fff;
  border-radius: 18px;
  padding: 16px 18px;
  border: 2px solid rgba(0, 0, 0, 0.08);
}
.title {
  margin: 0 0 12px;
  font-size: 20px;
  font-weight: 900;
  text-align: center;
}

/* ===== Filters ===== */
.filters {
  display: grid;
  grid-template-columns: repeat(4, max-content) 1fr;
  gap: 12px;
  align-items: center;
  justify-content: center;
}
.filter {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.filter label {
  font-weight: 900;
}
select,
input[type="date"] {
  height: 34px;
  border-radius: 10px;
  border: 2px solid rgba(0, 0, 0, 0.15);
  padding: 0 10px;
  background: #fff;
}
.filter.date {
  justify-content: flex-end;
  gap: 8px;
}
.date-sep {
  color: rgba(0, 0, 0, 0.45);
}

/* ===== status ===== */
.status-row {
  margin-top: 10px;
  display: grid;
  gap: 10px;
}
.status {
  border-radius: 14px;
  padding: 10px 12px;
  border: 2px solid rgba(0, 0, 0, 0.08);
  background: #fff;
  font-weight: 800;
}
.status.loading {
  opacity: 0.8;
}
.status.error {
  border-color: rgba(255, 0, 0, 0.18);
}

/* ===== Grid Cards ===== */
.grid {
  margin-top: 16px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.card {
  background: #fff;
  border-radius: 18px;
  border: 3px solid rgba(0, 0, 0, 0.22);
  padding: 14px 14px 12px;
}
.card-title {
  font-weight: 900;
  margin-bottom: 10px;
  text-align: center;
}
.card-body {
  min-height: 220px;
}

/* ===== Card 1 placeholder ===== */
.chart-placeholder {
  display: grid;
  gap: 10px;
  padding: 10px;
  border-radius: 14px;
  background: #fafafa;
  border: 2px dashed rgba(0, 0, 0, 0.15);
}
.bar-group {
  display: grid;
  grid-template-columns: 50px 1fr;
  align-items: center;
  gap: 10px;
}
.bar-label {
  font-weight: 900;
  color: rgba(0, 0, 0, 0.6);
}
.bar {
  height: 18px;
  border-radius: 999px;
  background: #2b7bbb;
}
.bar.post {
  background: #2aa84a;
}
.legend {
  margin-top: 10px;
  display: flex;
  gap: 12px;
  justify-content: center;
  align-items: center;
  flex-wrap: wrap;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  display: inline-block;
}
.dot-pre {
  background: #2b7bbb;
}
.dot-post {
  background: #2aa84a;
}
.muted {
  color: rgba(0, 0, 0, 0.55);
  font-weight: 700;
}
.hint {
  margin-top: 10px;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.55);
  text-align: center;
}

/* ===== Flow (card 2 prototype) ===== */
.flow {
  display: grid;
  grid-template-columns: 1fr auto 1fr auto 1fr;
  gap: 10px;
  align-items: center;
}
.level {
  display: grid;
  gap: 6px;
  justify-items: center;
}
.pill {
  width: 64px;
  height: 140px;
  border-radius: 18px;
  display: grid;
  place-items: center;
  font-weight: 900;
  color: #fff;
}
.l1 {
  background: #1f3d7a;
}
.l2 {
  background: #1aa0a0;
}
.l3 {
  background: #d7a04c;
}
.arrow {
  font-size: 22px;
  opacity: 0.75;
}
.count {
  font-weight: 900;
}
.sub {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.55);
}
.note {
  margin-top: 10px;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.55);
  text-align: center;
}

/* ===== Card 3 ===== */
.hbar {
  display: grid;
  gap: 10px;
}
.hbar-row {
  display: grid;
  grid-template-columns: 90px 1fr 38px;
  gap: 10px;
  align-items: center;
}
.hbar-label {
  font-weight: 900;
}
.hbar-track {
  height: 18px;
  background: #eee;
  border-radius: 999px;
  overflow: hidden;
}
.hbar-fill {
  height: 100%;
  background: #21b5c0;
  border-radius: 999px;
}
.hbar-value {
  text-align: right;
  font-weight: 900;
}
.mini-table {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed rgba(0, 0, 0, 0.15);
}
.mini-title {
  font-weight: 900;
  margin-bottom: 6px;
}
.mini-table ul {
  margin: 0;
  padding-left: 18px;
}
.mini-table li {
  margin: 6px 0;
  display: flex;
  gap: 8px;
  align-items: center;
}
.qid {
  font-weight: 900;
}
.stem {
  flex: 1;
  color: rgba(0, 0, 0, 0.75);
}
.badge {
  background: rgba(0, 0, 0, 0.06);
  border: 1px solid rgba(0, 0, 0, 0.12);
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
}
.empty {
  margin-top: 8px;
  color: rgba(0, 0, 0, 0.55);
  font-weight: 700;
  text-align: center;
}

/* ===== Card 4/5 placeholder ===== */
.line-placeholder {
  height: 170px;
  border-radius: 14px;
  background: #fafafa;
  border: 2px dashed rgba(0, 0, 0, 0.15);
  display: grid;
  place-items: center;
  position: relative;
  overflow: hidden;
}
.line-grid {
  position: absolute;
  inset: 0;
  background: linear-gradient(to right, rgba(0, 0, 0, 0.06) 1px, transparent 1px) 0
      0 / 40px 40px,
    linear-gradient(to bottom, rgba(0, 0, 0, 0.06) 1px, transparent 1px) 0 0 / 40px
      40px;
  opacity: 0.5;
}
.line-hint {
  position: relative;
  z-index: 1;
  font-weight: 900;
  color: rgba(0, 0, 0, 0.55);
}
.subnote {
  margin-top: 10px;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.55);
  text-align: center;
}

/* ===== Card 6 ===== */
.metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.metric {
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 14px;
  padding: 10px;
  background: rgba(0, 0, 0, 0.02);
}
.m-label {
  font-weight: 900;
  color: rgba(0, 0, 0, 0.6);
  font-size: 13px;
}
.m-value {
  font-weight: 900;
  font-size: 22px;
  margin-top: 6px;
}
.mini {
  margin-top: 10px;
  font-size: 13px;
  color: rgba(0, 0, 0, 0.7);
}

/* ===== Actions ===== */
.actions {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
.btn {
  border-radius: 12px;
  padding: 10px 14px;
  border: 2px solid rgba(0, 0, 0, 0.18);
  cursor: pointer;
  font-weight: 900;
}
.btn.primary {
  background: #f0c15f;
}
.btn.secondary {
  background: #f6f6f6;
}

/* ===== Responsive ===== */
@media (max-width: 1100px) {
  .filters {
    grid-template-columns: 1fr 1fr;
    justify-content: stretch;
  }
  .filter.date {
    justify-content: flex-start;
  }
}
@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .sidebar {
    position: sticky;
    top: 0;
    z-index: 10;
  }
  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
