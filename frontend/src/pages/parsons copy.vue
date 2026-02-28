<template>  
  <div class="page">
    <!-- 題目 -->
    <div class="qbox">
      <div class="qtitle">題目</div>
      <div class="qtext">{{ task?.question_text || (state.loading ? "載入中..." : "（無題目）") }}</div>
    </div>

    <div class="board">
      <!-- 左：挖空作答區 -->
      <div class="panel">
        <div class="panel-title">題目作答程式區：</div>

        <div v-if="state.noTask" class="emptyBox">
          {{ state.err || "此影片尚未發布題目" }}
        </div>

        <div v-else class="cloze">
          <div
            v-for="(slot, idx) in templateSlots"
            :key="slot.slot"
            class="cloze-row"
            :class="{ wrong: wrongIndices.includes(idx) }"
          >
            <div class="hint">
              {{ idx + 1 }}. {{ filled[slot.slot]?.meaning_zh || slot.label || "" }}
            </div>

            <div
              class="blank"
              :class="{ filled: !!filled[slot.slot], over: overSlot === slot.slot }"
              @dragover.prevent="onDragOver(slot.slot)"
              @dragleave="onDragLeave"
              @drop.prevent="onDrop(slot.slot)"
            >
              <span v-if="filled[slot.slot]">
                {{ filled[slot.slot].text }}
              </span>
              <span v-else class="placeholder">（把右邊片段拖到這裡）</span>

              <button
                v-if="filled[slot.slot]"
                class="remove"
                @click="removeFromSlot(slot.slot)"
                title="移除"
              >
                ×
              </button>

            </div>
          </div>
        </div>
      </div>

      <!-- 右：片段池 -->
      <div class="panel">
        <div class="panel-title">程式片段：</div>

        <div v-if="state.noTask" class="emptyBox">尚未載入片段</div>

        <div v-else class="pool">
          <div
            v-for="b in poolBlocks"
            :key="b.id"
            class="pill"
            :class="[b.type, { used: isUsed(b.id) }]"
            :draggable="!isUsed(b.id)"
            @dragstart="onDragStart(b)"
            @dragend="onDragEnd"
          >
            {{ b.text }}
          </div>
        </div>
      </div>
    </div>

    <!-- 按鈕 -->
    <div class="actions">
      <button class="btn submit" @click="submit" :disabled="state.submitting || state.noTask">
        {{ state.submitting ? "送出中..." : "送出" }}
      </button>
    </div>

    <!-- 回饋 -->
    <div v-if="result?.feedback" class="result" :class="{ ok: result?.is_correct }">
      {{ result.feedback }}
    </div>

    <div v-if="state.err && !state.noTask" class="result">
      {{ state.err }}
    </div>
  </div>
</template>

<script setup>
/**
 * 這支 script 主要做 5 件事：
 * 1) 從後端載入「已發布」的 parsons 題目與片段池
 * 2) 讓學生拖曳片段到左邊的格子（filled）
 * 3) 送出作答到 /api/parsons/submit，拿到對錯與錯誤位置
 * 4) 若答錯：跳出 confirm 問「要不要回 learning 看影片片段？」
 * 5) 若要回看：先把目前作答狀態存到 localStorage，回來能繼續做
 */

import { ref, reactive, computed, onMounted, watch, nextTick, onBeforeUnmount } from "vue"; // [新增]
import { useRoute, useRouter } from "vue-router";

const API_BASE = "http://127.0.0.1:5000";

const route = useRoute();
const router = useRouter();

/** ✅【新增】兼容不同 router param 命名，避免 videoId 取不到導致白畫面 */
const videoId = computed(() => {
  return String(route.params.videoId || route.params.id || route.params.video_id || "");
});

/** ✅【新增】從 learning 回來的回看 attempt id（用於 submit 時更新 followup） */
const review_attempt_id = computed(() => {
  // // [新增]
  const a = route.query.review_attempt_id ? String(route.query.review_attempt_id) : "";
  const b = route.query.attempt_id ? String(route.query.attempt_id) : "";
  return a || b;
});

