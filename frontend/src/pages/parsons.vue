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

    <!-- 回饋（測驗模式不顯示） -->
    <template v-if="!isTestMode">
      <div v-if="state.err && !state.noTask" class="result">
        {{ state.err }}
      </div>
    </template>

    <!-- 錯誤回饋 Modal（練習模式） -->
    <div
      v-if="feedbackModal.open"
      class="fb-modal-backdrop"
      @click.self="dismissFeedbackModal"
    >
      <div class="fb-modal" role="dialog" aria-modal="true" aria-label="作答回饋">
        <div class="fb-title">❌ 你錯在：{{ feedbackModal.slotLabel }}</div>

        <div class="fb-section">
          <div class="fb-label">錯誤類型</div>
          <div class="fb-text">{{ feedbackModal.diagnosis }}</div>
        </div>

        <div class="fb-section">
          <div class="fb-label">概念提示</div>
          <div class="fb-text">{{ feedbackModal.conceptHint }}</div>
        </div>

        <div class="fb-section">
          <div class="fb-label">引導問題</div>
          <ol class="fb-list">
            <li v-for="(q, i) in feedbackModal.reflectionQuestions" :key="`${i}-${q}`">{{ q }}</li>
          </ol>
        </div>

        <div class="fb-section fb-review">
          <div class="fb-label">建議回看影片</div>
          <div class="fb-time">{{ feedbackModal.reviewRange }}</div>
        </div>

        <div class="fb-actions">
          <button class="btn ghost" @click="dismissFeedbackModal">稍後再看</button>
          <button class="btn submit" @click="goReviewFromModal">前往複習片段</button>
        </div>
      </div>
    </div>

    <!-- 正確回饋 Modal（練習模式） -->
    <div
      v-if="successModal.open"
      class="fb-modal-backdrop"
      @click.self="dismissSuccessModal"
    >
      <div class="fb-modal success" role="dialog" aria-modal="true" aria-label="作答正確回饋">
        <div class="fb-title success">✓ 作答正確</div>

        <div class="fb-section success">
          <div class="fb-text">恭喜！你已正確完成本題的程式重組。</div>
        </div>

        <div class="fb-section success soft">
          <div class="fb-text">
            若想再次確認老師的講解內容，可以回到影片單元頁面進行複習；
            或是繼續前往下一個單元，學習新的程式概念。
          </div>
          <div v-if="!successModal.hasNext" class="fb-complete">恭喜你！完成本單元。</div>
        </div>

        <div class="fb-actions">
          <button class="btn ghost" @click="goBackToUnitPage">回到影片單元頁面</button>
          <button v-if="successModal.hasNext" class="btn submit" @click="goNextUnit">前往下一題</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch, nextTick, onBeforeUnmount } from "vue";
import { useRoute, useRouter } from "vue-router";

const API_BASE = "http://127.0.0.1:5000";

const route = useRoute();
const router = useRouter();

// ==============================
// 測驗計數器
const testStartedAt = ref(Date.now());
const durationSec = ref(0);

onMounted(() => {
  testStartedAt.value = Date.now();
});

// ==============================
// V1.8：前/後測模式參數
// ==============================
const isTestMode = computed(() => String(route.query.mode || "") === "test");
const testRole = computed(() => String(route.query.test_role || "pre").toLowerCase());
const testCycleId = computed(() => String(route.query.test_cycle_id || "default"));
const studentId = computed(() => String(localStorage.getItem("student_id") || localStorage.getItem("studentId") || "").trim());
const participantId = computed(() => String(localStorage.getItem("participant_id") || "").trim());

// AI 回饋（練習模式用）
const aiFeedbackDetail = ref(null);
const aiConceptExplanation = ref("");
const aiPossibleCauses = ref([]);
const aiImpact = ref("");
const aiGuidingQuestion = ref("");

const testMeta = reactive({
  test_task_id: "",
  source_task_id: "",
  started_at_ms: 0,
  total: 1,
  current_index: 1,
  request_index: 1,
});

const videoId = computed(() => {
  return String(route.params.videoId || route.params.id || route.params.video_id || "");
});

const review_attempt_id = computed(() => {
  const a = route.query.review_attempt_id ? String(route.query.review_attempt_id) : "";
  const b = route.query.attempt_id ? String(route.query.attempt_id) : "";
  return a || b;
});

// ====== 後端載入的題目資料 ======
const task = ref(null);
const poolBlocks = ref([]);
const templateSlots = ref([]);

// ====== 拖曳狀態 ======
const dragging = ref(null);
const overSlot = ref(null);
const filled = reactive({});

function normalizeFilledBlock(block) {
  if (!block) return null;
  return { ...block, meaning_zh: "" };
}

// ==============================
// V1.4：Tab / Space / Backspace 控制縮排
// ==============================
const activeSlotKey = ref(null);
const slotIndentLevel = reactive({});

let _blankCleanupFns = [];
let _docKeydownHandler = null;

