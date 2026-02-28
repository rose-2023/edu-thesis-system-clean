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

// ==============================
// 新增：測驗計數器（不影響既有練習模式）

const testStartedAt = ref(Date.now()); // 開始時間（毫秒）
const durationSec = ref(0);            // 耗時（秒）

onMounted(() => {
  testStartedAt.value = Date.now();
});

// ==============================
// ✅ [新增] V1.8：前/後測模式參數（不影響既有練習模式）
// ==============================
const isTestMode = computed(() => String(route.query.mode || "") === "test"); // [新增]
const testRole = computed(() => String(route.query.test_role || "pre").toLowerCase()); // [新增]
const testCycleId = computed(() => String(route.query.test_cycle_id || "default")); // [新增]
const studentId = computed(() => String(localStorage.getItem("student_id") || "").trim()); // [新增]

const testMeta = reactive({ // [新增]
  test_task_id: "",
  source_task_id: "",
  started_at_ms: 0,
  total: 1, // [新增]
  current_index: 1, // [新增]
  request_index: 1, // [新增]
});


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

// ==============================
// ✅ [新增] V1.8：測驗模式 - 送出後自動跳下一題（或結束）
// ==============================
// [新增] V1.8：測驗模式 - 送出後自動跳下一題（或結束）
//      請確保全檔只有一個 advanceTestAfterSubmit 的實作
async function advanceTestAfterSubmit() { // // [新增]
  try {
    // 如果後端有提供 total / current_index，則自動跳下一題
    const total = Number(testMeta.total || 1);
    const cur = Number(testMeta.current_index || 1);

    // 如果只有一題或已經是最後一題 -> 結束測驗（導回 home 或完成頁）
    if (total <= 1 || cur >= total) {
      // 結束行為：導回 home（或改成你要的完成頁 route）
      // router.push("/home");
      // [新增] 若你想導到 PreCheck 完成頁，請改成 router.push("/precheck_done")
      try { // [新增]
        if (window?.history?.length > 1) router.back(); // [新增]
        else router.push("/home"); // [新增]
      } catch (_) { // [新增]
        try { // [新增]
          if (window?.history?.length > 1) router.back(); // [新增]
          else router.push("/home"); // [新增]
        } catch (_) { // [新增]
          try { // [新增]
      if (window?.history?.length > 1) router.back(); // [新增]
      else router.push("/home"); // [新增]
    } catch (_) { // [新增]
      router.push("/home"); // [新增]
    } // [新增] // [新增]
        } // [新增] // [新增]
      } // [新增]
      return;
    }

    // 若還有下一題，嘗試從後端取得下一題資訊（若後端支援）
    // 範例：呼叫 test/task 並更新 testMeta、task/pool/templateSlots
    try {
      const url = `${API_BASE}/api/parsons/test/task?student_id=${encodeURIComponent(studentId.value)}&test_role=${encodeURIComponent(testRole.value)}&test_cycle_id=${encodeURIComponent(testCycleId.value)}&next_index=${cur + 1}`;
      const resp = await fetch(url);
      if (resp.ok) {
        const j = await resp.json();
        // 若後端回新的 test_task，更新 meta 與畫面
        testMeta.test_task_id = String(j?.test_task_id || testMeta.test_task_id || "");
        testMeta.source_task_id = String(j?.source_task_id || testMeta.source_task_id || "");
        testMeta.total = j?.total || total;
        testMeta.current_index = j?.current_index || (cur + 1);

        const norm = normalizeTaskPayload(j);
        task.value = norm.taskObj;
        poolBlocks.value = norm.pool;
        templateSlots.value = norm.slots;

        // reset state for next question
        resetFilled();
        wrongIndices.value = [];
        result.value = null;
        state.err = "";
        await bindBlankFocusHandlers();
        return;
      } else {
        // 後端不支援直接拉下一題 → 轉為結束流程
        try { // [新增]
        if (window?.history?.length > 1) router.back(); // [新增]
        else router.push("/home"); // [新增]
      } catch (_) { // [新增]
        try { // [新增]
          if (window?.history?.length > 1) router.back(); // [新增]
          else router.push("/home"); // [新增]
        } catch (_) { // [新增]
          try { // [新增]
      if (window?.history?.length > 1) router.back(); // [新增]
      else router.push("/home"); // [新增]
    } catch (_) { // [新增]
      router.push("/home"); // [新增]
    } // [新增] // [新增]
        } // [新增] // [新增]
      } // [新增]
        return;
      }
    } catch (innerErr) {
      // 取下一題失敗 → 安全回 home
      console.warn("advanceTestAfterSubmit: fetch next failed", innerErr);
      try { // [新增]
        if (window?.history?.length > 1) router.back(); // [新增]
        else router.push("/home"); // [新增]
      } catch (_) { // [新增]
        try { // [新增]
          if (window?.history?.length > 1) router.back(); // [新增]
          else router.push("/home"); // [新增]
        } catch (_) { // [新增]
          try { // [新增]
      if (window?.history?.length > 1) router.back(); // [新增]
      else router.push("/home"); // [新增]
    } catch (_) { // [新增]
      router.push("/home"); // [新增]
    } // [新增] // [新增]
        } // [新增] // [新增]
      } // [新增]
      return;
    }
  } catch (e) {
    console.error("advanceTestAfterSubmit error", e);
    try { // [新增]
      if (window?.history?.length > 1) router.back(); // [新增]
      else router.push("/home"); // [新增]
    } catch (_) { // [新增]
      router.push("/home"); // [新增]
    } // [新增]
  }
} // end advanceTestAfterSubmit

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
    `❌ 你錯在：${slotLabel}
` + 
    `你原先放的是：${actual}
` +
    `建議提示：${hint}
` +
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
    // ==============================
    // ✅ [新增] V1.8：測驗模式（前測/後測）送出
    // - 前測不要顯示中文提示 / 不要顯示「答案不完全正確」訊息
    // - 只要按送出就跳下一題（或結束）
    // ==============================
    if (isTestMode.value) { // [新增]
      if (!studentId.value) { // [新增]
        // 測驗模式：不顯示黃條，直接回上一頁/首頁
        state.err = ""; // [新增]
        result.value = null; // [新增]
        try { // [新增]
          if (window?.history?.length > 1) router.back(); // [新增]
          else router.push("/home"); // [新增]
        } catch (_) { // [新增]
          router.push("/home"); // [新增]
        } // [新增]
        return; // [新增]
      } // [新增]

        const duration_sec = testMeta.started_at_ms
          ? Math.max(0, Math.round((Date.now() - Number(testMeta.started_at_ms)) / 1000))
          : 0;

        // ✅【修正】組送出 body（source_task_id 一定要有值）
      const body = {
        student_id: String(studentId.value || ""),
        test_cycle_id: String(testCycleId.value || ""),
        test_role: String(testRole.value || ""),
        test_task_id: String(testMeta.test_task_id || ""),
        source_task_id: String(testMeta.source_task_id || ""), // ✅ 必須是 parsons_tasks 的 _id

        // ✅【修正】answer_ids 不能是 null
        answer_ids: Array.isArray(answer_ids.value)
          ? answer_ids.value.filter(Boolean)
          : [],

        // ✅【修正】duration_sec 直接用上面算好的數字（不要 durationSec.value）
        duration_sec,
      };

      console.log("poolBlocks sample =", poolBlocks.value?.slice?.(0, 3));
      console.log("answer_ids raw =", answer_ids.value);
      console.log("SUBMIT BODY =", body);
      console.log("API_BASE =", API_BASE);
      const res = await fetch(`${API_BASE}/api/parsons/test/submit`, { // [新增]
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      

      if (res.status === 403) { // [新增]
        window.alert("後測尚未開放，請等待老師開放後再作答。"); // [新增]
        router.push("/home"); // [新增]
        return; // [新增]
      } // [新增]

      const r = await res.json();

      // 已做過就導回（不顯示 ❌ 訊息）
      if (r?.ok && r?.already_submitted) { // [新增]
        window.alert("你已經完成本次測驗，無法重複作答。"); // [新增]
        router.push("/home"); // [新增]
        return; // [新增]
      } // [新增]

      // 測驗模式：不顯示回饋、不高亮錯誤，直接跳下一題
      result.value = null; // [新增]
      wrongIndices.value = []; // [新增]

      if (r?.ok) { // [新增]
        // 測驗模式：不顯示任何黃條訊息，直接前進
        state.err = ""; // [新增]
        result.value = null; // [新增]
        wrongIndices.value = []; // [新增]
        await advanceTestAfterSubmit(); // [新增]
      } else { // [新增]
        // 測驗模式：送出失敗也不顯示黃條，直接回上一頁/首頁
        state.err = ""; // [新增]
        result.value = null; // [新增]
        wrongIndices.value = []; // [新增]
        try { // [新增]
          if (window?.history?.length > 1) router.back(); // [新增]
          else router.push("/home"); // [新增]
        } catch (_) { // [新增]
          router.push("/home"); // [新增]
        } // [新增]
      } // [新增]
      return; // [新增]
    }
    const task_id = task.value?.task_id || task.value?._id;
    if (!task_id) {
      state.err = "尚未載入題目，無法送出。";
      result.value = { is_correct: false, feedback: state.err };
      return;
    }

    const body = {
      task_id,
      answer_ids: answer_ids.value,
      video_id: String(videoId.value || ""),
      level: route.query.level ? String(route.query.level) : "L1",
      review_attempt_id: review_attempt_id.value,
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
        `

即將帶你回到 learning 複習影片片段：${fmtTime(start)}–${fmtTime(end)}`
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
    // ==============================
    // ✅ [新增] V1.8：測驗模式（前測/後測）載入題目
    // ==============================
    if (isTestMode.value) { // [新增]
      if (!studentId.value) { // [新增]
        // 測驗模式：不顯示黃條，直接回上一頁/首頁
        state.noTask = true; // [新增]
        state.err = ""; // [新增]
        task.value = null; // [新增]
        poolBlocks.value = []; // [新增]
        templateSlots.value = []; // [新增]
        try { // [新增]
          if (window?.history?.length > 1) router.back(); // [新增]
          else router.push("/home"); // [新增]
        } catch (_) { // [新增]
          router.push("/home"); // [新增]
        } // [新增]
        return; // [新增]
      } // [新增]

      // [新增] 測驗計時起點（每題進來重置）
      testMeta.started_at_ms = Date.now(); // [新增]

      // [新增] 支援多題：從 query.test_index 讀取想要的題號（後端可忽略）
      const idxQ = route.query.test_index ? Number(route.query.test_index) : null; // [新增]
      testMeta.request_index = Number.isFinite(idxQ) && idxQ > 0 ? idxQ : Number(testMeta.request_index || 1); // [新增]

      const url = `${API_BASE}/api/parsons/test/task?student_id=${encodeURIComponent(studentId.value)}&test_role=${encodeURIComponent(testRole.value)}&test_cycle_id=${encodeURIComponent(testCycleId.value)}&index=${encodeURIComponent(String(testMeta.request_index))}`; // [新增]
      const res = await fetch(url); // [新增]

      if (!res.ok) { // [新增]
        state.noTask = true; // [新增]
        task.value = null; // [新增]
        poolBlocks.value = []; // [新增]
        templateSlots.value = []; // [新增]
        try { // [新增]
          const rr = await res.json(); // [新增]
          state.err = rr?.message || "測驗題目載入失敗"; // [新增]
        } catch (_) { // [新增]
          state.err = "測驗題目載入失敗"; // [新增]
        } // [新增]
        return; // [新增]
      } // [新增]

      const r = await res.json(); // [新增]

      // [新增] 讀取題序資訊（後端若提供）
      testMeta.total = Number(r?.total || 1); // [新增]
      testMeta.current_index = Number(r?.current_index || testMeta.request_index || 1); // [新增]

      // [新增] 記錄 test_task_id / source_task_id
      testMeta.test_task_id = String(r?.test_task_id || "");     // ✅ 只吃 test_task_id
      testMeta.source_task_id = String(r?.source_task_id || ""); // ✅ 只吃 source_task_id

      const norm = normalizeTaskPayload(r); // [新增]
      task.value = norm.taskObj; // [新增]
      poolBlocks.value = norm.pool; // [新增]

      // [新增] ✅前測不顯示中文提示：清空每格 label（template 不改）
      templateSlots.value = (norm.slots || []).map((s) => ({ ...s, label: "" })); // [新增]

      // ✅【新增】讓 blank 可 focus + 套用縮排（沿用既有機制）
      await bindBlankFocusHandlers(); // [新增]
      return; // [新增]
    }
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
  () => [videoId.value, route.query.level, route.query.mode, route.query.test_role, route.query.test_cycle_id, route.query.test_index], // [新增]
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