// ====== 後端載入的題目資料 ======
const task = ref(null);
const poolBlocks = ref([]);
const templateSlots = ref([]);

// ====== 拖曳狀態 ======
const dragging = ref(null);   // { block, fromSlotKey }
const overSlot = ref(null);   // slotKey（字串）
const filled = reactive({});  // ✅【修正】key 統一用 slot.slot（字串） => filled[slotKey] = block

// [新增] V1.4：固定顯示中文提示（slot.label），避免拖進去的 block.meaning_zh 覆蓋 slot.label
function normalizeFilledBlock(block) {
  if (!block) return null;
  return { ...block, meaning_zh: "" };
}

// ==============================
// ✅ [新增] V1.4：Tab / Space / Backspace 控制「框」縮排
//   - Tab：退四格（INDENT_STEP_PX）
//   - Space：退一格（INDENT_UNIT_PX）
//   - Backspace：回退一格（INDENT_UNIT_PX）
//   - 上限：最多 8 格（INDENT_MAX_PX = INDENT_STEP_PX * 2）
// ==============================
const activeSlotKey = ref(null); // [新增]

// ⚠️ 不改既有變數名稱：slotIndentLevel 保留，但內部改存「px」
// slotKey -> px
const slotIndentLevel = reactive({}); // [修改]

let _blankCleanupFns = [];     // [新增]
let _docKeydownHandler = null; // [新增]

const INDENT_STEP_PX = 24; // Tab：退四格（你可自行調整）
const INDENT_UNIT_PX = Math.max(1, Math.round(INDENT_STEP_PX / 4)); // [新增] Space/Backspace：退一格
const INDENT_MAX_PX = INDENT_STEP_PX * 2; // [新增] 最多退 2 次（8 格）
// 保留舊常數（不破壞既有邏輯/相容性）
const INDENT_MAX_LEVEL = 2; // [保留]

function setActiveSlotKey(k) { // [新增]
  if (k == null) return;
  activeSlotKey.value = String(k);
}

function clampIndentPx(px) { // [新增]
  const n = Number(px || 0);
  return Math.max(0, Math.min(INDENT_MAX_PX, n));
}

async function applyIndentStylesToDOM() { // [修改]
  await nextTick();
  const blanks = Array.from(document.querySelectorAll(".cloze .blank"));
  const slots = templateSlots.value || [];

  blanks.forEach((el, idx) => {
    const slotKey = slots[idx]?.slot != null ? String(slots[idx].slot) : null;
    const pxRaw = slotKey != null ? Number(slotIndentLevel[slotKey] || 0) : 0; // [修改]
    const px = clampIndentPx(pxRaw); // [修改]

    el.style.marginLeft = px ? `${px}px` : "";
    el.style.width = px ? `calc(100% - ${px}px)` : "";
  });
}

function indentBoxPx(slotKey, deltaPx) { // [新增]
  const k = slotKey != null ? String(slotKey) : null;
  if (!k) return false;

  const cur = clampIndentPx(slotIndentLevel[k]); // [修改]
  const next = clampIndentPx(cur + Number(deltaPx || 0)); // [新增]
  if (next === cur) return false;

  slotIndentLevel[k] = next; // [修改]
  return true;
}

function pickTargetSlotKey() { // [新增]
  const a = activeSlotKey.value != null ? String(activeSlotKey.value) : "";
  if (a) return a;

  const slots = templateSlots.value || [];
  if (slots[0]?.slot != null) return String(slots[0].slot);
  return null;
}