const INDENT_STEP_PX = 24;
const INDENT_UNIT_PX = Math.max(1, Math.round(INDENT_STEP_PX / 4));
const INDENT_MAX_PX = INDENT_STEP_PX * 2;
const INDENT_MAX_LEVEL = 2;

function setActiveSlotKey(k) {
  if (k == null) return;
  activeSlotKey.value = String(k);
}

function clampIndentPx(px) {
  const n = Number(px || 0);
  return Math.max(0, Math.min(INDENT_MAX_PX, n));
}

async function applyIndentStylesToDOM() {
  await nextTick();
  const blanks = Array.from(document.querySelectorAll(".cloze .blank"));
  const slots = templateSlots.value || [];

  blanks.forEach((el, idx) => {
    const slotKey = slots[idx]?.slot != null ? String(slots[idx].slot) : null;
    const pxRaw = slotKey != null ? Number(slotIndentLevel[slotKey] || 0) : 0;
    const px = clampIndentPx(pxRaw);

    el.style.marginLeft = px ? `${px}px` : "";
    el.style.width = px ? `calc(100% - ${px}px)` : "";
  });
}

function indentBoxPx(slotKey, deltaPx) {
  const k = slotKey != null ? String(slotKey) : null;
  if (!k) return false;

  const cur = clampIndentPx(slotIndentLevel[k]);
  const next = clampIndentPx(cur + Number(deltaPx || 0));
  if (next === cur) return false;

  slotIndentLevel[k] = next;
  return true;
}

function pickTargetSlotKey() {
  const a = activeSlotKey.value != null ? String(activeSlotKey.value) : "";
  if (a) return a;

  const slots = templateSlots.value || [];
  if (slots[0]?.slot != null) return String(slots[0].slot);
  return null;
}

