<template>
  <div class="t5-layout">
    <!-- ===== Sidebar ===== -->
    <aside class="sidebar">
      <div class="profile">
        <div class="avatar"></div>
        <div class="hello">您好，老師</div>
      </div>

      <nav class="menu">
        <button class="menu-item">總覽</button>
        <button class="menu-item">影片管理</button>
        <button class="menu-item active">AI 管理生成紀錄檢視</button>
        <button class="menu-item">分析</button>
      </nav>

      <div class="logout">
        <button class="btn-outline">登出</button>
      </div>
    </aside>

    <!-- ===== Main ===== -->
    <main class="main">
      <h1 class="title">AI 代理 Parsons 題目生成紀錄</h1>

      <!-- [新增] 後測開放控制（僅新增按鈕，不影響既有功能） -->
      <div class="posttest-bar">
        <div class="posttest-left">
          <span class="posttest-label">後測狀態：</span>
          <span class="posttest-status" :class="{ open: postOpen === true, closed: postOpen === false }">
            {{ postOpen === null ? "未讀取" : (postOpen ? "已開放" : "未開放") }}
          </span>
          <span class="posttest-hint">（依單元控制：{{ testCycleId }}）</span>
        </div>

        <div class="posttest-actions">
          <button class="btn primary" :disabled="!testCycleId || postOpenLoading" @click="setPostOpen(true)">
            後測發布
          </button>
          <button class="btn warn" :disabled="!testCycleId || postOpenLoading" @click="setPostOpen(false)">
            後測取消發布
          </button>
        </div>
      </div>


      <section class="grid">
        <!-- A -->
        <div class="card">
          <h2 class="card-title">A. 影片與字幕資訊</h2>

          <div class="row2">
            <div class="field">
              <label>單元</label>
              <select v-model="selectedUnit" :disabled="loading.units">
                <option value="">請選擇</option>
                <option v-for="u in units" :key="u.id" :value="u.id">{{ u.name }}</option>
              </select>
            </div>

            <div class="field">
              <label>影片標題</label>
              <select v-model="selectedVideo" :disabled="!selectedUnit || loading.videos">
                <option value="">請選擇</option>
                <option v-for="v in videos" :key="v.id" :value="v.id">{{ v.title }}</option>
              </select>
            </div>
          </div>

          <div class="row2 info">
            <div class="kv">
              <span class="k">影片狀態：</span>
              <span class="chip" :class="videoInfo.enabled ? 'chip-ok' : 'chip-off'">
                {{ videoInfo.enabled ? "啟用" : "停用" }}
              </span>
            </div>

            <div class="kv">
              <span class="k">字幕：</span>
              <span class="chip chip-ok" v-if="videoInfo.subtitle_uploaded">已上傳</span>
              <span class="chip chip-off" v-else>未上傳</span>

              <span class="chip chip-ok" v-if="videoInfo.subtitle_verified">已校正</span>
              <span class="chip chip-off" v-else>未校正</span>
            </div>
          </div>

          <div class="row2 info">
            <div class="kv"><span class="k">來源：</span>{{ videoInfo.source || "—" }}</div>
            <div class="kv">
              <span class="k">字幕版本：</span>
              <select v-model="selectedSubtitleVersion" :disabled="!videoInfo.subtitle_versions?.length">
                <option v-for="sv in videoInfo.subtitle_versions" :key="sv" :value="sv">{{ sv }}</option>
              </select>
            </div>
          </div>

          <div class="row2 info">
            <div class="kv"><span class="k">影片時間：</span>{{ videoInfo.duration || "—" }}</div>
            <div class="actions">
              <button class="btn" @click="goVideoManage" :disabled="!selectedVideo">前往影片管理</button>
              <button class="btn" @click="goSubtitleCheck" :disabled="!selectedVideo">前往字幕校正</button>
            </div>
          </div>

          <p class="hint err" v-if="err.a">{{ err.a }}</p>
        </div>

        <!-- B -->
        <div class="card">
          <h2 class="card-title">B. AI 代理任務流程</h2>
          <div class="flow">
            <div class="step">
              <div class="num">1</div>
              <div class="box">
                <div class="icon">📄</div>
                <div class="txt">Text/Code</div>
              </div>
            </div>
            <div class="arrow">→</div>
            <div class="step">
              <div class="num">2</div>
              <div class="box">
                <div class="icon">💡</div>
                <div class="txt">Key Concepts</div>
              </div>
            </div>
            <div class="arrow">→</div>
            <div class="step">
              <div class="num">3</div>
              <div class="box">
                <div class="icon">🧩</div>
                <div class="txt">Parsons Puzzle</div>
              </div>
            </div>
          </div>

          <div class="meta">
            <div>使用模型：OpenAI</div>
            <div>執行方式：AI Agent 自動生成</div>
          </div>
        </div>

        <!-- C -->
        <div class="card">
          <h2 class="card-title">C. 題目設定</h2>
          <div class="kvcol">
            <div class="kv"><span class="k">類型：</span>Parsons 程式除錯題</div>
            <div class="kv"><span class="k">數量：</span>1 題 / 影片</div>
            <div class="kv"><span class="k">語言：</span>Python</div>
            <div class="kv"><span class="k">依據：</span>影片字幕檔生成</div>
          </div>
        </div>

        <!-- D -->
        <div class="card">
          <h2 class="card-title">D. 題目內容預覽與審核</h2>

          <!-- filters -->
          <div class="d-toolbar">
            <div class="d-filter">
              <label>狀態：</label>
              <select v-model="filterStatus" :disabled="!selectedVideo || loading.questions">
                <option value="all">全部</option>
                <option value="pending">待審核</option>
                <option value="published">已上架</option>
                <option value="rejected">已退回</option>
              </select>
            </div>

            <div class="d-filter">
              <label>排序：</label>
              <select v-model="sortOrder" :disabled="!selectedVideo || loading.questions">
                <option value="newest">生成時間（新→舊）</option>
                <option value="oldest">生成時間（舊→新）</option>
              </select>
            </div>
          </div>

          <!-- states -->
          <div class="d-state" v-if="!selectedVideo">
            請先於 A 區塊選擇影片後，再查看題目版本。
          </div>

          <div class="d-state" v-else-if="loading.questions">
            ⏳ 載入題目中，請稍候…
          </div>

          <div class="d-state err" v-else-if="err.d">
            ⚠️ 題目載入失敗<br />
            原因：{{ err.d }}<br />
            <button class="btn mini" @click="fetchQuestions()">重新嘗試</button>
          </div>

          <div class="d-state" v-else-if="questions.length === 0">
            📭 尚無題目版本<br />
            請點擊「建立題目」以建立 AI 題目。<br />
            <button class="btn mini" @click="regenerate()">建立題目</button>
          </div>

          <!-- table -->
          <div v-else class="table-wrap">
            <table class="t">
              <thead>
                <tr>
                  <th style="width: 90px;">題目ID</th>
                  <th style="width: 170px;">生成時間</th>
                  <th style="width: 150px;">狀態</th>
                  <th style="width: 210px;">操作</th>
                  <th style="width: 90px;">備註</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="q in questions" :key="q.task_id">
                  <td class="mono">
                    {{ q.version }}
                    <div class="sub" v-if="q.parent_version">（由 {{ q.parent_version }} 重新生成）</div>
                  </td>
                  <td class="mono">{{ fmtTime(q.created_at) }}</td>
                  <td>
                    <span class="dot" :class="dotClass(q.status)"></span>
                    {{ q.status_zh }}
                    <div class="sub" v-if="q.status === 'pending'">學生端不可見</div>
                  </td>
                  <td>
                    <div class="btns">
                      <button class="pillBtn preview" @click="openPreview(q)">預覽</button>

                      <button
                        v-if="q.status !== 'published'"
                        class="pillBtn publish"
                        @click="publish(q)"
                      >
                        發布
                      </button>

                      <button
                        v-else
                        class="pillBtn unpub"
                        @click="unpublish(q)"
                      >
                        取消發布
                      </button>

                      <button class="pillBtn regen" @click="regenerate(q)">重新生成</button>
                    </div>
                  </td>
                  <td>
                    <button class="noteBtn" @click="openPreview(q)">
                      📝 {{ q.has_note ? "老師備註" : "—" }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <p class="hint err" v-if="err.e">{{ err.e }}</p>
        </div>
      </section>

    <!-- ===== Preview Modal（統一用 previewData）===== -->
    <div v-if="modal.open" class="modal-mask" @click.self="closeModal">
      <div class="modal">
        <div class="modal-head">
          <div class="modal-title">
            【AI 題目生成紀錄｜題目預覽（{{ previewData?.meta?.version || "—" }}）】
          </div>
          <button class="x" @click="closeModal">✕</button>
        </div>

        <!-- 三態：loading / error / content -->
        <div class="modal-body" v-if="modal.loading">
          ⏳ 題目載入中…
        </div>

        <div class="modal-body" v-else-if="modal.err">
          ⚠️ 題目載入失敗：{{ modal.err }}
        </div>

        <div class="modal-body" v-else>
          <div class="pv" v-if="previewData?.ok">
            <!-- Header meta -->
            <div class="pvTop">
              <div class="pvMetaRow">
                <div class="pill">教學來源：{{ previewData.meta.unit || "—" }} / {{ previewData.meta.title || "—" }}</div>
                <div class="pill">分析片段：{{ previewData.meta.segment_label || "—" }}</div>
                <div class="pill">字幕版本：{{ previewData.meta.subtitle_version || "—" }}</div>
                <div class="pill">
                  題目狀態：{{ previewData.meta.status || "—" }}
                  （{{ previewData.meta.enabled ? "學生端可見" : "學生端不可見" }}）
                </div>
              </div>
              <hr class="pvHr" />
            </div>

            <!-- 題目說明 -->
            <div class="pvSection">
              <div class="pvH">【題目說明】</div>
              <div class="pvBox">
                {{ previewData.prompt ? previewData.prompt : "（未提供題目敘述）" }}
              </div>
            </div>

            <!-- Parsons 區塊 -->
            <div class="pvSection">
              <div class="pvH">Parsons 區塊（AI 生成｜逐區塊中文語意）</div>
              <div class="pvGrid">
                <div class="pvBlock" v-for="b in (previewData.parsons_blocks || [])" :key="b.id">
                  <div class="code">{{ b.text || "（空）" }}</div>
                  <div class="zh">中文語意（AI）：{{ enhanceMeaning(b.text, b.meaning_zh) }}</div>
                </div>
                <div v-if="!(previewData.parsons_blocks || []).length" class="pvEmpty">
                  尚無 Parsons 區塊資料（請確認題目生成時是否有存 blocks）。
                </div>
              </div>
            </div>

          <!-- 干擾區塊 -->
          <div class="pvSection">
            <div class="pvH">B-2 干擾區塊（Distractor Blocks｜AI 生成）</div>

            <div class="pvGrid">
              <div
                class="pvBlock pvBlockD"
                v-for="b in (previewData.distractor_blocks || [])"
                :key="'d-' + (b.id || b._id)"
                :class="{ removed: !isKeepDistractor(b.id || b._id) }"
              >
                <!-- 右上角 ✅/❌ -->
                <div class="dCtrl">
                  <button
                    class="dBtn ok"
                    type="button"
                    :class="{ active: isKeepDistractor(b.id || b._id) }"
                    @click="keepDistractor(b.id || b._id)"
                    title="保留此干擾（學生端會看到）"
                  >
                    ✅
                  </button>
                  <button
                    class="dBtn no"
                    type="button"
                    :class="{ active: !isKeepDistractor(b.id || b._id) }"
                    @click="removeDistractor(b.id || b._id)"
                    title="移除此干擾（學生端不會看到）"
                  >
                    ❌
                  </button>
                </div>

                <div class="code">{{ b.text || "（空）" }}</div>
                <div class="zh">
                  中文語意（AI）：{{ enhanceMeaning(b.text, b.meaning_zh) }}
                  <span v-if="!isKeepDistractor(b.id || b._id)" class="removedTag">（已標記移除）</span>
                </div>
              </div>

              <div v-if="!(previewData.distractor_blocks || []).length" class="pvEmpty">
                尚無干擾區塊資料（請確認生成流程是否有存 distractor_blocks）。
              </div>
            </div>
          </div>

            <!-- 正確答案順序 -->
            <div class="pvSection">
              <div class="pvH">【正確答案順序（僅教師可見）】</div>
              <div class="pvOrder">
                {{ previewData.solution_order_text || "（未提供）" }}
              </div>
              <!-- ✅ 顯示對應程式碼（老師一眼看懂） -->
              <div class="pvOrderList" v-if="solutionDetailList.length">
                <div class="pvOrderItem" v-for="r in solutionDetailList" :key="r.id">
                  <div class="pvOrderIdx">{{ r.idx }}.</div>
                  <div class="pvOrderMain">
                    <div class="pvOrderCode"><span class="pvOrderId">{{ r.id }}</span> {{ r.text }}</div>
                    <div class="pvOrderZh" v-if="r.meaning_zh">中文語意：{{ r.meaning_zh }}</div>
                  </div>
                </div>
              </div>

              <div class="pvSmall">
                版本：{{ previewData.meta.version }}　生成時間：{{ previewData.meta.created_at || "—" }}　建立者：{{ previewData.meta.created_by || "AI Agent" }}
              </div>
            </div>

            <!-- 問題類型 + 老師備註 -->
            <div class="pvSection">
              <div class="pvH">問題類型（可複選）</div>
              <div class="pvChecks">
                <label><input type="checkbox" v-model="reviewForm.tags" value="題幹過長" /> 題幹過長</label>
                <label><input type="checkbox" v-model="reviewForm.tags" value="中文語意提示不清楚" /> 中文語意提示不清楚</label>
                <label><input type="checkbox" v-model="reviewForm.tags" value="干擾選項不清楚" /> 干擾選項不清楚</label>
                <label><input type="checkbox" v-model="reviewForm.tags" value="題目難度過高" /> 題目難度過高</label>
                <label><input type="checkbox" v-model="reviewForm.tags" value="其他" /> 其他</label>
              </div>

              <div class="pvH" style="margin-top:10px;">老師備註（選填）</div>
              <textarea class="pvNote" v-model="reviewForm.note" placeholder="題目敘述偏長 / 干擾片段不夠明確 / 難度需調整 …"></textarea>
            </div>
          </div>

          <div v-else class="d-state err">
            ⚠️ 無法顯示預覽資料（previewData 為空或 ok=false）
          </div>

          <!-- ✅ 新增：固定底部按鈕列 -->
          <div class="modal-foot" v-if="!modal.loading && !modal.err && previewData?.ok">
            <div class="pvActions">
              <button class="btn primary" @click="publishFromPreview" :disabled="previewData.meta.enabled">發布至學生題庫</button>
              <button class="btn" @click="regenerate">重新生成新版本</button>
              <button class="btn warn" @click="returnNotPublish">退回不發布</button>
              <button class="btn" @click="closeModal">關閉</button>
            </div>
          </div>

        </div>
      </div>
    </div>



    </main>
  </div>
</template>

<script setup>
import { ref, reactive, watch, onMounted, computed } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";

// ✅ Teacher T5 API base（有些版本用 /api/teacher/t5，有些用 /api/teacher_t5）
const T5_BASE_PRIMARY = "/api/teacher_t5";
const T5_BASE_FALLBACK = "/api/teacher_t5";

async function t5Get(path, config = {}) {
  try {
    return await api.get(`${T5_BASE_PRIMARY}${path}`, config);
  } catch (e) {
    const status = e?.response?.status;
    if (status === 404) return await api.get(`${T5_BASE_FALLBACK}${path}`, config);
    throw e;
  }
}

async function t5Post(path, data = {}, config = {}) {
  try {
    return await api.post(`${T5_BASE_PRIMARY}${path}`, data, config);
  } catch (e) {
    const status = e?.response?.status;
    if (status === 404) return await api.post(`${T5_BASE_FALLBACK}${path}`, data, config);
    throw e;
  }
}
/** 干擾區塊：老師保留/移除狀態
 *  true  = 保留（會出現在學生端）
 *  false = 移除（不會出現在學生端）
 */
const distractorKeep = reactive({}); // { [blockId]: true/false }

const router = useRouter();

// ===== state =====
const units = ref([]);
const videos = ref([]);
const selectedUnit = ref("");
const selectedVideo = ref("");
const selectedSubtitleVersion = ref("");


// [新增] ===== 後測開放控制（依單元） =====
const testCycleId = computed(() => (selectedUnit.value || "default").toString().trim());
const postOpen = ref(null); // null=未讀取, true/false=狀態
const postOpenLoading = ref(false);

async function fetchPostOpen() {
  if (!testCycleId.value) return;
  postOpenLoading.value = true;
  try {
    const { data } = await api.get("/api/parsons/test/cycle/get", { params: { test_cycle_id: testCycleId.value } }); // [新增]
    postOpen.value = !!data?.post_open;
  } catch (e) {
    // 若後端尚未加入此 API，不讓頁面壞掉
    postOpen.value = null;
  } finally {
    postOpenLoading.value = false;
  }
}

// [新增] 切換單元時重新讀取後測開放狀態
async function setPostOpen(open) {
  if (!testCycleId.value) return;

  postOpenLoading.value = true;

  try {
    await api.post("/api/parsons/test/cycle/toggle", {
      test_cycle_id: testCycleId.value,
      post_open: open
    });

    postOpen.value = open;

  } catch (e) {
    console.error("toggle error:", e);
  } finally {
    postOpenLoading.value = false;
  }
}


const loading = reactive({ units: false, videos: false, videoInfo: false, questions: false });
const busy = reactive({ regen: false });
const err = reactive({ a: "", d: "", e: "" });

const videoInfo = reactive({
  enabled: true,
  subtitle_uploaded: false,
  subtitle_verified: false,
  subtitle_versions: [],
  source: "",
  duration: ""
});

// D table
const filterStatus = ref("all");
const sortOrder = ref("newest");
const questions = ref([]);
const previewData = ref(null);

// Modal
const modal = reactive({
  open: false,
  loading: false,
  err: "",
  data: null
});


async function openPreview(row = null) {
  console.log("[openPreview] row =", row);

  modal.open = true;
  modal.loading = true;
  modal.err = "";
  previewData.value = null;

  try {
    const taskId = row?.task_id;
    console.log("[openPreview] task_id =", taskId);

    if (!taskId) {
      throw new Error("這一列沒有 task_id（D 區塊列表資料缺少 task_id）");
    }

    const { data } = await t5Get("/question", {
      params: { task_id: taskId }
    });

    console.log("[openPreview] api data =", data);

    if (!data?.ok) {
      throw new Error(data?.error || "讀取題目失敗");
    }

    const question = data.question || {};
    const prompt = question.prompt || question.title || question.text || "";
    initDistractorKeep(previewData.value?.distractor_blocks || []);


    const mapBlocks = (arr = []) =>
      (arr || []).map((b, idx) => ({
        id: String(b.id ?? b._id ?? `b${idx}`),
        text: b.text || b.code || b.line || "",
        meaning_zh: b.semantic_zh || b.semantic || b.zh || ""
      }));

    const solutionOrderText = Array.isArray(data.solution_order)
      ? data.solution_order.join(" → ")
      : (data.solution_order || "");

    previewData.value = {
      ok: true,
      meta: {
        task_id: data.task_id,
        version: data.version || row.version || "—",
        unit: selectedUnit.value || "—",
        title: selectedVideoTitle.value || "—",
        segment_label: data.segment_label || "—",
        subtitle_version: selectedSubtitleVersion.value || "—",
        status: data.status_zh || data.status || "—",
        enabled: !!data.student_visible,
        created_at: data.created_at || "—",
        created_by: "AI Agent"
      },
      prompt,
      parsons_blocks: mapBlocks(data.solution_blocks),
      distractor_blocks: mapBlocks(data.distractor_blocks),
      solution_order_text: solutionOrderText
    };

    reviewForm.tags = data.review_tags || [];
    reviewForm.note = data.review_note || "";

  } catch (e) {
    console.error("[openPreview] error =", e);
    modal.err = e?.message || "讀取預覽失敗（請看 console）";
  } finally {
    modal.loading = false;
  }
}

// 老師審核用：從 previewData 計算出正確答案區塊的詳細資訊（包含中文語意）
const solutionDetailList = computed(() => {
  // blocks 來源：你目前預覽用的是 parsons_blocks（保底也支援 solution_blocks）
  const blocks =
    previewData.value?.parsons_blocks ||
    previewData.value?.solution_blocks ||
    [];

  // order 來源 1：如果後端有給 solution_order（array）
  let order =
    previewData.value?.solution_order ||
    previewData.value?.solution_ids ||
    [];

  // order 來源 2：如果只有 solution_order_text（像 "b1 → b2 → b3 → b4"）
  if (!Array.isArray(order) || order.length === 0) {
    const s = String(previewData.value?.solution_order_text || "").trim();

    // 同時支援 "→" 或 "->"
    order = s
      .split(/→|->/g)
      .map(x => x.trim())
      .filter(Boolean);
  }

  const map = new Map(blocks.map(b => [String(b.id), b]));

  return (order || []).map((id, idx) => {
    const b = map.get(String(id));
    return {
      idx: idx + 1,
      id: String(id),
      text: b?.text || "（找不到對應區塊）",
      meaning_zh: b?.meaning_zh || "",
    };
  });
});


// review in modal
const reviewTagOptions = [
  "題幹過長",
  "中文語意提示不清楚",
  "干擾選項不清楚",
  "題目難度過高",
  "其他"
];
const reviewTags = ref([]);
const reviewNote = ref("");
const dKeep = reactive({}); // {block_id: true/false}

// ===== computed =====
const selectedVideoTitle = computed(() => {
  const v = videos.value.find(x => x.id === selectedVideo.value);
  return v?.title || "—";
});

const modalPrompt = computed(() => {
  const q = modal.data?.question || {};
  return q.prompt || q.title || q.text || "（未提供題目敘述）";
});

const solutionBlocks = computed(() => {
  const arr = modal.data?.solution_blocks || [];
  return arr.map((b, idx) => ({
    id: String(b.id ?? b._id ?? `s${idx}`),
    _key: `s-${idx}-${b.id ?? ""}`,
    code: b.code || b.text || b.line || "",
    semantic_zh: b.semantic_zh || b.semantic || b.zh || ""
  }));
});

const distractorBlocks = computed(() => {
  const arr = modal.data?.distractor_blocks || [];
  return arr.map((b, idx) => ({
    id: String(b.id ?? b._id ?? `d${idx}`),
    _key: `d-${idx}-${b.id ?? ""}`,
    code: b.code || b.text || b.line || "",
    semantic_zh: b.semantic_zh || b.semantic || b.zh || ""
  }));
});

const answerText = computed(() => {
  const so = modal.data?.solution_order;
  if (Array.isArray(so)) return so.join(" → ");
  if (typeof so === "string") return so;
  // fallback: 用區塊 code 拼成一行
  return solutionBlocks.value.map(b => b.code).join(" → ");
});

// ===== navigation =====
function goVideoManage() {
  router.push({ name: "TeacherVideoManage" });
}
function goSubtitleCheck() {
  router.push({ name: "TeacherSubtitles" });
}

// ===== helpers =====
function fmtTime(iso) {
  if (!iso) return "—";
  // 簡單顯示，避免時區問題
  return iso.replace("T", " ").slice(0, 16);
}
function dotClass(status) {
  return {
    pending: "dot-yellow",
    published: "dot-green",
    rejected: "dot-red"
  }[status] || "dot-yellow";
}

function closeModal() {
  modal.open = false;
  modal.loading = false;
  modal.err = "";
  modal.data = null;
  reviewTags.value = [];
  reviewNote.value = "";
  Object.keys(dKeep).forEach(k => delete dKeep[k]);
}

// ===== API =====
async function fetchUnits() {
  err.a = "";
  loading.units = true;
  try {
    const { data } = await t5Get("/units");
    units.value = data.items || [];
  } catch {
    err.a = "讀取單元失敗，請稍後再試。";
  } finally {
    loading.units = false;
  }
}

async function fetchVideos(unitId) {
  err.a = "";
  loading.videos = true;
  videos.value = [];
  selectedVideo.value = "";
  try {
    const { data } = await t5Get("/videos", { params: { unit_id: unitId } });
    videos.value = data.items || [];
  } catch {
    err.a = "讀取影片列表失敗。";
  } finally {
    loading.videos = false;
  }
}

async function fetchVideoInfo(videoId) {
  err.a = "";
  loading.videoInfo = true;
  try {
    const { data } = await t5Get("/video_info", { params: { video_id: videoId } });
    Object.assign(videoInfo, data);

    if (videoInfo.subtitle_versions?.length) {
      selectedSubtitleVersion.value = videoInfo.subtitle_versions[0];
    } else {
      selectedSubtitleVersion.value = "";
    }
  } catch {
    err.a = "讀取影片資訊失敗。";
  } finally {
    loading.videoInfo = false;
  }
}

async function fetchQuestions() {
  err.d = "";
  if (!selectedVideo.value) return;
  loading.questions = true;
  try {
    const { data } = await t5Get("/questions", {
      params: {
        video_id: selectedVideo.value,
        status: filterStatus.value,
        sort: sortOrder.value
      }
    });
    questions.value = data.items || [];
  } catch {
    err.d = "伺服器連線失敗";
  } finally {
    loading.questions = false;
  }
}

async function regenerate() {
  err.e = "";
  if (!selectedVideo.value) return;
  busy.regen = true;
  try {
    await t5Post("/regenerate", {
      video_id: selectedVideo.value,
      level: "L1", // 不做適性化：固定
      subtitle_version: selectedSubtitleVersion.value || null
    });
    await fetchQuestions();
  } catch {
    err.e = "重新生成失敗，請稍後再試。";
  } finally {
    busy.regen = false;
  }
}

async function saveReviewOnly() {
  if (!modal.data?.task_id) return;
  const payload = {
    task_id: modal.data.task_id,
    review_tags: reviewTags.value,
    review_note: reviewNote.value,
    distractor_keep: { ...dKeep }
  };
  await t5Post("/question/review_save", payload);
}

async function publish(row) {
  await t5Post("/question/publish", { task_id: row.task_id });
  await fetchQuestions();
}

async function unpublish(row) {
  await t5Post("/question/unpublish", { task_id: row.task_id });
  await fetchQuestions();
}

async function publishFromModal() {
  if (!modal.data?.task_id) return;
  await saveReviewOnly();
  await t5Post("/question/publish", { task_id: modal.data.task_id });
  await fetchQuestions();
  closeModal();
}

async function regenFromModal() {
  await saveReviewOnly();
  await regenerate();
  closeModal();
}

async function rejectFromModal() {
  if (!modal.data?.task_id) return;
  await t5Post("/question/reject", {
    task_id: modal.data.task_id,
    review_tags: reviewTags.value,
    review_note: reviewNote.value
  });
  await fetchQuestions();
  closeModal();
}

// 干擾區塊切換移除/保留
/** 初始化：預覽載入成功後，把所有 distractor 預設設為 true */
function initDistractorKeep(distractors = []) {
  // 只初始化「尚未存在」的，避免老師切過後又被覆蓋
  for (const b of distractors) {
    const id = String(b.id ?? b._id ?? "");
    if (!id) continue;
    if (typeof distractorKeep[id] === "undefined") {
      distractorKeep[id] = true; // 預設保留
    }
  }
}

/** 是否保留（預設 true） */
function isKeepDistractor(id) {
  const key = String(id || "");
  return typeof distractorKeep[key] === "undefined" ? true : !!distractorKeep[key];
}

/** 點 ✅：保留 */
function keepDistractor(id) {
  distractorKeep[String(id)] = true;
}

/** 點 ❌：移除 */
function removeDistractor(id) {
  distractorKeep[String(id)] = false;
}

// ===== watchers =====
watch(selectedUnit, async (u) => {
  if (!u) return;
  await fetchVideos(u);
});

watch(selectedVideo, async (v) => {
  if (!v) return;
  await fetchVideoInfo(v);
  await fetchQuestions();
});

watch([filterStatus, sortOrder], async () => {
  if (!selectedVideo.value) return;
  await fetchQuestions();
});

// ===== init =====
onMounted(async () => {
  // [新增] 讀取後測開放狀態
  fetchPostOpen();
  await fetchUnits();
});

const reviewForm = reactive({
  tags: [],
  note: ""
});

async function publishFromPreview() {
  try {
    const taskId = previewData.value?.meta?.task_id;
    if (!taskId) throw new Error("缺少 task_id，無法發布");

    // 1) 先存老師審核（tags/note + 干擾保留移除）
    await t5Post("/question/review_save", {
      task_id: taskId,
      review_tags: reviewForm.tags || [],
      review_note: reviewForm.note || "",
      distractor_keep: { ...distractorKeep }, // ✅ 把 ✅/❌ 狀態送到後端
    });

    // 2) 再發布（學生端可見）
    await t5Post("/question/publish", {
      task_id: taskId,
    });

    // 3) UI 更新：重抓列表 + 重新讀取預覽狀態
    await fetchQuestions();
    await openPreview({ task_id: taskId }); // 重新載入（可選）
    alert("✅ 已發布：學生端現在看得到這題了");
  } catch (e) {
    alert("⚠️ 發布失敗：" + (e?.message || "unknown"));
  }
}

// ai中文語意提示
function enhanceMeaning(codeText, rawMeaning) {
  const t = (codeText || "").trim();

  // 先用你原本的 rawMeaning 當 fallback
  const base = rawMeaning || "（未提供）";

  // 針對常見模式做教學版補強
  if (/^total\s*=\s*0$/.test(t)) {
    return "建立變數 total，用來累積加總結果，先把初始值設為 0。";
  }
  if (/^for\s+\w+\s+in\s+range\(\s*1\s*,\s*6\s*\)\s*:\s*$/.test(t)) {
    return "使用迴圈讓 i 依序取值 1 到 5，準備逐一加總（range(1,6) 不包含 6）。";
  }
  if (/^total\s*\+=\s*i$/.test(t)) {
    return "把目前的 i 加到 total 中，逐步累積總和。";
  }
  if (/^print\(\s*total\s*\)$/.test(t)) {
    return "迴圈結束後，輸出最後計算完成的總和結果。";
  }

  // 其他行：維持原本語意
  return base;
}


function returnNotPublish() {
  alert("（示意）已退回：你下一步要接後端 /return，把題目 status=已退回 並存 review tags/note。");
}



// [新增] 切換單元時同步讀取後測開放狀態
watch(selectedUnit, () => {
  fetchPostOpen();
});</script>

<style scoped>
/* ===== Layout ===== */
.t5-layout { display: grid; grid-template-columns: 240px 1fr; min-height: 100vh; background: #f5f6f8; }
.sidebar { background: #d4b34a; padding: 18px 16px; display: flex; flex-direction: column; gap: 16px; }
.profile { display: flex; align-items: center; gap: 10px; }
.avatar { width: 48px; height: 48px; border-radius: 50%; background: #fff; opacity: 0.9; }
.hello { font-weight: 900; }
.menu { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.menu-item { border: none; background: transparent; text-align: left; padding: 10px 10px; border-radius: 10px; cursor: pointer; font-weight: 800; }
.menu-item.active { background: rgba(255,255,255,0.35); }
.logout { margin-top: auto; }
.btn-outline { width: 100%; border: 2px solid #0b2a4a; background: #fff; padding: 10px; border-radius: 10px; font-weight: 900; cursor: pointer; }

/* ===== Main ===== */
.main { padding: 18px 22px; }
.title { margin: 6px 0 16px; text-align: center; font-size: 20px; font-weight: 900; }

/* ===== Cards grid ===== */
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.card { background: #fff; border-radius: 18px; border: 2px solid #1d1d1d; padding: 14px 14px 12px; }
.card-title { margin: 0 0 10px; font-size: 15px; font-weight: 900; }

.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
.field label { display: block; font-size: 12px; font-weight: 900; margin-bottom: 6px; }
.field select { width: 100%; padding: 8px; border-radius: 10px; border: 1px solid #d0d0d0; background: #fff; }
.info .kv { font-size: 13px; }
.k { font-weight: 900; }
.actions { display: flex; gap: 10px; justify-content: flex-end; align-items: center; }

.kvcol { display: grid; gap: 10px; font-size: 13px; }

/* ===== chips ===== */
.chip { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 900; margin-right: 6px; }
.chip-ok { background: #e7f6ee; color: #1b6b3a; border: 1px solid #a7e0bd; }
.chip-off { background: #f2f2f2; color: #777; border: 1px solid #ddd; }

/* ===== flow ===== */
.flow { display: flex; align-items: center; justify-content: center; gap: 10px; margin-top: 4px; }
.step { display: grid; justify-items: center; gap: 6px; }
.num { width: 22px; height: 22px; border-radius: 50%; background: #dfe9ff; display: grid; place-items: center; font-weight: 900; }
.box { width: 130px; height: 78px; border-radius: 14px; background: #f7e1b2; display: grid; place-items: center; border: 1px solid #e3c07c; }
.icon { font-size: 20px; }
.txt { font-weight: 900; }
.arrow { font-weight: 900; color: #777; }
.meta { margin-top: 10px; display: grid; gap: 6px; font-size: 13px; font-weight: 800; }

/* ===== D ===== */
.d-toolbar { display: flex; gap: 14px; align-items: center; margin-bottom: 10px; }
.d-filter { display: flex; gap: 8px; align-items: center; font-weight: 900; font-size: 13px; }
.d-filter select { padding: 8px 10px; border-radius: 10px; border: 1px solid #d0d0d0; background: #fff; }

.d-state { background: #f6f7fb; border: 1px dashed #c9d3e6; border-radius: 12px; padding: 10px; font-weight: 800; color: #445; }
.d-state.err { border-color: #f1a5a5; background: #fff2f2; color: #8b1a1a; }

.table-wrap { margin-top: 10px; border: 1px solid #e2e2e2; border-radius: 12px; overflow: hidden; }
.t { width: 100%; border-collapse: collapse; font-size: 13px; }
.t th, .t td { padding: 10px; border-bottom: 1px solid #eee; vertical-align: middle; }
.t thead th { background: #f3f4f6; font-weight: 900; }
.sub { font-size: 11px; color: #666; margin-top: 4px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }

.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.dot-yellow { background: #e7b84a; }
.dot-green { background: #2fbf71; }
.dot-red { background: #e74c3c; }

.btns { display: flex; gap: 8px; flex-wrap: wrap; }
.pillBtn { border: none; padding: 7px 12px; border-radius: 10px; font-weight: 900; cursor: pointer; }
.pillBtn.preview { background: #6d85a5; color: #fff; }
.pillBtn.publish { background: #f2c266; }
.pillBtn.unpub { background: #ffe0a6; }
.pillBtn.regen { background: #ffd6d6; }

.noteBtn { border: none; background: transparent; cursor: pointer; font-weight: 900; color: #1f3b5b; }
.btn { border: none; background: #f2c266; padding: 10px 14px; border-radius: 10px; font-weight: 900; cursor: pointer; }
.btn.mini { padding: 8px 12px; margin-top: 8px; }
.warn { background: #ffd6d6; }
.hint { margin-top: 8px; font-size: 12px; color: #666; }
.err { color: #b00020; }

/* ===== Modal ===== */
/* ===== Modal 美化（TeacherT5AgentLog.vue）===== */

.modal-mask{
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, .45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  z-index: 9999;
}

.modal{
  width: min(1180px, 96vw);
  height: min(86vh, 920px);
  background: #fff;
  border-radius: 14px;
  border: 1px solid rgba(0,0,0,.08);
  box-shadow: 0 18px 60px rgba(0,0,0,.25);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Header */
.modal-head{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
  background: linear-gradient(180deg, #ffffff, #fbfbfd);
  border-bottom: 1px solid rgba(0,0,0,.08);
}

.modal-title{
  font-weight: 800;
  letter-spacing: .2px;
  font-size: 16px;
  color: #111827;
}

.modal-head .x{
  width: 34px;
  height: 34px;
  border-radius: 10px;
  border: 1px solid rgba(0,0,0,.12);
  background: #fff;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: transform .08s ease, background .12s ease;
}
.modal-head .x:hover{ background: #f3f4f6; }
.modal-head .x:active{ transform: scale(.98); }

/* Body（可滾動） */
.modal-body{
  flex: 1 1 auto;
  overflow: auto;
  padding: 18px;
  background: #ffffff;
}

/* 內容 container */
.pv{
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* Meta pills */
.pvTop{
  background: #fff;
}
.pvMetaRow{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.pill{
  font-size: 13px;
  color: #111827;
  background: #f3f4f6;
  border: 1px solid rgba(0,0,0,.08);
  border-radius: 999px;
  padding: 6px 10px;
}
.pvHr{
  margin: 12px 0 0;
  border: none;
  border-top: 1px dashed rgba(0,0,0,.12);
}

/* 區塊標題 */
.pvSection{
  background: #fff;
  border: 1px solid rgba(0,0,0,.08);
  border-radius: 12px;
  padding: 14px;
}
.pvH{
  font-weight: 800;
  font-size: 14px;
  color: #111827;
  margin-bottom: 10px;
}

/* 題目敘述 box */
.pvBox{
  background: #f8fafc;
  border: 1px solid rgba(0,0,0,.08);
  border-radius: 10px;
  padding: 12px;
  color: #111827;
  line-height: 1.7;
  white-space: pre-wrap;
}

/* Blocks grid */
.pvGrid{
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

/* 每個 block */
.pvBlock{
  border: 1px solid rgba(0,0,0,.08);
  border-radius: 12px;
  background: #ffffff;
  padding: 12px;
}
.pvBlock .code{
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 13px;
  background: #0b1220;
  color: #e5e7eb;
  padding: 10px 12px;
  border-radius: 10px;
  white-space: pre-wrap;
  line-height: 1.6;
  overflow-x: auto;
}
.pvBlock .zh{
  margin-top: 8px;
  color: #374151;
  font-size: 13px;
  line-height: 1.65;
  background: #f8fafc;
  border: 1px solid rgba(0,0,0,.06);
  border-radius: 10px;
  padding: 10px 12px;
}

/* Empty state */
.pvEmpty{
  border-radius: 12px;
  padding: 12px;
  background: #fff7ed;
  border: 1px solid rgba(245, 158, 11, .35);
  color: #9a3412;
  font-size: 13px;
  line-height: 1.6;
}

/* 正確答案順序 */
.pvOrder{
  background: #f8fafc;
  border: 1px dashed rgba(0,0,0,.18);
  border-radius: 10px;
  padding: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 13px;
  color: #111827;
  white-space: pre-wrap;
}
.pvSmall{
  margin-top: 8px;
  font-size: 12px;
  color: #6b7280;
}

/* Checkboxes */
.pvChecks{
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  padding: 6px 0 0;
}
.pvChecks label{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #111827;
}
.pvChecks input[type="checkbox"]{
  width: 16px;
  height: 16px;
}

/* Note textarea */
.pvNote{
  width: 100%;
  min-height: 110px;
  resize: vertical;
  border-radius: 12px;
  border: 1px solid rgba(0,0,0,.12);
  background: #fff;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.6;
  outline: none;
}
.pvNote:focus{
  border-color: rgba(59,130,246,.55);
  box-shadow: 0 0 0 4px rgba(59,130,246,.12);
}

/* ✅ 讓按鈕列更像 footer（即使你還沒搬到 modal-foot，也會好看） */
.pvActions{
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
  padding-top: 6px;
}

/* Buttons */
.btn{
  border: 1px solid rgba(0,0,0,.14);
  background: #fff;
  color: #111827;
  border-radius: 12px;
  padding: 10px 14px;
  cursor: pointer;
  font-weight: 700;
  font-size: 13px;
  transition: transform .08s ease, background .12s ease, box-shadow .12s ease;
}
.btn:hover{
  background: #f3f4f6;
}
.btn:active{ transform: scale(.99); }
.btn:disabled{
  opacity: .55;
  cursor: not-allowed;
}

.btn.primary{
  background: #111827;
  color: #fff;
  border-color: rgba(17,24,39,.9);
}
.btn.primary:hover{ background: #0b1220; }

.btn.warn{
  background: #fff1f2;
  border-color: rgba(244,63,94,.35);
  color: #9f1239;
}
.btn.warn:hover{ background: #ffe4e6; }

/* 老師解答區 */
.pvOrderList{
  margin-top: 10px;
  border: 1px dashed rgba(0,0,0,.12);
  border-radius: 12px;
  padding: 10px 12px;
  background: #fafafa;
}

.pvOrderItem{
  display: flex;
  gap: 10px;
  padding: 8px 6px;
  border-bottom: 1px solid rgba(0,0,0,.06);
}
.pvOrderItem:last-child{ border-bottom: none; }

.pvOrderIdx{
  width: 28px;
  font-weight: 700;
  opacity: .7;
}

.pvOrderCode{
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 13px;
}

.pvOrderId{
  display: inline-block;
  font-weight: 800;
  margin-right: 6px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef2ff;
  border: 1px solid rgba(99,102,241,.25);
}

.pvOrderZh{
  margin-top: 4px;
  font-size: 12px;
  opacity: .8;
}




/* 手機適配 */
@media (max-width: 720px){
  .modal{ width: 96vw; height: 90vh; }
  .modal-body{ padding: 14px; }
  .pvSection{ padding: 12px; }
  .pvActions{ justify-content: stretch; }
  .btn{ flex: 1 1 auto; }
}

/* 干擾提 */
/* 干擾卡：右上控制 */
.pvBlockD{
  position: relative;
}

.dCtrl{
  position: absolute;
  top: 10px;
  right: 10px;
  display: inline-flex;
  gap: 8px;
  z-index: 2;
}

.dBtn{
  width: 34px;
  height: 34px;
  border-radius: 10px;
  border: 1px solid rgba(0,0,0,.12);
  background: #fff;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: transform .08s ease, background .12s ease, box-shadow .12s ease, opacity .12s ease;
}

.dBtn:hover{ background: #f3f4f6; }
.dBtn:active{ transform: scale(.98); }

.dBtn.active{
  box-shadow: 0 0 0 4px rgba(59,130,246,.12);
  border-color: rgba(59,130,246,.45);
}

/* 移除狀態：卡片淡掉 */
.pvBlock.removed{
  opacity: .45;
  filter: grayscale(.4);
  background: #fff1f2;               /* 淡紅底 */
  border-color: rgba(220,38,38,.35);
}

.pvBlock.removed .code{
  text-decoration: line-through;
  opacity: .8;
}

/* 標籤 */
.removedTag{
  display: inline-block;
  margin-left: 8px;
  font-size: 12px;
  font-weight: 700;
  color: #9f1239;
  background: #ffe4e6;
  border: 1px solid rgba(244,63,94,.35);
  padding: 3px 10px;
  border-radius: 999px;
}


/* ===== Preview Modal UI ===== */
.pv { padding: 6px 2px; }
.pvTop .pvMetaRow { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
.pill { background:#f2f2f2; border:1px solid #e0e0e0; padding:6px 10px; border-radius:999px; font-size:12px; }
.pvHr { border:0; border-top:1px solid #e6e6e6; margin:12px 0; }

.pvSection { margin: 12px 0; }
.pvH { font-weight: 800; margin-bottom: 8px; }
.pvBox { background:#f6f8ff; border:1px solid #dde3ff; padding:12px; border-radius:10px; }
.pvGrid { display:grid; grid-template-columns: 1fr 1fr; gap:12px; }
.pvBlock { background:#fff; border:1px solid #e8e8e8; border-radius:12px; padding:12px; }
.pvBlock .code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-weight:700; }
.pvBlock .zh { margin-top:8px; color:#444; background:#fff7d6; border:1px solid #ffe29a; padding:8px 10px; border-radius:10px; }

.pvEmpty { grid-column: 1 / -1; background:#fff3f3; border:1px solid #ffd2d2; padding:12px; border-radius:10px; color:#8a2f2f; }

.pvOrder { border:1px dashed #cfcfcf; border-radius:12px; padding:12px; background:#fafafa; }
.pvSmall { margin-top:8px; font-size:12px; color:#666; }

.pvChecks { display:flex; flex-wrap:wrap; gap:16px; }
.pvNote { width:100%; min-height:90px; border-radius:12px; border:1px solid #e0e0e0; padding:12px; outline:none; }

.pvActions { display:flex; justify-content:center; gap:12px; margin-top: 14px; flex-wrap:wrap; }


/* [新增] 後測發布/取消發布按鈕區（最小樣式，不影響既有排版） */
.posttest-bar{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  margin: 8px 0 16px;
  padding: 10px 12px;
  border: 1px solid #e6e6e6;
  border-radius: 10px;
  background: #fff;
}
.posttest-left{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
  font-size:14px;
}
.posttest-status{
  font-weight:700;
}
.posttest-status.open{ color:#2e7d32; }
.posttest-status.closed{ color:#c62828; }
.posttest-hint{ color:#888; font-size:12px; }
.posttest-actions{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
}

</style>