async function bindBlankFocusHandlers() { // [新增]
  // 清掉舊綁定
  try {
    (_blankCleanupFns || []).forEach((fn) => {
      try { fn(); } catch (_) {}
    });
  } catch (_) {}
  _blankCleanupFns = [];

  await nextTick();

  const blanks = Array.from(document.querySelectorAll(".cloze .blank"));
  if (!blanks.length) return;

  blanks.forEach((el, idx) => {
    el.setAttribute("tabindex", "0"); // 讓框可 focus

    const onFocus = () => {
      const slot = (templateSlots.value || [])[idx];
      if (slot?.slot != null) setActiveSlotKey(slot.slot);
    };

    el.addEventListener("focus", onFocus, true);

    _blankCleanupFns.push(() => {
      el.removeEventListener("focus", onFocus, true);
      try { el.removeAttribute("tabindex"); } catch (_) {}
      el.style.marginLeft = "";
      el.style.width = "";
    });
  });

  await applyIndentStylesToDOM();
}

// ====== 作答結果 ======
const result = ref(null);
const wrongIndices = ref([]);

// ====== UI 狀態 ======
const state = reactive({
  loading: false,
  submitting: false,
  noTask: false,
  err: "",
});

// ========= localStorage 暫存 =========
const PRACTICE_STATE_KEY = "parsons_practice_state_v1";
const RESTORE_ONCE_KEY = "parsons_restore_once_v1";
const RESTORE_MSG_KEY = "parsons_restore_msg_v1";

/** ✅【修正】清空作答（filled 的 key 是 slotKey，不是 idx） */
function resetFilled() {
  for (const k of Object.keys(filled)) delete filled[k];
  wrongIndices.value = [];
  result.value = null;
  state.err = "";

  // [新增] 清掉縮排狀態
  for (const k of Object.keys(slotIndentLevel)) delete slotIndentLevel[k]; // [新增]
  activeSlotKey.value = null; // [新增]
}

/** ✅【修正】右側某個 block 是否已被使用 */
function isUsed(blockId) {
  return Object.values(filled).some((b) => String(b?.id) === String(blockId));
}

/** ✅【新增】找出某個 block 目前被放在哪一格（用於避免同一塊出現兩次） */
function findSlotKeyByBlockId(blockId) { 
  const id = String(blockId);
  for (const [k, v] of Object.entries(filled)) {
    if (String(v?.id) === id) return k;
  }
  return null;
}

// ====== 拖曳事件 ======
function onDragStart(block) {
  const fromSlotKey = findSlotKeyByBlockId(block?.id);
  dragging.value = { block, fromSlotKey };
}
function onDragEnd() {
  dragging.value = null;
  overSlot.value = null;
}
function onDragOver(slotKey) {
  overSlot.value = slotKey;
}
function onDragLeave() {
  overSlot.value = null;
}
function onDrop(slotKey) {
  overSlot.value = null;
  const payload = dragging.value;
  if (!payload?.block) return;

  // findSlotKeyByBlockId：如果這塊已經在某格了，先清掉（避免同一塊出現兩次）
  // 判斷：「如果它已經在格子裡，且那個位置不是我現在正要放進去的位置。
  const existedKey = findSlotKeyByBlockId(payload.block.id);
  if (existedKey != null && existedKey !== slotKey) {
    delete filled[existedKey];  // 舊位置的紀錄刪除
  }

  // 把積木放進新家（B）時，就把舊家（A）的紀錄清空。
  if (payload.fromSlotKey != null && payload.fromSlotKey !== slotKey) {
    delete filled[payload.fromSlotKey];
  }

  filled[slotKey] = normalizeFilledBlock(payload.block);
  setActiveSlotKey(slotKey); // [新增]
  dragging.value = null;
}

/** ✅【新增】點 X 移除 */
function removeFromSlot(slotKey) {
  if (slotKey == null) return;
  delete filled[String(slotKey)];

  const idx = templateSlots.value.findIndex(s => String(s.slot) === String(slotKey));
  if (idx >= 0) {
    wrongIndices.value = (wrongIndices.value || []).filter(i => i !== idx);
  }

  // [新增] 移除該格縮排（框回到原位）
  delete slotIndentLevel[String(slotKey)]; // [新增]
  if (String(activeSlotKey.value || "") === String(slotKey)) activeSlotKey.value = null; // [新增]
  applyIndentStylesToDOM(); // [新增]
}