async function bindBlankFocusHandlers() {
  try {
    (_blankCleanupFns || []).forEach((fn) => { try { fn(); } catch (_) {} });
  } catch (_) {}
  _blankCleanupFns = [];

  await nextTick();

  const blanks = Array.from(document.querySelectorAll(".cloze .blank"));
  if (!blanks.length) return;

  blanks.forEach((el, idx) => {
    el.setAttribute("tabindex", "0");

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

const feedbackModal = reactive({
  open: false,
  slotLabel: "",
  diagnosis: "",
  conceptHint: "",
  reflectionQuestions: [],
  reviewRange: "（未提供）",
  start: null,
  end: null,
  jumpVideoId: "",
  attemptId: "",
  reviewAttemptId: "",
  taskId: "",
});

const successModal = reactive({
  open: false,
  hasNext: false,
  nextVideoId: "",
  unitName: "",
});

// ========= localStorage 暫存 =========
const PRACTICE_STATE_KEY = "parsons_practice_state_v1";
const RESTORE_ONCE_KEY = "parsons_restore_once_v1";
const RESTORE_MSG_KEY = "parsons_restore_msg_v1";

function resetFilled() {
  for (const k of Object.keys(filled)) delete filled[k];
  wrongIndices.value = [];
  result.value = null;
  state.err = "";

  for (const k of Object.keys(slotIndentLevel)) delete slotIndentLevel[k];
  activeSlotKey.value = null;
}

function isUsed(blockId) {
  return Object.values(filled).some((b) => String(b?.id) === String(blockId));
}

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

  const existedKey = findSlotKeyByBlockId(payload.block.id);
  if (existedKey != null && existedKey !== slotKey) {
    delete filled[existedKey];
  }

  if (payload.fromSlotKey != null && payload.fromSlotKey !== slotKey) {
    delete filled[payload.fromSlotKey];
  }

  filled[slotKey] = normalizeFilledBlock(payload.block);
  setActiveSlotKey(slotKey);
  dragging.value = null;
}

function removeFromSlot(slotKey) {
  if (slotKey == null) return;
  delete filled[String(slotKey)];

  const idx = templateSlots.value.findIndex(s => String(s.slot) === String(slotKey));
  if (idx >= 0) {
    wrongIndices.value = (wrongIndices.value || []).filter(i => i !== idx);
  }

  delete slotIndentLevel[String(slotKey)];
  if (String(activeSlotKey.value || "") === String(slotKey)) activeSlotKey.value = null;
  applyIndentStylesToDOM();
}

const answer_ids = computed(() => {
  return (templateSlots.value || []).map((s) => filled[String(s.slot)]?.id || null);
});

// ==============================
// V1.8：測驗模式 - 送出後自動跳下一題（或結束）
// ==============================
async function advanceTestAfterSubmit() {
  try {
    const total = Number(testMeta.total || 1);
    const cur = Number(testMeta.current_index || 1);

    if (total <= 1 || cur >= total) {
      const role = String(testRole.value || "");
      if (role === "pre") {
        alert("前測已完成！系統將帶您回首頁。");
      } else if (role === "post") {
        alert("後測已完成！感謝您的作答。");
      }
      router.replace("/home");
      return;
    }

    try {
      const url = `${API_BASE}/api/parsons/test/task?student_id=${encodeURIComponent(studentId.value)}&test_role=${encodeURIComponent(testRole.value)}&test_cycle_id=${encodeURIComponent(testCycleId.value)}&next_index=${cur + 1}`;
      const resp = await fetch(url);
      if (resp.ok) {
        const j = await resp.json();
        testMeta.test_task_id = String(j?.test_task_id || testMeta.test_task_id || "");
        testMeta.source_task_id = String(j?.source_task_id || testMeta.source_task_id || "");
        testMeta.total = j?.total || total;
        testMeta.current_index = j?.current_index || (cur + 1);

        const norm = normalizeTaskPayload(j);
        task.value = norm.taskObj;
        poolBlocks.value = norm.pool;
        templateSlots.value = norm.slots;

        resetFilled();
        wrongIndices.value = [];
        result.value = null;
        state.err = "";
        await bindBlankFocusHandlers();
        return;
      }
    } catch (innerErr) {
      console.warn("advanceTestAfterSubmit: fetch next failed", innerErr);
    }

    // 後端不支援或失敗 → 回 home
    try {
      if (window?.history?.length > 1) router.back();
      else router.push("/home");
    } catch (_) {
      router.push("/home");
    }
  } catch (e) {
    console.error("advanceTestAfterSubmit error", e);
    try {
      if (window?.history?.length > 1) router.back();
      else router.push("/home");
    } catch (_) {
      router.push("/home");
    }
  }
}

function fmtTime(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function buildWrongFeedbackParts(r) {
  const indentErrors = Array.isArray(r?.indent_errors) ? r.indent_errors : [];
  const hasIndentError = indentErrors.length > 0;
  const wrongIndex = Number.isInteger(r?.wrong_index) ? Number(r.wrong_index) : null;
  const focusIndex = hasIndentError
    ? Number(indentErrors[0])
    : (wrongIndex != null ? wrongIndex : null);
  const slotNum = (focusIndex != null && focusIndex >= 0) ? (focusIndex + 1) : null;

  const slotLabel = hasIndentError
    ? `第${slotNum || "?"}格的程式區塊縮排不正確`
    : (r?.slot_label ? `${String(r.slot_label)}的程式區塊位置不正確` : `第${slotNum || "?"}格的程式區塊位置不正確`);

  const startSec = r?.jump?.start ?? r?.review_t ?? null;
  const endSec = r?.jump?.end ?? null;
  const reviewRange = (startSec != null && endSec != null)
    ? `${fmtTime(startSec)}–${fmtTime(endSec)}`
    : "（未提供）";

  const possibleCauses = Array.isArray(r?.ai_feedback_detail?.possible_causes)
    ? r.ai_feedback_detail.possible_causes
        .map((x) => String(x || "").trim())
        .filter(Boolean)
    : [];

  const reflectionQuestions = Array.isArray(r?.ai_feedback_detail?.reflection_questions)
    ? r.ai_feedback_detail.reflection_questions
        .map((x) => String(x || "").trim())
        .filter(Boolean)
        .slice(0, 3)
    : [];

  const diagnosis = hasIndentError
    ? `縮排錯誤（共 ${indentErrors.length} 格）`
    : "程式區塊錯誤";

  const conceptHint = hasIndentError
    ? "Python 中 if 條件成立時，\n其程式區塊需要使用縮排表示。"
    : (
        (r?.ai_feedback_detail?.concept_hint && String(r.ai_feedback_detail.concept_hint).trim()) ||
        (r?.hint && String(r.hint).trim()) ||
        (r?.ai_feedback_detail?.concept_explanation && String(r.ai_feedback_detail.concept_explanation).trim()) ||
        "請重新檢查這一格在整體程式流程中的角色。"
      );

  const guidingQuestion1 = hasIndentError
    ? "這一行程式是否只應在條件成立時執行？"
    : (reflectionQuestions[0] || ((r?.ai_feedback_detail?.guiding_question && String(r.ai_feedback_detail.guiding_question).trim()) || "這一格程式在流程中的主要目的為何？"));

  const guidingQuestion2 = hasIndentError
    ? "如果沒有縮排，程式會在哪些情況執行？"
    : (reflectionQuestions[1] || (possibleCauses[0] ? `你覺得這次是否出現這個狀況：${possibleCauses[0]}？` : "這行程式放在目前位置，會不會提早或延後執行？"));

  return {
    slotLabel,
    diagnosis,
    conceptHint,
    reflectionQuestions: [guidingQuestion1, guidingQuestion2],
    reviewRange,
    startSec,
    endSec,
    hasIndentError,
    indentErrorCount: indentErrors.length,
  };
}

function buildWrongFeedback(r) {
  const parts = buildWrongFeedbackParts(r);

  return [
    `❌ 你錯在：${parts.slotLabel}`,
    "",
    "錯誤類型",
    parts.diagnosis,
    "",
    "概念提示",
    parts.conceptHint,
    "",
    "引導思考",
    `1 ${parts.reflectionQuestions[0] || "請重新檢查此格在流程中的執行條件。"}`,
    `2 ${parts.reflectionQuestions[1] || "這行程式若放在目前位置，執行時機是否正確？"}`,
    "",
    "建議回看影片",
    parts.reviewRange,
  ].join("\n");
}

async function resolveNextVideoInUnit(curVideoId) {
  const vid = String(curVideoId || "").trim();
  if (!vid) return { hasNext: false, nextVideoId: "", unitName: "" };

  try {
    const res = await fetch(`${API_BASE}/api/admin_upload/videos`);
    if (!res.ok) return { hasNext: false, nextVideoId: "", unitName: "" };

    const data = await res.json();
    const list = Array.isArray(data) ? data : (data?.items || data?.videos || []);
    const rows = (list || [])
      .filter((v) => v && v.active !== false && v.deleted !== true)
      .map((v) => ({
        id: String(v?._id?.$oid || v?._id || v?.id || "").trim(),
        unit: String(v?.unit || "").trim(),
      }))
      .filter((v) => v.id);

    const curIdx = rows.findIndex((x) => x.id === vid);
    if (curIdx < 0) return { hasNext: false, nextVideoId: "", unitName: "" };

    const unitName = rows[curIdx].unit;
    if (!unitName) return { hasNext: false, nextVideoId: "", unitName: "" };

    const sameUnit = rows.filter((x) => x.unit === unitName);
    const unitIdx = sameUnit.findIndex((x) => x.id === vid);
    if (unitIdx < 0 || unitIdx >= sameUnit.length - 1) {
      return { hasNext: false, nextVideoId: "", unitName };
    }

    return { hasNext: true, nextVideoId: sameUnit[unitIdx + 1].id, unitName };
  } catch (_) {
    return { hasNext: false, nextVideoId: "", unitName: "" };
  }
}

async function openSuccessModal(r) {
  const nextInfo = await resolveNextVideoInUnit(videoId.value);
  successModal.hasNext = !!nextInfo.hasNext;
  successModal.nextVideoId = String(nextInfo.nextVideoId || "");
  successModal.unitName = String(nextInfo.unitName || "");
  successModal.open = true;
}

function dismissSuccessModal() {
  successModal.open = false;
}

function goBackToUnitPage() {
  successModal.open = false;
  if (videoId.value) {
    router.push(`/learn/video/${encodeURIComponent(String(videoId.value))}`);
    return;
  }
  router.push("/home");
}

function goNextUnit() {
  successModal.open = false;
  const nextId = String(successModal.nextVideoId || "").trim();
  if (!nextId) return;
  router.push({
    path: `/parsons/${encodeURIComponent(nextId)}`,
    query: {
      level: route.query.level ? String(route.query.level) : "L1",
    },
  });
}

function openFeedbackModal(r, taskId) {
  const parts = buildWrongFeedbackParts(r || {});
  feedbackModal.open = true;
  feedbackModal.slotLabel = parts.slotLabel;
  feedbackModal.diagnosis = parts.diagnosis;
  feedbackModal.conceptHint = parts.conceptHint;
  feedbackModal.reflectionQuestions = parts.reflectionQuestions;
  feedbackModal.reviewRange = parts.reviewRange;
  feedbackModal.start = parts.startSec;
  feedbackModal.end = parts.endSec;
  feedbackModal.jumpVideoId = String(r?.jump?.video_id || "");
  feedbackModal.attemptId = String(r?.attempt_id || "");
  feedbackModal.reviewAttemptId = String(r?.review_attempt_id || r?.attempt_id || "");
  feedbackModal.taskId = String(taskId || "");
}

async function dismissFeedbackModal() {
  if (feedbackModal.attemptId) {
    await sendChoice(feedbackModal.attemptId, "no");
  }
  feedbackModal.open = false;
}

async function goReviewFromModal() {
  if (!feedbackModal.jumpVideoId) {
    feedbackModal.open = false;
    return;
  }

  await sendChoice(feedbackModal.attemptId, "yes");
  savePracticeState({ attempt_id: feedbackModal.attemptId || "" });

  const start = feedbackModal.start ?? 0;
  const end = feedbackModal.end ?? 0;
  const jumpVideoId = feedbackModal.jumpVideoId;
  const attemptId = feedbackModal.reviewAttemptId;
  const taskId = feedbackModal.taskId;

  feedbackModal.open = false;

  router.push({
    path: `/learn/video/${jumpVideoId}`,
    query: {
      start,
      end,
      attempt_id: String(attemptId || ""),
      task_id: String(taskId || ""),
      level: route.query.level ? String(route.query.level) : "L1",
    },
  });
}

async function sendChoice(attempt_id, choice) {
  if (!attempt_id) return;
  try {
    await fetch(`${API_BASE}/api/parsons/review_choice`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        attempt_id,
        student_id: (localStorage.getItem("student_id") || ""),
        student_choice: choice
      }),
    });
  } catch (_) {}
}

function savePracticeState(extra = {}) {
  try {
    const stateObj = {
      videoId: String(videoId.value || ""),
      level: String(route.query.level || "L1"),
      task_id: task.value?.task_id || task.value?._id || "",
      filled_map: (templateSlots.value || []).map((s) => filled[String(s.slot)]?.id || null),
      slot_indent_map: JSON.parse(JSON.stringify(slotIndentLevel || {})),
      wrong_indices: Array.isArray(wrongIndices.value) ? wrongIndices.value : [],
      saved_at: Date.now(),
      ...extra,
    };
    localStorage.setItem(PRACTICE_STATE_KEY, JSON.stringify(stateObj));
  } catch (_) {}
}

function buildAnswerLines() {
  try {
    const slots = templateSlots.value || [];
    const lines = [];
    slots.forEach((s) => {
      const slotKey = s?.slot != null ? String(s.slot) : null;
      const block = slotKey != null ? filled[String(slotKey)] : null;
      const rawText = String(block?.text || "").replace(/\r\n/g, "\n");
      const text = rawText.replace(/^[ \t]+/, "");
      const pxRaw = slotKey != null ? Number(slotIndentLevel[String(slotKey)] || 0) : 0;
      const px = clampIndentPx(pxRaw);
      const spaces = Math.max(0, Math.round(px / INDENT_UNIT_PX));
      const prefix = spaces ? " ".repeat(spaces) : "";
      lines.push(prefix + text);
    });
    return lines;
  } catch (_) {
    return [];
  }
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

    try {
      const m = st.slot_indent_map || {};
      for (const k of Object.keys(slotIndentLevel)) delete slotIndentLevel[k];
      for (const k of Object.keys(m)) slotIndentLevel[String(k)] = Number(m[k] || 0);
      applyIndentStylesToDOM();
    } catch (_) {}

    localStorage.setItem(RESTORE_ONCE_KEY, "1");
    localStorage.setItem(RESTORE_MSG_KEY, "已恢復上一題作答");
    return true;
  } catch (_) {
    return false;
  }
}