/** ✅【修正】answer_ids 必須依 templateSlots 的順序，用 slot.slot 取值 */
const answer_ids = computed(() => {
  return (templateSlots.value || []).map((s) => filled[String(s.slot)]?.id || null);
});

// ====== 秒數轉 mm:ss ======
function fmtTime(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

// 設定「單一時間點」或「時間區段」的回看提示文字，fmtTime轉成【分:秒】
function fmtReview(r) {
  if (r?.review_t != null && r?.review_t !== "") {
    return `${fmtTime(r.review_t)}（${Number(r.review_t)} 秒）`;
  }
  if (r?.jump && (r.jump.start != null || r.jump.end != null)) {
    const s = r.jump.start ?? 0;
    const e = r.jump.end ?? 0;
    return `${fmtTime(s)}–${fmtTime(e)}（${Number(s)}–${Number(e)} 秒）`;
  }
  return "（未提供）";
}

function buildWrongFeedback(r) {
  const slotLabel = r?.slot_label ? String(r.slot_label) : "（未提供格數）";
  const actual = (r?.actual_text != null && String(r.actual_text).trim() !== "")
    ? String(r.actual_text)
    : "（空白）";
  const hint = r?.hint ? String(r.hint) : "（未提供）";
  const review = fmtReview(r);
  // slotLabel（錯在哪）
  // actual（你放了什麼）
  // hint（提示）
  // review（建議回看時間）
  return (
    `❌ 你錯在：${slotLabel}\n` + 
    `你原先放的是：${actual}\n` +
    `建議提示：${hint}\n` +
    `建議回看時間軸：${review}`
  );
}

// 身分證 (attempt_id) 以及學生的選擇 (choice)。
// fetch 傳送到後端 API 路由 /api/parsons/review_choice。
async function sendChoice(attempt_id, choice) {
  if (!attempt_id) return;
  try {
    await fetch(`${API_BASE}/api/parsons/review_choice`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        attempt_id,
        student_id: (localStorage.getItem("student_id") || ""), // 或你目前存的 key
        student_choice: choice
      }),
    });
  } catch (_) {}
}
// 儲存學生的作答狀態到 localStorage，extra 可額外帶入 attempt_id 或其他資訊，讓回來後能繼續做或顯示提示訊息
// filled 記錄作答內容
// wrong_indices 記錄錯誤位置（讓回來後能高亮錯誤格子）

function savePracticeState(extra = {}) {
  try {
    const stateObj = {
      videoId: String(videoId.value || ""),
      level: String(route.query.level || "L1"),
      task_id: task.value?.task_id || task.value?._id || "",
      filled_map: (templateSlots.value || []).map((s) => filled[String(s.slot)]?.id || null),
      wrong_indices: Array.isArray(wrongIndices.value) ? wrongIndices.value : [],
      saved_at: Date.now(),
      ...extra,
    };
    localStorage.setItem(PRACTICE_STATE_KEY, JSON.stringify(stateObj));
  } catch (_) {}
}

function restorePracticeStateIfMatch(loadedTask) {
  try {
    const raw = localStorage.getItem(PRACTICE_STATE_KEY);
    if (!raw) return false;
    const st = JSON.parse(raw);

    const loadedTaskId = loadedTask?.task_id || loadedTask?._id || "";
    if (!loadedTaskId) return false;

    if (String(st.videoId) !== String(videoId.value)) return false;
    if (String(st.task_id) !== String(loadedTaskId)) return false;

    const byId = new Map((poolBlocks.value || []).map(b => [String(b.id), b]));

    for (const k of Object.keys(filled)) delete filled[k];

    const slots = templateSlots.value || [];
    (st.filled_map || []).forEach((bid, i) => {
      if (!bid) return;
      const b = byId.get(String(bid));
      const slotKey = slots[i]?.slot;
      if (b && slotKey != null) filled[String(slotKey)] = normalizeFilledBlock(b);
    });

    wrongIndices.value = Array.isArray(st.wrong_indices) ? st.wrong_indices : [];

    localStorage.setItem(RESTORE_ONCE_KEY, "1");
    localStorage.setItem(RESTORE_MSG_KEY, "已恢復上一題作答");
    return true;
  } catch (_) {
    return false;
  }
}