// ==============================
// 清空 AI 回饋狀態
// ==============================
function clearAiFeedback() {
  aiFeedbackDetail.value = null;
  aiConceptExplanation.value = "";
  aiPossibleCauses.value = [];
  aiImpact.value = "";
  aiGuidingQuestion.value = "";
}

// ====== 送出作答 ======
async function submit() {
  state.submitting = true;
  state.err = "";
  result.value = null;
  wrongIndices.value = [];

  // 每次送出前先清空 AI 回饋
  aiFeedbackDetail.value = null;
  aiConceptExplanation.value = "";
  aiPossibleCauses.value = [];
  aiImpact.value = "";
  aiGuidingQuestion.value = "";

  try {
    // ==============================
    // 前/後測模式：不顯示任何回饋，直接下一題/結束
    // ==============================
    if (isTestMode.value) {
      if (!studentId.value) {
        state.err = "";
        result.value = null;
        try {
          if (window?.history?.length > 1) router.back();
          else router.push("/home");
        } catch (_) {
          router.push("/home");
        }
        return;
      }

      const duration_sec = testMeta.started_at_ms
        ? Math.max(0, Math.round((Date.now() - Number(testMeta.started_at_ms)) / 1000))
        : 0;

      const body = {
        student_id: String(studentId.value || ""),
        participant_id: String(participantId.value || ""),
        test_cycle_id: String(testCycleId.value || ""),
        test_role: String(testRole.value || ""),
        test_task_id: String(testMeta.test_task_id || ""),
        source_task_id: String(testMeta.source_task_id || ""),
        answer_lines: buildAnswerLines(),
        answer_ids: Array.isArray(answer_ids.value)
          ? answer_ids.value.filter(Boolean)
          : [],
        duration_sec,
      };

      console.log("poolBlocks sample =", poolBlocks.value?.slice?.(0, 3));
      console.log("answer_ids raw =", answer_ids.value);
      console.log("SUBMIT BODY =", body);
      console.log("API_BASE =", API_BASE);

      const res = await fetch(`${API_BASE}/api/parsons/test/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (res.status === 403) {
        window.alert("後測尚未開放，請等待老師開放後再作答。");
        router.push("/home");
        return;
      }

      const r = await res.json();

      // 前後測：完全不顯示任何回饋
      aiFeedbackDetail.value = null;
      aiConceptExplanation.value = "";
      aiPossibleCauses.value = [];
      aiImpact.value = "";
      aiGuidingQuestion.value = "";

      result.value = null;
      wrongIndices.value = [];
      state.err = "";

      if (r?.ok && r?.already_submitted) {
        window.alert("你已經完成本次測驗，無法重複作答。");
        router.push("/home");
        return;
      }

      if (r?.ok) {
        await advanceTestAfterSubmit();
      } else {
        try {
          if (window?.history?.length > 1) router.back();
          else router.push("/home");
        } catch (_) {
          router.push("/home");
        }
      }
      return;
    }

    // ==============================
    // 一般練習模式：顯示 AI 回饋 + 回看影片
    // ==============================
    const task_id = task.value?.task_id || task.value?._id;
    if (!task_id) {
      state.err = "尚未載入題目，無法送出。";
      result.value = { is_correct: false, feedback: state.err };
      return;
    }

    const body = {
      task_id,
      answer_ids: answer_ids.value,
      answer_lines: buildAnswerLines(),
      video_id: String(videoId.value || ""),
      level: route.query.level ? String(route.query.level) : "L1",
      review_attempt_id: review_attempt_id.value,
      student_id: String(studentId.value || ""),
      participant_id: String(participantId.value || ""),
    };

    const res = await fetch(`${API_BASE}/api/parsons/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const r = await res.json();

    // AI 回饋優先覆蓋 hint
    if (r?.ai_feedback_detail) {
      r.hint =
        r.ai_feedback_detail.concept_explanation ||
        r.ai_feedback_detail.guiding_question ||
        r.hint ||
        "";
    }

    // 一般練習模式：保留 AI 回饋內容
    aiFeedbackDetail.value = r?.ai_feedback_detail || null;
    aiConceptExplanation.value = r?.ai_feedback_detail?.concept_explanation || "";
    aiPossibleCauses.value = Array.isArray(r?.ai_feedback_detail?.possible_causes)
      ? r.ai_feedback_detail.possible_causes
      : [];
    aiImpact.value = r?.ai_feedback_detail?.impact || "";
    aiGuidingQuestion.value = r?.ai_feedback_detail?.guiding_question || "";

    if (r?.ok && r?.is_correct === false) {
      // 統一使用前端模板，避免後端舊訊息混入「診斷結果」等舊格式
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
      await openSuccessModal(r);
    }

    if (r?.ok && r?.is_correct === false && r?.jump?.video_id) {
      openFeedbackModal(r, task_id);
      return;
    }
  } catch (e) {
    state.err = e?.message || "送出失敗";
    result.value = { is_correct: false, feedback: state.err };

    aiFeedbackDetail.value = null;
    aiConceptExplanation.value = "";
    aiPossibleCauses.value = [];
    aiImpact.value = "";
    aiGuidingQuestion.value = "";
  } finally {
    state.submitting = false;
  }
}

// ==============================
// V1.5：相容後端回傳格式
// ==============================
function normalizeTaskPayload(apiJson) {
  if (!apiJson) return { taskObj: null, pool: [], slots: [] };

  const t = apiJson.task ? apiJson.task : apiJson;
  if (!t) return { taskObj: null, pool: [], slots: [] };

  if (!t.question_text) {
    if (t.question && typeof t.question === "object" && t.question.prompt) {
      t.question_text = String(t.question.prompt);
    } else if (t.prompt) {
      t.question_text = String(t.prompt);
    }
  }

  let pool = Array.isArray(t.pool) ? t.pool : [];
  if (!pool.length) {
    const core = Array.isArray(t.solution_blocks) ? t.solution_blocks : [];
    const dist = Array.isArray(t.distractor_blocks) ? t.distractor_blocks : [];
    const blocks = Array.isArray(t.blocks) ? t.blocks : [];
    pool = (core.length || dist.length) ? [...core, ...dist] : blocks;
  }

  pool = (pool || []).map((b, i) => ({
    id: b?.id != null ? String(b.id) : `b${i + 1}`,
    text: b?.text != null ? String(b.text) : "",
    type: b?.type ? String(b.type) : (b?.is_distractor ? "distractor" : "core"),
    meaning_zh: b?.meaning_zh != null ? String(b.meaning_zh) : (b?.semantic_zh != null ? String(b.semantic_zh) : ""),
    ...b,
  }));

  let slots = Array.isArray(t.template_slots) ? t.template_slots : [];
  if (!slots.length) {
    const order = Array.isArray(t.solution_order) ? t.solution_order : [];
    const core = Array.isArray(t.solution_blocks) ? t.solution_blocks : [];
    const n = order.length || core.length || 0;
    slots = Array.from({ length: n }, (_, idx) => ({
      slot: `s${idx + 1}`,
      label: `第 ${idx + 1} 格`,
    }));
  } else {
    slots = slots.map((s, idx) => ({
      slot: s?.slot != null ? String(s.slot) : `s${idx + 1}`,
      label: s?.label != null ? String(s.label) : `第 ${idx + 1} 格`,
      ...s,
    }));
  }

  t.template_slots = slots;
  return { taskObj: t, pool, slots };
}

// ====== 載入題目 ======
async function loadTask() {
  state.loading = true;
  state.err = "";
  state.noTask = false;

  resetFilled();

  try {
    if (isTestMode.value) {
      if (!studentId.value) {
        state.noTask = true;
        state.err = "";
        task.value = null;
        poolBlocks.value = [];
        templateSlots.value = [];
        try {
          if (window?.history?.length > 1) router.back();
          else router.push("/home");
        } catch (_) {
          router.push("/home");
        }
        return;
      }

      testMeta.started_at_ms = Date.now();

      const idxQ = route.query.test_index ? Number(route.query.test_index) : null;
      testMeta.request_index = Number.isFinite(idxQ) && idxQ > 0 ? idxQ : Number(testMeta.request_index || 1);

      const url = `${API_BASE}/api/parsons/test/task?student_id=${encodeURIComponent(studentId.value)}&test_role=${encodeURIComponent(testRole.value)}&test_cycle_id=${encodeURIComponent(testCycleId.value)}&index=${encodeURIComponent(String(testMeta.request_index))}`;
      const res = await fetch(url);

      if (!res.ok) {
        state.noTask = true;
        task.value = null;
        poolBlocks.value = [];
        templateSlots.value = [];
        try {
          const rr = await res.json();
          state.err = rr?.message || "測驗題目載入失敗";
        } catch (_) {
          state.err = "測驗題目載入失敗";
        }
        return;
      }

      const r = await res.json();

      testMeta.total = Number(r?.total || 1);
      testMeta.current_index = Number(r?.current_index || testMeta.request_index || 1);
      testMeta.test_task_id = String(r?.test_task_id || "");
      testMeta.source_task_id = String(r?.source_task_id || "");

      const norm = normalizeTaskPayload(r);
      task.value = norm.taskObj;
      poolBlocks.value = norm.pool;
      // 前後測不顯示中文提示：清空 label
      templateSlots.value = (norm.slots || []).map((s) => ({ ...s, label: "" }));

      await bindBlankFocusHandlers();
      return;
    }

    // 練習模式
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

    const norm = normalizeTaskPayload(r);
    task.value = norm.taskObj;
    poolBlocks.value = norm.pool;
    templateSlots.value = norm.slots;

    const restored = restorePracticeStateIfMatch(task.value);

    await bindBlankFocusHandlers();

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

onMounted(() => {
  _docKeydownHandler = (e) => {
    const tag = String(e.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;

    if (e.key === "Tab") {
      const k = pickTargetSlotKey();
      if (!k) return;
      e.preventDefault();
      const changed = indentBoxPx(k, +INDENT_STEP_PX);
      if (changed) applyIndentStylesToDOM();
      return;
    }

    if (e.key === " ") {
      const k = pickTargetSlotKey();
      if (!k) return;
      e.preventDefault();
      const changed = indentBoxPx(k, +INDENT_UNIT_PX);
      if (changed) applyIndentStylesToDOM();
      return;
    }

    if (e.key === "Backspace") {
      const k = pickTargetSlotKey();
      if (!k) return;
      const cur = clampIndentPx(slotIndentLevel[String(k)] || 0);
      if (cur <= 0) return;
      e.preventDefault();
      const changed = indentBoxPx(k, -INDENT_UNIT_PX);
      if (changed) applyIndentStylesToDOM();
      return;
    }
  };

  document.addEventListener("keydown", _docKeydownHandler, true);
});

onBeforeUnmount(() => {
  if (_docKeydownHandler) document.removeEventListener("keydown", _docKeydownHandler, true);
  try {
    (_blankCleanupFns || []).forEach((fn) => { try { fn(); } catch (_) {} });
  } catch (_) {}
  _blankCleanupFns = [];
});

watch(
  () => [videoId.value, route.query.level, route.query.mode, route.query.test_role, route.query.test_cycle_id, route.query.test_index],
  () => loadTask()
);
</script>

<style scoped>
:global(body) {
  background: radial-gradient(circle at 15% 12%, #fff7db 0%, rgba(255, 247, 219, 0) 34%),
    radial-gradient(circle at 88% 10%, #dff2ff 0%, rgba(223, 242, 255, 0) 30%),
    linear-gradient(180deg, #f7fafc 0%, #f2f6fb 100%);
}

.page{
  --ink: #1f2937;
  --line: #d3deea;
  --paper: rgba(255, 255, 255, 0.94);
  --paper-strong: #ffffff;
  --primary: #0f5c84;
  --accent: #f59e0b;
  --ok: #2f855a;
  --danger: #b91c1c;
  --radius-lg: 18px;
  --radius-md: 12px;
  --shadow-soft: 0 12px 30px rgba(15, 23, 42, 0.08);

  position: relative;
  padding: 22px;
  max-width: 1420px;
  margin: 0 auto;
  color: var(--ink);
  font-family: "Noto Sans TC", "Segoe UI", "PingFang TC", sans-serif;
}

.qbox{
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 18px;
  background: var(--paper);
  box-shadow: var(--shadow-soft);
  backdrop-filter: blur(3px);
}

.qtitle{
  font-size: 15px;
  font-weight: 900;
  color: #4b5563;
  letter-spacing: 0.4px;
  margin-bottom: 6px;
}

.qtext{
  font-size: 22px;
  line-height: 1.45;
  font-weight: 900;
  color: #0f172a;
}

.board{
  display: grid;
  grid-template-columns: 1.25fr 1fr;
  gap: 16px;
  margin-top: 16px;
}

.panel{
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 16px;
  min-height: 460px;
  min-width: 0;
  overflow: hidden;
  background: var(--paper);
  box-shadow: var(--shadow-soft);
  animation: rise-in .32s ease both;
}

.panel-title{
  font-size: 18px;
  font-weight: 900;
  margin-bottom: 10px;
  color: #0f172a;
}

.emptyBox{
  padding: 12px;
  border-radius: var(--radius-md);
  background: #fff3cd;
  font-weight: 900;
}

.cloze{ display:grid; gap:12px; min-width:0; }
.cloze-row{ display:grid; gap:8px; min-width:0; }
.hint{
  font-weight: 900;
  background: linear-gradient(90deg, #ffe3a1 0%, #ffd57a 100%);
  padding: 7px 10px;
  border-radius: 10px;
  color: #5b4105;
}

.blank{
  position: relative;
  border: 2px dashed #8da2b8;
  border-radius: 14px;
  padding: 12px 44px 12px 12px;
  background: var(--paper-strong);
  min-height: 48px;
  display:flex;
  align-items:center;
  box-sizing: border-box;
  max-width: 100%;
  min-width: 0;
  transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;
}
.blank:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 14px rgba(15, 23, 42, 0.08);
}
.blank.over { border-color: var(--primary); background: #eef9ff; }
.blank.filled{ border-style: solid; border-color: #4f46e5; background: #eef0ff; }
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
  background: #fee2e2;
  color: #7f1d1d;
}

.cloze-row.wrong .hint{
  background: linear-gradient(90deg, #ffc8c8 0%, #ffb2b2 100%);
}
.cloze-row.wrong .blank{
  border-color: var(--danger) !important;
  box-shadow: 0 0 0 2px rgba(185, 28, 28, .12);
}

.pool{ display:grid; gap:10px; }
.pill{
  padding: 13px 16px;
  border-radius: 14px;
  border: 1px solid #d7e1eb;
  background: linear-gradient(180deg, #ffffff 0%, #f7fbff 100%);
  font-weight:800;
  text-align:left;
  cursor:grab;
  user-select:none;
  transition: transform .15s ease, box-shadow .15s ease;
}
.pill:not(.used):hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 20px rgba(15, 23, 42, 0.1);
}
.pill.used { opacity: 0.35; cursor: not-allowed; }

.actions{ display:flex; justify-content:center; gap:16px; margin-top:18px; }
.btn{
  min-width: 160px;
  padding: 12px 16px;
  border-radius: 999px;
  border: 0;
  font-weight: 900;
  cursor: pointer;
  transition: transform .15s ease, box-shadow .15s ease, filter .15s ease;
}
.btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.2);
}
.submit{ background: linear-gradient(90deg, #2f855a 0%, #2a9d8f 100%); color:#fff; }
.btn:disabled{ opacity:.6; cursor:not-allowed; }

.result{
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  border: 1px solid #f5d98e;
  background: #fff8de;
  font-weight: 800;
}
.result.ok{ background:#e8fff2; border-color: #b6e5c8; }

.fb-modal-backdrop{
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.38);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  z-index: 1000;
}

.fb-modal{
  width: min(760px, 100%);
  max-height: 90vh;
  overflow: auto;
  border-radius: 18px;
  border: 1px solid #d9e2ee;
  background: linear-gradient(180deg, #fffdf8 0%, #ffffff 100%);
  box-shadow: 0 20px 50px rgba(15, 23, 42, 0.25);
  padding: 18px;
  animation: modal-pop .2s ease-out both;
}

.fb-modal.success{
  width: min(560px, 100%);
  background: linear-gradient(180deg, #f6fff8 0%, #ffffff 100%);
}

.fb-title{
  font-size: 22px;
  font-weight: 900;
  color: #b91c1c;
  margin-bottom: 10px;
}

.fb-title.success{
  color: #047857;
}

.fb-section{
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 12px;
  background: #ffffff;
  margin-top: 10px;
}

.fb-section.success{
  background: #f0fdf4;
  border-color: #a7f3d0;
}

.fb-section.success.soft{
  background: #f8fafc;
  border-color: #dbe4ee;
}

.fb-complete{
  margin-top: 10px;
  font-size: 16px;
  font-weight: 900;
  color: #065f46;
}

.fb-label{
  font-size: 13px;
  font-weight: 900;
  color: #334155;
  margin-bottom: 6px;
}

.fb-text{
  font-size: 15px;
  font-weight: 700;
  color: #0f172a;
  line-height: 1.6;
}

.fb-list{
  margin: 0;
  padding-left: 20px;
  display: grid;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  color: #0f172a;
}

.fb-review{
  background: #f0f9ff;
  border-color: #bae6fd;
}

.fb-time{
  font-size: 18px;
  font-weight: 900;
  color: #0c4a6e;
  letter-spacing: 0.4px;
}

.fb-actions{
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.btn.ghost{
  background: #e8edf3;
  color: #1f2937;
}

@media (max-width: 900px) {
  .page { padding: 14px; }
  .qtext { font-size: 20px; }
  .panel { min-height: 0; }
  .board{ grid-template-columns: 1fr; }
}

@keyframes rise-in {
  from { transform: translateY(6px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}

@keyframes modal-pop {
  from { transform: scale(.97); opacity: .65; }
  to { transform: scale(1); opacity: 1; }
}
</style>