// ====== 送出作答 ======
async function submit() {
  state.submitting = true;
  state.err = "";
  result.value = null;
  wrongIndices.value = [];

  try {
    const task_id = task.value?.task_id || task.value?._id;
    if (!task_id) {
      state.err = "尚未載入題目，無法送出。";
      result.value = { is_correct: false, feedback: state.err };
      return;
    }

    // ✅【重要】V1.7: 從 localStorage 取得 student_id
    const student_id = localStorage.getItem("student_id") || "";

    const body = {
      task_id,
      answer_ids: answer_ids.value,
      video_id: String(videoId.value || ""),
      level: route.query.level ? String(route.query.level) : "L1",
      review_attempt_id: review_attempt_id.value,
      student_id: student_id,  // ✅ 新增：發送學號
    };

    const res = await fetch(`${API_BASE}/api/parsons/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const r = await res.json();

    if (r?.ok && r?.is_correct === false) {
      r.feedback = buildWrongFeedback(r);
    }

    result.value = r;

    wrongIndices.value = Array.isArray(r?.wrong_indices)
      ? r.wrong_indices
      : (r?.wrong_index != null ? [r.wrong_index] : []);

    if (r?.ok && r?.is_correct === true) {
      localStorage.removeItem(PRACTICE_STATE_KEY);
      localStorage.removeItem(RESTORE_ONCE_KEY);
      localStorage.removeItem(RESTORE_MSG_KEY);
    }

    if (r?.ok && r?.is_correct === false && r?.jump?.video_id) {
      const start = r.jump.start ?? 0;
      const end = r.jump.end ?? 0;

      window.alert(
        (r?.feedback || buildWrongFeedback(r)) +
        `\n\n即將帶你回到 learning 複習影片片段：${fmtTime(start)}–${fmtTime(end)}`
      );

      await sendChoice(r?.attempt_id, "yes");
      savePracticeState({ attempt_id: r?.attempt_id || "" });

      router.push({
        path: `/learn/video/${r.jump.video_id}`,
        query: {
          start,
          end,
          attempt_id: String(r.review_attempt_id || r.attempt_id || ""),
          task_id: String(task_id || ""),
          level: route.query.level ? String(route.query.level) : "L1",
        },
      });
    }
  } catch (e) {
    state.err = e?.message || "送出失敗";
    result.value = { is_correct: false, feedback: state.err };
  } finally {
    state.submitting = false;
  }
}


// ==============================
// ✅ [新增] V1.5：相容後端回傳格式（task 可能是扁平或缺欄位）
// 目的：避免 template_slots 缺失導致左側「題目作答程式區」空白
// ==============================
function normalizeTaskPayload(apiJson) { // [新增]
  if (!apiJson) return { taskObj: null, pool: [], slots: [] };

  // 後端可能回：{ ok, task: {...} } 或直接把 task 攤平在最外層
  const t = apiJson.task ? apiJson.task : apiJson; // [新增]
  if (!t) return { taskObj: null, pool: [], slots: [] };

  // --- 題目文字相容 ---
  if (!t.question_text) { // [新增]
    if (t.question && typeof t.question === "object" && t.question.prompt) {
      t.question_text = String(t.question.prompt);
    } else if (t.prompt) {
      t.question_text = String(t.prompt);
    }
  }

  // --- pool 相容（後端可能沒有 pool 欄位） ---
  let pool = Array.isArray(t.pool) ? t.pool : []; // [新增]
  if (!pool.length) { // [新增]
    const core = Array.isArray(t.solution_blocks) ? t.solution_blocks : [];
    const dist = Array.isArray(t.distractor_blocks) ? t.distractor_blocks : [];
    const blocks = Array.isArray(t.blocks) ? t.blocks : [];
    // 優先：solution_blocks + distractor_blocks，其次：blocks
    pool = (core.length || dist.length) ? [...core, ...dist] : blocks;
  }

  // 統一每塊至少有 {id,text,type}
  pool = (pool || []).map((b, i) => ({ // [新增]
    id: b?.id != null ? String(b.id) : `b${i + 1}`,
    text: b?.text != null ? String(b.text) : "",
    type: b?.type ? String(b.type) : (b?.is_distractor ? "distractor" : "core"),
    meaning_zh: b?.meaning_zh != null ? String(b.meaning_zh) : (b?.semantic_zh != null ? String(b.semantic_zh) : ""),
    ...b,
  }));

  // --- template_slots 相容 ---
  let slots = Array.isArray(t.template_slots) ? t.template_slots : []; // [新增]
  if (!slots.length) { // [新增]
    // 以 solution_order 或 solution_blocks 長度推導格數
    const order = Array.isArray(t.solution_order) ? t.solution_order : [];
    const core = Array.isArray(t.solution_blocks) ? t.solution_blocks : [];
    const n = order.length || core.length || 0;

    slots = Array.from({ length: n }, (_, idx) => ({ // [新增]
      slot: `s${idx + 1}`,
      label: `第 ${idx + 1} 格`,
    }));
  } else {
    // 確保 slot / label 存在
    slots = slots.map((s, idx) => ({ // [新增]
      slot: s?.slot != null ? String(s.slot) : `s${idx + 1}`,
      label: s?.label != null ? String(s.label) : `第 ${idx + 1} 格`,
      ...s,
    }));
  }

  // 把補齊後的 slots 放回 taskObj，讓後面流程一致
  t.template_slots = slots; // [新增]

  return { taskObj: t, pool, slots }; // [新增]
}

// ====== 載入題目（學生端只拿「已發布」） ======
async function loadTask() {
  state.loading = true;
  state.err = "";
  state.noTask = false;

  resetFilled();

  try {
    const level = route.query.level ? String(route.query.level) : "L1";
    const url = `${API_BASE}/api/parsons/task?video_id=${encodeURIComponent(String(videoId.value || ""))}&level=${encodeURIComponent(level)}`;
    const res = await fetch(url);

    if (!res.ok) {
      state.noTask = true;
      task.value = null;
      poolBlocks.value = [];
      templateSlots.value = [];
      return;
    }

    const r = await res.json();

    const norm = normalizeTaskPayload(r); // [新增]
    task.value = norm.taskObj;

    poolBlocks.value = norm.pool;
    templateSlots.value = norm.slots;

    const restored = restorePracticeStateIfMatch(task.value); // [修改]

    // ✅【新增】讓 blank 可 focus + 套用縮排
    await bindBlankFocusHandlers(); // [新增]

    if (restored) {
      const once = localStorage.getItem(RESTORE_ONCE_KEY);
      const msg = localStorage.getItem(RESTORE_MSG_KEY) || "已恢復上一題作答";
      if (once === "1") {
        localStorage.setItem(RESTORE_ONCE_KEY, "0");
        window.alert(msg);
      }
    }
  } catch (e) {
    state.err = e?.message || "載入失敗";
  } finally {
    state.loading = false;
  }
}

// ====== 生命週期 ======
onMounted(loadTask);

// ✅ [新增] 全域鍵盤：Tab / Space / Backspace 控制「框」縮排
onMounted(() => { // [新增]
  _docKeydownHandler = (e) => {
    const tag = String(e.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;

    // Tab：退四格
    if (e.key === "Tab") {
      const k = pickTargetSlotKey();
      if (!k) return;
      e.preventDefault();
      const changed = indentBoxPx(k, +INDENT_STEP_PX); // [新增]
      if (changed) applyIndentStylesToDOM();
      return;
    }

    // Space：退一格
    if (e.key === " ") { // [新增]
      const k = pickTargetSlotKey();
      if (!k) return;
      e.preventDefault(); // 避免頁面捲動
      const changed = indentBoxPx(k, +INDENT_UNIT_PX); // [新增]
      if (changed) applyIndentStylesToDOM();
      return;
    }

    // Backspace：回退一格（回到 0 就停止）
    if (e.key === "Backspace") {
      const k = pickTargetSlotKey();
      if (!k) return;

      const cur = clampIndentPx(slotIndentLevel[String(k)] || 0); // [新增]
      if (cur <= 0) return; // 沒縮排就不吃掉 Backspace（避免影響其他行為）

      e.preventDefault();
      const changed = indentBoxPx(k, -INDENT_UNIT_PX); // [新增]
      if (changed) applyIndentStylesToDOM();
      return;
    }
  };

  document.addEventListener("keydown", _docKeydownHandler, true);
});

onBeforeUnmount(() => { // [新增]
  if (_docKeydownHandler) document.removeEventListener("keydown", _docKeydownHandler, true);
  try {
    (_blankCleanupFns || []).forEach((fn) => {
      try { fn(); } catch (_) {}
    });
  } catch (_) {}
  _blankCleanupFns = [];
});

watch(
  () => [videoId.value, route.query.level],
  () => loadTask()
);
</script>

<style scoped>
.page{ padding: 18px; max-width: 1200px; margin: 0 auto; }
.qbox{ border:3px solid #000; border-radius:14px; padding:16px; }
.qtitle{ font-size: 18px; font-weight: 900; margin-bottom: 6px; }
.qtext{ font-size: 18px; font-weight: 800; }

.board{ display:grid; grid-template-columns: 1.2fr 1fr; gap:16px; margin-top:16px; }
.panel{ border:3px solid #000; border-radius:14px; padding:14px; min-height: 460px; min-width: 0; overflow: hidden; }
.panel-title{ font-size:20px; font-weight:900; margin-bottom:10px; }

.emptyBox{
  padding: 12px;
  border-radius: 12px;
  background: #fff3cd;
  font-weight: 900;
}

.cloze{ display:grid; gap:12px; min-width:0; }
.cloze-row{ display:grid; gap:8px; min-width:0; }
.hint{ font-weight: 900; background:#e2c25a; padding:6px 8px; border-radius:6px; }

.blank{
  position: relative;
  border: 2px dashed #999;
  border-radius: 14px;
  padding: 12px 44px 12px 12px;
  background: #fff;
  min-height: 48px;
  display:flex;
  align-items:center;
  box-sizing: border-box;
  max-width: 100%;
  min-width: 0;
}
.blank.over { border-color: #0c5e86; background: #eef9ff; }
.blank.filled{ border-style: solid; border-color: #6b5bff; background: #f1f0ff; }
.placeholder{ color:#777; font-weight: 800; }

.remove{
  position:absolute;
  right:10px; top:50%;
  transform: translateY(-50%);
  width:28px; height:28px;
  border-radius: 999px;
  border:0;
  cursor:pointer;
  font-weight: 900;
}

/* ✅ 錯誤格子高亮 */
.cloze-row.wrong .hint{
  background:#ffb3b3;
}
.cloze-row.wrong .blank{
  border-color:#d61f1f !important;
  box-shadow: 0 0 0 2px rgba(214,31,31,.15);
}

.pool{ display:grid; gap:10px; }
.pill{
  padding:14px 16px;
  border-radius:999px;
  background:#f2f2f2;
  font-weight:800;
  text-align:center;
  cursor:grab;
  user-select:none;
}
.pill.used { opacity: 0.35; cursor: not-allowed; }

.actions{ display:flex; justify-content:center; gap:16px; margin-top:18px; }
.btn{ min-width: 160px; padding: 12px 16px; border-radius:999px; border:0; font-weight:900; cursor:pointer; }
.submit{ background:#4e9b6a; color:#fff; }
.btn:disabled{ opacity:.6; cursor:not-allowed; }

.result{ margin-top:12px; padding: 12px 14px; border-radius: 12px; background:#fff3cd; font-weight:800; }
.result.ok{ background:#d1e7dd; }
</style>
