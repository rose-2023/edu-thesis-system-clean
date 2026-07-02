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
            :class="{ wrong: isPrimaryWrong(idx), affected: isAffectedWrong(idx) }"
          >
            <div class="hint" v-if="effectiveShowSemanticZh && (slot.expected_meaning_zh || filled[slot.slot]?.meaning_zh)">
              <span v-if="isAffectedWrong(idx)" class="hint-affected-icon" aria-hidden="true">⚠</span>
              {{ idx + 1 }}. {{ slot.expected_meaning_zh || filled[slot.slot]?.meaning_zh }}
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
        <template v-if="feedbackModal.stage === 'choice'">
          <div class="fb-title">❌ 作答有誤</div>

          <div class="fb-section">
            <div class="fb-label">💡 你可以選擇：</div>
            <div class="fb-actions pick">
              <button class="btn submit" @click="onToggleHintOpen" :disabled="feedbackModal.hintLoading || (!feedbackModal.hintOpen && feedbackModal.hintLimitReached)">
                {{ feedbackModal.hintLoading ? "提示中..." : (feedbackModal.hintOpen ? "收起提示" : (feedbackModal.hintLimitReached ? "提示已達上限" : "查看提示")) }}
              </button>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="fb-title">❌ {{ feedbackModal.headline }}</div>

          <div class="fb-section">
            <div class="fb-label">💡 你可以選擇：</div>
            <div class="fb-actions pick">
              <button class="btn submit" @click="onToggleHintOpen" :disabled="feedbackModal.hintLoading || (!feedbackModal.hintOpen && feedbackModal.hintLimitReached)">
                {{ feedbackModal.hintLoading ? "提示中..." : (feedbackModal.hintOpen ? "收起提示" : (feedbackModal.hintLimitReached ? "提示已達上限" : "查看提示")) }}
              </button>
              <button v-if="showReviewVideoFeature" class="btn submit" @click="onToggleReviewOpen">
                {{ feedbackModal.reviewOpen ? "收起影片" : "查看影片" }}
              </button>
            </div>
          </div>

          <div class="fb-section" v-if="feedbackModal.hintOpen">
            <div class="fb-label">💡 AI提示</div>
            <div class="fb-text">{{ feedbackModal.hintQuestion || "提示產生中..." }}</div>
            <div class="fb-actions pick">
              <button
                class="btn ghost"
                :disabled="!feedbackModal.hintLoaded || feedbackModal.hintLoading || hintRemaining <= 0 || isRegeneratingHint"
                @click="handleRetryHint"
              >
                {{ hintRemaining <= 0 ? "已使用提示" : (isRegeneratingHint ? "提示中..." : `再次提示(${hintRemaining})`) }}
              </button>
            </div>
            <div v-if="showReviewVideoFeature" class="fb-actions pick">
              <button class="btn submit" @click="openReviewFromHint">回看影片</button>
            </div>
          </div>

          <div class="fb-section fb-review" v-if="showReviewVideoFeature && feedbackModal.reviewOpen">
            <div class="fb-label">🎬 建議回看片段</div>
            <div class="fb-focus" v-if="feedbackModal.reviewMode === 'chapter_recommendation'">模式：章節推薦</div>
            <div class="fb-focus" v-else>模式：秒級字幕對齊</div>
            <div class="fb-time">{{ feedbackModal.reviewRange }}</div>
            <div v-if="feedbackModal.debugText" class="fb-debug">{{ feedbackModal.debugText }}</div>
            <div class="fb-actions">
              <button class="btn submit" @click="goReviewFromModal">前往影片</button>
            </div>
          </div>
        </template>
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
const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000";
const showReviewVideoFeature = false;

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

const LEARNING_SESSION_ID_KEY = "parsons_learning_session_id_v1";
const LEARNING_SESSION_STUDENT_KEY = "parsons_learning_session_student_v1";
const LEARNING_SESSION_STARTED_KEY = "parsons_learning_session_started_v1";

function createLearningSessionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `session_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`;
}

function getLearningSessionId() {
  const currentStudentId = String(studentId.value || "");
  try {
    const storedStudentId = String(sessionStorage.getItem(LEARNING_SESSION_STUDENT_KEY) || "");
    let sessionId = String(sessionStorage.getItem(LEARNING_SESSION_ID_KEY) || "");
    if (!sessionId || storedStudentId !== currentStudentId) {
      sessionId = createLearningSessionId();
      sessionStorage.setItem(LEARNING_SESSION_ID_KEY, sessionId);
      sessionStorage.setItem(LEARNING_SESSION_STUDENT_KEY, currentStudentId);
      sessionStorage.removeItem(LEARNING_SESSION_STARTED_KEY);
    }
    return sessionId;
  } catch (_) {
    return createLearningSessionId();
  }
}

const learningSessionId = getLearningSessionId();

function currentTaskId() {
  return String(task.value?.task_id || task.value?._id || "").trim() || null;
}

function currentTargetConcept() {
  const raw = task.value?.target_concept ?? task.value?.concept;
  if (raw != null && String(raw).trim()) return String(raw).trim();
  const tags = Array.isArray(task.value?.tags) ? task.value.tags : [];
  return tags.length && String(tags[0] || "").trim() ? String(tags[0]).trim() : null;
}

async function logLearningEvent(eventType, extra = {}) {
  if (!studentId.value) return null;
  const body = {
    session_id: learningSessionId,
    student_id: String(studentId.value),
    event_type: String(eventType || ""),
    page: "parsons",
    activity_type: isTestMode.value ? "test" : "practice",
    test_role: isTestMode.value ? String(testRole.value || "") : null,
    task_id: currentTaskId(),
    target_concept: currentTargetConcept(),
    event_at: new Date().toISOString(),
    ...extra,
  };
  try {
    const response = await fetch(`${API_BASE}/api/learning_logs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) return null;
    return await response.json().catch(() => null);
  } catch (_) {
    return null;
  }
}

async function initializeLearningLogging() {
  let alreadyStarted = false;
  try {
    alreadyStarted = sessionStorage.getItem(LEARNING_SESSION_STARTED_KEY) === learningSessionId;
  } catch (_) {}

  if (!alreadyStarted) {
    const result = await logLearningEvent("session_start", { task_id: null, target_concept: null });
    if (result?.ok) {
      try { sessionStorage.setItem(LEARNING_SESSION_STARTED_KEY, learningSessionId); } catch (_) {}
    }
  }
  await logLearningEvent("page_view", { task_id: null, target_concept: null });
}

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
const teacherForcedHideSemantic = ref(false);
const effectiveShowSemanticZh = computed(() => !teacherForcedHideSemantic.value);

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
const INDENT_MAX_LEVEL = 3;
const INDENT_MAX_PX = INDENT_STEP_PX * INDENT_MAX_LEVEL;

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
const primaryWrongIndex = ref(null);

// ====== UI 狀態 ======
const state = reactive({
  loading: false,
  submitting: false,
  noTask: false,
  err: "",
});

const feedbackModal = reactive({
  open: false,
  stage: "choice",
  headline: "",
  locationText: "",
  hintQuestion: "",
  slotLabel: "",
  diagnosis: "",
  conceptHint: "",
  reflectionQuestions: [],
  impactHint: "",
  reviewRange: "（未提供）",
  reviewMode: "subtitle_alignment",
  subtitleHealth: null,
  chapterRecommendation: null,
  chapterLabel: "",
  reviewFocus: "",
  hintOpen: false,
  hintLoading: false,
  hintLoaded: false,
  hintError: "",
  hintLimitReached: false,
  reviewOpen: false,
  start: null,
  end: null,
  chapterStart: null,
  chapterEnd: null,
  jumpVideoId: "",
  attemptId: "",
  attemptV2Id: "",
  attemptNo: null,
  reviewAttemptId: "",
  taskId: "",
  targetConcept: "",
  hintClicked: false,
  videoClicked: false,
  source: "default",
  firstHint: "",
  secondHint: "",
  possibleCauses: [],
  actualText: "",
  expectedText: "",
  debugText: "",
});

const maxHintRetry = 1;
const hintRetryUsed = ref(0);
const isRegeneratingHint = ref(false);
const hintRemaining = computed(() => Math.max(0, maxHintRetry - hintRetryUsed.value));

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
  primaryWrongIndex.value = null;
  result.value = null;
  state.err = "";

  for (const k of Object.keys(slotIndentLevel)) delete slotIndentLevel[k];
  activeSlotKey.value = null;
}

function isPrimaryWrong(idx) {
  if (primaryWrongIndex.value == null || primaryWrongIndex.value === "") return false;
  const p = Number(primaryWrongIndex.value);
  if (!Number.isFinite(p)) return false;
  return p === Number(idx);
}

function isAffectedWrong(idx) {
  if (!Array.isArray(wrongIndices.value) || !wrongIndices.value.length) return false;
  const n = Number(idx);
  if (!Number.isFinite(n)) return false;
  const hasMatch = wrongIndices.value.some((v) => Number(v) === n);
  return hasMatch && !isPrimaryWrong(n);
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
let taskStartLoggedFor = "";

async function recordTaskOpen() {
  taskStartLoggedFor = "";
  await logLearningEvent("task_open");
}

function recordTaskStartOnce() {
  const key = currentTaskId() || "unknown_task";
  if (taskStartLoggedFor === key) return;
  taskStartLoggedFor = key;
  logLearningEvent("task_start").catch(() => {});
}

function onDragStart(block) {
  recordTaskStartOnce();
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
        await recordTaskOpen();
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

function debugFeedback(payload) {
  if (!import.meta.env.DEV) return;
  try {
    console.groupCollapsed("[parsons-feedback-debug]");
    console.table(payload);
    console.groupEnd();
  } catch (_) {
    // Keep debug logging best-effort only.
  }
}

function buildWrongFeedbackParts(r) {
  const indentErrors = Array.isArray(r?.indent_errors) ? r.indent_errors : [];
  const hasIndentError = indentErrors.length > 0;
  const isWrongAnswer = r?.is_correct === false || hasIndentError || Number.isInteger(r?.wrong_index) || (Array.isArray(r?.wrong_indices) && r.wrong_indices.length > 0);
  const wrongIndex = Number.isInteger(r?.wrong_index) ? Number(r.wrong_index) : null;
  const focusIndex = (wrongIndex != null) ? wrongIndex : (hasIndentError ? Number(indentErrors[0]) : null);
  const slotNum = (focusIndex != null && focusIndex >= 0) ? (focusIndex + 1) : null;
  const backendErrorType = String(r?.error_type || "").trim().toLowerCase();
  const wrongType = String(r?.wrong_type || "").trim();
  const conceptTag = String(r?.concept_tag || "").trim();

  const aiDetail = (r?.ai_feedback_detail && typeof r.ai_feedback_detail === "object") ? r.ai_feedback_detail : {};
  const aiDiagnosisSummary = String(r?.ai_diagnosis_summary || "").trim();
  const aiConceptHint = String(aiDetail?.concept_hint || aiDetail?.concept_explanation || r?.hint || "").trim();
  const aiGuidingQuestion = String(aiDetail?.guiding_question || "").trim();
  const aiImpactHint = String(aiDetail?.impact || "").trim();
  const aiFirstHint = String(aiDetail?.first_hint || r?.hint || "").trim();
  const aiSecondHint = String(aiDetail?.second_hint || "").trim();

  const possibleCauses = Array.isArray(aiDetail?.possible_causes)
    ? aiDetail.possible_causes.map((x) => String(x || "").trim()).filter(Boolean)
    : [];
  const reflectionQuestions = Array.isArray(aiDetail?.reflection_questions)
    ? aiDetail.reflection_questions.map((x) => String(x || "").trim()).filter(Boolean).slice(0, 3)
    : [];
  if (!reflectionQuestions.length && aiGuidingQuestion) {
    reflectionQuestions.push(aiGuidingQuestion);
  }
  const hasUsableAi = Boolean(aiDiagnosisSummary || aiConceptHint || aiFirstHint || aiSecondHint || reflectionQuestions.length || possibleCauses.length || aiImpactHint);

  const startSec = Number.isFinite(Number(r?.jump?.start)) ? Number(r.jump.start) : 0;
  const endSec = Number.isFinite(Number(r?.jump?.end)) ? Number(r.jump.end) : 0;
  const subtitleHealth = (r?.subtitle_health && typeof r.subtitle_health === "object") ? r.subtitle_health : null;
  const chapterRecommendation = (r?.chapter_recommendation && typeof r.chapter_recommendation === "object") ? r.chapter_recommendation : null;
  const reviewMode = String(r?.review_mode || subtitleHealth?.mode || (chapterRecommendation ? "chapter_recommendation" : "subtitle_alignment") || "subtitle_alignment").trim() || "subtitle_alignment";
  const chapterLabel = buildChapterLabel(chapterRecommendation);
  const reviewRange = (reviewMode === "chapter_recommendation")
    ? (chapterLabel || "（章節推薦）")
    : ((endSec > startSec) ? `${fmtTime(startSec)} - ${fmtTime(endSec)}` : "（未提供）");

  const hasIfElseStructure = (poolBlocks.value || []).some((b) => String(b?.text || "").trim() === "else:")
    && (poolBlocks.value || []).some((b) => /^\s*if\s+.+:\s*$/.test(String(b?.text || "")));
  const actualText = String(r?.actual_text || "").trim();
  const expectedText = String(r?.expected_text || "").trim();
  const hasElseToken = /(^|\W)else\s*:/i.test(actualText) || /(^|\W)else\s*:/i.test(expectedText);
  const hasInlineIfPrint = /if\s+.+:\s*print\s*\(/i.test(actualText);
  const hasConditionToken = /(^|\W)if\s+.+:/i.test(actualText) || /(^|\W)if\s+.+:/i.test(expectedText);
  const hasLogicPriorityIssue = hasInlineIfPrint || hasConditionToken;
  const hasMixedLogicAndIndent = hasLogicPriorityIssue && hasIndentError;

  let errorClass = (hasIfElseStructure && hasElseToken && !hasLogicPriorityIssue)
    ? "branch_error"
    : (hasLogicPriorityIssue
        ? "logic_error"
        : (hasIndentError ? "indentation_error" : "logic_error"));

  // 後端已完成主錯誤判定時，優先採用後端類型，避免前端啟發式誤判。
  if (backendErrorType === "syntax") {
    errorClass = "indentation_error";
  } else if (backendErrorType === "structure") {
    errorClass = "structure_error";
  } else if (backendErrorType === "logic") {
    if (hasIfElseStructure && hasElseToken) errorClass = "branch_error";
    else if (hasLogicPriorityIssue) errorClass = "logic_error";
    else errorClass = "logic_error";
  }

  let slotLabel = `第${slotNum || "?"}格的條件邏輯錯誤`;
  if (hasMixedLogicAndIndent) {
    slotLabel = `第${slotNum || "?"}格的條件判斷錯誤（含縮排）`;
  } else if (errorClass === "indentation_error") {
    slotLabel = `第${slotNum || "?"}格的縮排錯誤`;
  } else if (errorClass === "structure_error") {
    slotLabel = `第${slotNum || "?"}格的結構順序錯誤`;
  } else if (errorClass === "branch_error") {
    slotLabel = `第${slotNum || "?"}格的 if-else 結構錯誤`;
  }

  let diagnosis = "邏輯錯誤";
  if (hasMixedLogicAndIndent) {
    diagnosis = "條件判斷錯誤（並伴隨縮排問題）";
  } else if (errorClass === "indentation_error") {
    diagnosis = "縮排錯誤";
  } else if (errorClass === "structure_error") {
    diagnosis = "結構順序錯誤";
  } else if (errorClass === "branch_error") {
    diagnosis = "if-else 結構錯誤";
  }

  let conceptHint = "請檢查條件成立時應輸出的內容是否正確，並確認輸出語句是否放在正確的判斷區塊中。";
  if (hasMixedLogicAndIndent) {
    conceptHint = "請檢查條件成立時應輸出的內容是否正確，並確認輸出語句是否放在正確的判斷區塊中。\n另外，這行程式也需要正確縮排，才能表示它屬於該判斷區塊。";
  } else if (errorClass === "indentation_error") {
    conceptHint = "在 Python 中，if 判斷成立時需要透過縮排表示該區塊的程式碼。\n請檢查輸出語句是否應該放在 if 判斷內部。";
  } else if (errorClass === "structure_error") {
    conceptHint = "請先確認主程式與函式呼叫的先後順序，避免把定義或呼叫放錯位置。";
  } else if (errorClass === "branch_error") {
    conceptHint = "if 與 else 應該構成完整的條件分支結構，當條件不成立時，程式會執行 else 區塊。";
  }
  if (aiConceptHint) {
    conceptHint = aiConceptHint;
  }
  if (aiDiagnosisSummary) {
    diagnosis = aiDiagnosisSummary;
  }

  let guidingQuestion1 = "當條件成立時，應該輸出哪一句訊息？";
  if (hasMixedLogicAndIndent) {
    guidingQuestion1 = "當條件成立時，你希望輸出哪一句訊息？這句輸出目前是否放在正確區塊中？";
  } else if (errorClass === "indentation_error") {
    guidingQuestion1 = "這行輸出語句是否應該在 if 判斷成立時才執行？";
  } else if (errorClass === "structure_error") {
    guidingQuestion1 = "這一行應該先於哪一行執行？目前順序是否顛倒？";
  }
  if (aiGuidingQuestion) {
    guidingQuestion1 = aiGuidingQuestion;
  }

  let guidingQuestion2 = "目前的輸出語句是否放在正確的 if / else 判斷區塊中？";
  if (hasMixedLogicAndIndent) {
    guidingQuestion2 = "如果這行沒有正確縮排，是否會在不該執行時也被執行？";
  } else if (errorClass === "branch_error") {
    guidingQuestion2 = "當條件不成立時，哪一句訊息應該放在 else 區塊？";
  } else if (errorClass === "indentation_error") {
    guidingQuestion2 = "如果不縮排，這行程式會不會在不該執行時也被執行？";
  } else if (errorClass === "structure_error") {
    guidingQuestion2 = "若把這行放在目前位置，是否會造成函式尚未定義就被呼叫？";
  }

  const impactHint = (errorClass === "branch_error" || hasMixedLogicAndIndent)
    ? "這個錯誤可能會影響後續程式區塊的排列結果，請先從主錯誤格開始調整，並留意 if / else 區塊中的縮排"
    : "這個錯誤可能會影響後續程式區塊的排列結果，請先從主錯誤格開始調整。";

  debugFeedback({
    focusIndex,
    wrongIndex,
    hasIndentError,
    indentErrorCount: indentErrors.length,
    hasIfElseStructure,
    errorClass,
    hasElseToken,
    hasInlineIfPrint,
    hasConditionToken,
    hasMixedLogicAndIndent,
    diagnosis,
    slotLabel,
    actualText,
    expectedText,
    wrongType,
    conceptTag,
  });

  const resolvedReflections = reflectionQuestions.length ? reflectionQuestions : [guidingQuestion1, guidingQuestion2].filter(Boolean);

  return {
    focusIndex,
    slotLabel,
    diagnosis,
    errorClass,
    conceptHint,
    reflectionQuestions: resolvedReflections,
    impactHint: aiImpactHint || impactHint,
    reviewRange,
    startSec,
    endSec,
    hasIndentError,
    indentErrorCount: indentErrors.length,
    source: hasUsableAi ? "ai" : "default",
    firstHint: aiFirstHint,
    secondHint: aiSecondHint,
    possibleCauses,
    actualText,
    expectedText,
    wrongType,
    conceptTag,
    reviewMode,
    subtitleHealth,
    chapterRecommendation,
    chapterLabel,
  };
}

function buildWrongFeedback(r) {
  const parts = buildWrongFeedbackParts(r);
  const reviewLabel = String(parts.reviewMode || "").trim() === "chapter_recommendation"
    ? "建議回看章節"
    : "建議回看影片";

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
    reviewLabel,
    parts.reviewRange,
  ].join("\n");
}

function getSlotNumberFromLabel(label) {
  const m = String(label || "").match(/第\s*(\d+)\s*格/);
  return m ? Number(m[1]) : null;
}

function buildFeedbackHeadline(parts) {
  const n = getSlotNumberFromLabel(parts?.slotLabel);
  const numText = Number.isFinite(n) ? `第${n}格` : "此格";
  if (String(parts?.slotLabel || "").includes("含縮排")) {
    return `${numText}有錯誤（條件判斷 + 縮排）`;
  }
  return `${numText}有錯誤（${parts?.diagnosis || "邏輯錯誤"}）`;
}

function buildFeedbackLocation(parts) {
  const n = getSlotNumberFromLabel(parts?.slotLabel);
  const numText = Number.isFinite(n) ? `第${n}格` : "此格";
  if (String(parts?.slotLabel || "").includes("條件")) {
    return `${numText}（if 判斷區塊）`;
  }
  return `${numText}（程式區塊）`;
}

function buildFeedbackReviewFocus(parts) {
  if (String(parts?.reviewMode || "").trim() === "chapter_recommendation") {
    return String(parts?.chapterLabel || parts?.conceptTag || "章節推薦");
  }
  if (String(parts?.slotLabel || "").includes("條件") && String(parts?.slotLabel || "").includes("縮排")) {
    return "if 與縮排";
  }
  if (String(parts?.slotLabel || "").includes("縮排")) {
    return "縮排層級與執行範圍";
  }
  if (String(parts?.slotLabel || "").includes("條件")) {
    return "條件判斷流程";
  }
  return "程式區塊流程";
}

function buildHintQuestion(parts) {
  const firstHint = String(parts?.firstHint || "").trim();
  if (firstHint) return firstHint;
  const q1 = Array.isArray(parts?.reflectionQuestions) ? String(parts.reflectionQuestions[0] || "").trim() : "";
  if (q1) return q1;
  if (String(parts?.slotLabel || "").includes("條件") && String(parts?.slotLabel || "").includes("縮排")) {
    return "這一行輸出應該在 if 區塊內嗎？";
  }
  if (String(parts?.slotLabel || "").includes("條件")) {
    return "這一行應該屬於條件判斷區塊嗎？";
  }
  if (String(parts?.slotLabel || "").includes("縮排")) {
    return "這一行是否需要再往右縮排一層？";
  }
  return "這一行在目前流程中的位置正確嗎？";
}

const CONCEPT_TAG_ZH = {
  loop_count_control: "迴圈次數控制",
  loop_reverse_range: "反向 range 迴圈",
  nested_loop_structure: "巢狀迴圈結構",
  if_condition_logic: "條件判斷邏輯",
  if_branch_order: "分支順序",
  edge_case_condition: "邊界條件",
  star_formula_2i_minus_1: "星號公式 2i-1",
  space_formula_n_minus_i: "空白公式 n-i",
  input_int_cast: "輸入轉整數",
  print_separator: "輸出分隔格式",
  python_syntax: "Python 語法",
};

function toConceptZh(tag) {
  const key = String(tag || "").trim().toLowerCase();
  if (!key) return "";
  return CONCEPT_TAG_ZH[key] || String(tag || "").trim();
}

function buildChapterLabel(ch) {
  const title = String(ch?.chapter_title || ch?.chapter_name || ch?.title || "").trim();
  const conceptLabel = String(ch?.concept_label || ch?.chapter_label || "").trim();
  const family = String(ch?.family || "").trim();
  const conceptTags = Array.isArray(ch?.concept_tags)
    ? ch.concept_tags.map((x) => String(x || "").trim()).filter(Boolean)
    : [];
  const conceptTagsZh = conceptTags.map((tag) => toConceptZh(tag)).filter(Boolean);
  const _num = (v) => {
    if (v === null || v === undefined) return null;
    const t = String(v).trim();
    if (!t) return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  };
  // 章節模式應優先使用秒數欄位，不應把 slot_start/slot_end（格位索引）當時間。
  const chapterStartSec = _num(ch?.start_sec) ?? _num(ch?.start);
  const chapterEndSec = _num(ch?.end_sec) ?? _num(ch?.end);
  const rangeText = (chapterStartSec != null && chapterEndSec != null && chapterEndSec > chapterStartSec)
    ? `${fmtTime(chapterStartSec)} - ${fmtTime(chapterEndSec)}`
    : "";
  const head = title || conceptLabel || family || conceptTagsZh[0] || "章節推薦";
  let tail = "";
  if (conceptLabel && conceptLabel !== head) {
    tail = `・${conceptLabel}`;
  } else if (!conceptLabel && conceptTagsZh.length) {
    const tagsText = conceptTagsZh.slice(0, 2).join("／");
    if (tagsText && tagsText !== head) {
      tail = `・${tagsText}`;
    }
  }
  return `${head}${tail}${rangeText ? `（${rangeText}）` : ""}`;
}

function getChapterBoundary(ch, keys) {
  for (const key of keys) {
    const raw = ch?.[key];
    if (raw === null || raw === undefined) continue;
    const text = String(raw).trim();
    if (!text) continue;
    const n = Number(text);
    if (Number.isFinite(n)) {
      return n;
    }
  }
  return null;
}

function buildReviewRangeText(parts) {
  if (String(parts?.reviewMode || "").trim() === "chapter_recommendation") {
    return String(parts?.chapterLabel || "").trim() || "（章節推薦）";
  }
  return String(parts?.reviewRange || "").trim() || "（未提供）";
}

function buildRetryHintQuestion(parts, usedCount = 1) {
  const secondHint = String(parts?.secondHint || "").trim();
  if (secondHint) return secondHint;

  const reflectionQuestions = Array.isArray(parts?.reflectionQuestions)
    ? parts.reflectionQuestions.map((x) => String(x || "").trim()).filter(Boolean)
    : [];
  if (usedCount === 1 && reflectionQuestions.length >= 2) {
    return reflectionQuestions[1];
  }

  const possibleCauses = Array.isArray(parts?.possibleCauses)
    ? parts.possibleCauses.map((x) => String(x || "").trim()).filter(Boolean)
    : [];
  if (possibleCauses.length > 0) {
    return possibleCauses[0];
  }

  const actualText = String(parts?.actualText || "").trim();
  const expectedText = String(parts?.expectedText || "").trim();
  if (actualText && expectedText && actualText !== expectedText) {
    if (actualText.includes("print(")) {
      return "目前這行只呈現部分輸出內容，請檢查是否同時包含固定文字與變數資訊。";
    }
    return "目前這一行與題目要求不完全一致，請檢查是否少了一部分必要資訊。";
  }

  const conceptHint = String(parts?.conceptHint || "").trim();
  if (conceptHint) return conceptHint;

  return "請重新比對題目要求與目前輸出結果。";
}

async function handleRetryHint() {
  if (hintRemaining.value <= 0) return;
  if (isRegeneratingHint.value) return;
  if (!feedbackModal.open) return;
  if (!feedbackModal.hintLoaded || feedbackModal.hintLoading) return;

  try {
    isRegeneratingHint.value = true;
    const parts = {
      secondHint: feedbackModal.secondHint,
      reflectionQuestions: feedbackModal.reflectionQuestions,
      possibleCauses: feedbackModal.possibleCauses,
      conceptHint: feedbackModal.conceptHint,
      actualText: feedbackModal.actualText,
      expectedText: feedbackModal.expectedText,
    };
    const nextUsed = hintRetryUsed.value + 1;
    feedbackModal.hintQuestion = buildRetryHintQuestion(parts, nextUsed);
    hintRetryUsed.value = nextUsed;
    if (feedbackModal.attemptId) {
      await sendChoice(feedbackModal.attemptId, "no", {
        click_type: "hint_retry",
        hint_retry_count: nextUsed,
      });
    }
  } catch (err) {
    console.error("handleRetryHint error:", err);
  } finally {
    isRegeneratingHint.value = false;
  }
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
  primaryWrongIndex.value = (parts.focusIndex != null ? Number(parts.focusIndex) : null);
  feedbackModal.open = true;
  feedbackModal.stage = "detail";
  feedbackModal.headline = buildFeedbackHeadline(parts);
  feedbackModal.locationText = buildFeedbackLocation(parts);
  feedbackModal.hintQuestion = "";
  feedbackModal.slotLabel = parts.slotLabel;
  feedbackModal.diagnosis = parts.diagnosis;
  feedbackModal.conceptHint = parts.conceptHint;
  feedbackModal.reflectionQuestions = parts.reflectionQuestions;
  feedbackModal.impactHint = parts.impactHint;
  feedbackModal.source = String(parts.source || "default");
  feedbackModal.firstHint = String(parts.firstHint || "");
  feedbackModal.secondHint = String(parts.secondHint || "");
  feedbackModal.possibleCauses = Array.isArray(parts.possibleCauses) ? parts.possibleCauses : [];
  feedbackModal.actualText = String(parts.actualText || "");
  feedbackModal.expectedText = String(parts.expectedText || "");
  feedbackModal.reviewMode = String(parts.reviewMode || "subtitle_alignment");
  feedbackModal.subtitleHealth = parts.subtitleHealth || null;
  feedbackModal.chapterRecommendation = parts.chapterRecommendation || null;
  feedbackModal.chapterLabel = buildChapterLabel(parts.chapterRecommendation);
  feedbackModal.reviewRange = buildReviewRangeText({ ...parts, chapterLabel: feedbackModal.chapterLabel });
  feedbackModal.reviewFocus = buildFeedbackReviewFocus(parts);
  feedbackModal.hintOpen = false;
  feedbackModal.hintLoading = false;
  feedbackModal.hintLoaded = false;
  feedbackModal.hintError = "";
  feedbackModal.hintLimitReached = false;
  feedbackModal.reviewOpen = false;
  feedbackModal.start = parts.startSec;
  feedbackModal.end = parts.endSec;
  feedbackModal.chapterStart = getChapterBoundary(parts.chapterRecommendation, ["start", "start_sec"]);
  feedbackModal.chapterEnd = getChapterBoundary(parts.chapterRecommendation, ["end", "end_sec"]);
  feedbackModal.jumpVideoId = String(r?.jump?.video_id || "");
  feedbackModal.attemptId = String(r?.attempt_id || "");
  feedbackModal.attemptV2Id = String(r?.attempt_v2_id || "");
  feedbackModal.attemptNo = Number.isFinite(Number(r?.attempt_no)) ? Number(r.attempt_no) : null;
  feedbackModal.reviewAttemptId = String(r?.review_attempt_id || r?.attempt_id || "");
  feedbackModal.taskId = String(taskId || "");
  feedbackModal.targetConcept = String(r?.target_concept || currentTargetConcept() || "");
  feedbackModal.hintClicked = false;
  feedbackModal.videoClicked = false;
  feedbackModal.debugText = "";
  hintRetryUsed.value = 0;
  isRegeneratingHint.value = false;

  // if (import.meta.env.DEV) {
  //   const src = String(r?.segment_source || "");
  //   const concept = String(r?.segment_concept || "");
  //   const wrongType = String(r?.wrong_type || "");
  //   const conceptTag = String(r?.concept_tag || "");
  //   const wrongIdx = Number.isFinite(Number(r?.wrong_index)) ? Number(r.wrong_index) : null;
  //   const traceArr = Array.isArray(r?.alignment_trace) ? r.alignment_trace : [];
  //   const traceText = traceArr.map((x) => {
  //     const step = String(x?.step || "?");
  //     const fbSrc = String(x?.fallback_source || "").trim();
  //     const reason = String(x?.reason || x?.segment_source || "");
  //     if (fbSrc) {
  //       return `${step}:${reason || "n/a"}(${fbSrc})`;
  //     }
  //     return reason ? `${step}:${reason}` : step;
  //   }).join(" | ");
  //   feedbackModal.debugText = `debug src=${src || "n/a"}, concept=${concept || "n/a"}, wrong_type=${wrongType || "n/a"}, concept_tag=${conceptTag || "n/a"}, wrong_index=${wrongIdx != null ? wrongIdx : "n/a"}${traceText ? `\ntrace ${traceText}` : ""}`;
  // }

  debugFeedback({
    modalOpen: true,
    taskId: String(taskId || ""),
    primaryWrongIndex: primaryWrongIndex.value,
    wrongIndices: (wrongIndices.value || []).join(","),
    modalDiagnosis: feedbackModal.diagnosis,
    modalSlotLabel: feedbackModal.slotLabel,
  });
}

function applyAiHintToFeedbackModal(data = {}) {
  const detail = (data?.ai_feedback_detail && typeof data.ai_feedback_detail === "object")
    ? data.ai_feedback_detail
    : {};

  aiFeedbackDetail.value = detail;
  aiConceptExplanation.value = String(detail?.concept_explanation || detail?.concept_hint || "");
  aiPossibleCauses.value = Array.isArray(detail?.possible_causes) ? detail.possible_causes : [];
  aiImpact.value = String(detail?.impact || "");
  aiGuidingQuestion.value = String(detail?.guiding_question || "");

  feedbackModal.source = String(data?.source || "ai");
  feedbackModal.conceptHint = String(detail?.concept_hint || detail?.concept_explanation || feedbackModal.conceptHint || "");
  feedbackModal.firstHint = String(detail?.first_hint || data?.hint || feedbackModal.firstHint || "");
  feedbackModal.secondHint = String(detail?.second_hint || feedbackModal.secondHint || "");
  feedbackModal.possibleCauses = Array.isArray(detail?.possible_causes) ? detail.possible_causes : feedbackModal.possibleCauses;
  feedbackModal.reflectionQuestions = Array.isArray(detail?.reflection_questions) ? detail.reflection_questions : feedbackModal.reflectionQuestions;
  feedbackModal.impactHint = String(detail?.impact || feedbackModal.impactHint || "");

  feedbackModal.hintQuestion = String(
    detail?.first_hint
    || data?.hint
    || detail?.guiding_question
    || detail?.concept_explanation
    || detail?.concept_hint
    || "請先確認這一格在程式流程中的角色與位置。"
  );
}

async function loadAiHintForModal() {
  if (feedbackModal.hintLoaded || feedbackModal.hintLoading) return;
  if (!feedbackModal.attemptId) {
    feedbackModal.hintQuestion = buildHintQuestion({
      firstHint: feedbackModal.firstHint,
      reflectionQuestions: feedbackModal.reflectionQuestions,
      slotLabel: feedbackModal.slotLabel,
    });
    feedbackModal.hintLoaded = true;
    return;
  }

  feedbackModal.hintLoading = true;
  feedbackModal.hintError = "";
  feedbackModal.hintQuestion = "提示產生中...";

  try {
    const res = await fetch(`${API_BASE}/api/parsons/hint`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        attempt_id: feedbackModal.attemptId,
        task_id: feedbackModal.taskId,
        student_id: String(studentId.value || ""),
        participant_id: String(participantId.value || ""),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) {
      throw new Error(data?.message || "提示產生失敗");
    }
    applyAiHintToFeedbackModal(data);
    feedbackModal.hintLoaded = true;
  } catch (err) {
    feedbackModal.hintError = err?.message || "提示產生失敗";
    feedbackModal.hintQuestion = "AI 提示暫時無法產生，請稍後再試。";
  } finally {
    feedbackModal.hintLoading = false;
  }
}

function feedbackLearningContext(metadata) {
  return {
    task_id: feedbackModal.taskId || currentTaskId(),
    attempt_id: feedbackModal.attemptV2Id || null,
    attempt_no: feedbackModal.attemptNo,
    target_concept: feedbackModal.targetConcept || currentTargetConcept(),
    metadata,
  };
}

async function closeHintReview(closeMethod) {
  if (!feedbackModal.hintOpen) return false;
  feedbackModal.hintOpen = false;
  await logLearningEvent("review_close", feedbackLearningContext({
    review_type: "ai_hint",
    close_method: closeMethod,
  }));
  return true;
}

async function closeHintAndReturnToTask(closeMethod) {
  const learningContext = feedbackLearningContext({
    review_type: "ai_hint",
    return_method: closeMethod,
  });
  const closed = await closeHintReview(closeMethod);
  if (!closed) return;
  if (feedbackModal.attemptId) {
    await sendChoice(feedbackModal.attemptId, "no");
  }
  feedbackModal.open = false;
  await logLearningEvent("return_to_task", learningContext);
}

async function dismissFeedbackModal() {
  const hadOpenHint = feedbackModal.hintOpen;
  const learningContext = feedbackLearningContext({
    review_type: "ai_hint",
    close_method: "click_blank_area",
  });
  if (hadOpenHint) {
    feedbackModal.hintOpen = false;
    await logLearningEvent("review_close", learningContext);
  }
  if (feedbackModal.attemptId) {
    await sendChoice(feedbackModal.attemptId, "no");
  }
  feedbackModal.open = false;
  feedbackModal.stage = "choice";
  feedbackModal.hintOpen = false;
  feedbackModal.hintLoading = false;
  feedbackModal.hintLoaded = false;
  feedbackModal.hintError = "";
  feedbackModal.hintLimitReached = false;
  feedbackModal.reviewOpen = false;
  feedbackModal.attemptV2Id = "";
  feedbackModal.attemptNo = null;
  feedbackModal.targetConcept = "";
  feedbackModal.hintClicked = false;
  feedbackModal.videoClicked = false;
  feedbackModal.debugText = "";
  feedbackModal.source = "default";
  feedbackModal.reviewMode = "subtitle_alignment";
  feedbackModal.subtitleHealth = null;
  feedbackModal.chapterRecommendation = null;
  feedbackModal.chapterLabel = "";
  feedbackModal.chapterStart = null;
  feedbackModal.chapterEnd = null;
  feedbackModal.firstHint = "";
  feedbackModal.secondHint = "";
  feedbackModal.possibleCauses = [];
  feedbackModal.actualText = "";
  feedbackModal.expectedText = "";
  hintRetryUsed.value = 0;
  isRegeneratingHint.value = false;
  if (hadOpenHint) {
    await logLearningEvent("return_to_task", {
      ...learningContext,
      metadata: { review_type: "ai_hint", return_method: "click_blank_area" },
    });
  }
}

async function goReviewFromModal() {
  if (!feedbackModal.jumpVideoId) {
    feedbackModal.open = false;
    return;
  }

  await sendChoice(feedbackModal.attemptId, "yes");
  savePracticeState({ attempt_id: feedbackModal.attemptId || "" });

  const useChapterMode = String(feedbackModal.reviewMode || "").trim() === "chapter_recommendation";
  const start = useChapterMode && Number.isFinite(Number(feedbackModal.chapterStart))
    ? Number(feedbackModal.chapterStart)
    : (feedbackModal.start ?? 0);
  const end = useChapterMode && Number.isFinite(Number(feedbackModal.chapterEnd))
    ? Number(feedbackModal.chapterEnd)
    : (feedbackModal.end ?? 0);
  const jumpVideoId = feedbackModal.jumpVideoId;
  const attemptId = feedbackModal.reviewAttemptId;
  const taskId = feedbackModal.taskId;

  feedbackModal.open = false;
  feedbackModal.hintClicked = false;
  feedbackModal.videoClicked = false;

  router.push({
    path: `/learn/video/${jumpVideoId}`,
    query: {
      start,
      end,
      review_mode: useChapterMode ? "chapter_recommendation" : "subtitle_alignment",
      attempt_id: String(attemptId || ""),
      task_id: String(taskId || ""),
      level: route.query.level ? String(route.query.level) : "L1",
    },
  });
}

async function onToggleHintOpen() {
  if (feedbackModal.hintOpen) {
    await closeHintAndReturnToTask("click_ai_hint_again");
    return;
  }

  feedbackModal.stage = "detail";
  const openEvent = await logLearningEvent("review_open", feedbackLearningContext({
    review_type: "ai_hint",
    trigger_method: "click_ai_hint",
    button_name: "AI提示",
  }));
  const hintMetadata = openEvent?.metadata || {};
  if (hintMetadata.hint_limit_reached === true) {
    feedbackModal.hintOpen = false;
    feedbackModal.hintLimitReached = true;
    return;
  }
  feedbackModal.hintOpen = true;
  feedbackModal.hintLimitReached = Number(hintMetadata.hint_no) >= Number(hintMetadata.max_hint_count || 2);
  if (!feedbackModal.hintClicked && feedbackModal.attemptId) {
    feedbackModal.hintClicked = true;
    sendChoice(feedbackModal.attemptId, "no", { click_type: "hint" }).catch(() => {});
  }
  await loadAiHintForModal();
}

async function onToggleReviewOpen() {
  const nextOpen = !feedbackModal.reviewOpen;
  feedbackModal.reviewOpen = nextOpen;
  if (nextOpen && !feedbackModal.videoClicked && feedbackModal.attemptId) {
    feedbackModal.videoClicked = true;
    await sendChoice(feedbackModal.attemptId, "no", { click_type: "video" });
  }
}

async function openReviewFromHint() {
  if (!feedbackModal.reviewOpen) {
    feedbackModal.reviewOpen = true;
  }
  if (!feedbackModal.videoClicked && feedbackModal.attemptId) {
    feedbackModal.videoClicked = true;
    await sendChoice(feedbackModal.attemptId, "no", { click_type: "video" });
  }
}

async function sendChoice(attempt_id, choice, extra = {}) {
  if (!attempt_id) return;
  try {
    await fetch(`${API_BASE}/api/parsons/review_choice`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        attempt_id,
        student_id: (localStorage.getItem("student_id") || ""),
        student_choice: choice,
        ...extra,
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
      wrong_indices: Array.isArray(wrongIndices.value)
        ? wrongIndices.value.map((v) => Number(v)).filter((v) => Number.isFinite(v))
        : [],
      primary_wrong_index: Number.isFinite(Number(primaryWrongIndex.value))
        ? Number(primaryWrongIndex.value)
        : null,
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

    // 回到同題時保留上次錯誤標記，直到學生修正正確為止。
    const restoredWrong = Array.isArray(st.wrong_indices)
      ? st.wrong_indices.map((v) => Number(v)).filter((v) => Number.isFinite(v))
      : [];
    wrongIndices.value = restoredWrong;

    const restoredPrimary = Number(st.primary_wrong_index);
    if (Number.isFinite(restoredPrimary)) {
      primaryWrongIndex.value = restoredPrimary;
    } else {
      primaryWrongIndex.value = restoredWrong.length ? restoredWrong[0] : null;
    }

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
  if (state.submitting || state.noTask) return;
  state.submitting = true;
  state.err = "";
  result.value = null;
  wrongIndices.value = [];
  primaryWrongIndex.value = null;

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

      const submittedAt = new Date();
      const startedAtIso = testMeta.started_at_ms
        ? new Date(Number(testMeta.started_at_ms)).toISOString()
        : null;
      const duration_sec = testMeta.started_at_ms
        ? Math.max(0, Math.round((submittedAt.getTime() - Number(testMeta.started_at_ms)) / 1000))
        : 0;

      const body = {
        session_id: learningSessionId,
        page: "parsons",
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
        started_at: startedAtIso,
        submitted_at: submittedAt.toISOString(),
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

      if (!res.ok) {
        state.err = r?.message || "答案送出失敗，請稍後再試。";
        result.value = { is_correct: false, feedback: state.err };
        return;
      }

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
    // 一般練習模式：送出時只做系統判斷；AI 提示由「查看提示」按鈕延後載入
    // ==============================
    const task_id = task.value?.task_id || task.value?._id;
    if (!task_id) {
      state.err = "尚未載入題目，無法送出。";
      result.value = { is_correct: false, feedback: state.err };
      return;
    }

    const submittedAt = new Date();
    const startedAtMs = Number(testStartedAt.value || 0);
    const startedAtIso = startedAtMs
      ? new Date(startedAtMs).toISOString()
      : null;
    const duration_sec = startedAtMs
      ? Math.max(0, Math.round((submittedAt.getTime() - startedAtMs) / 1000))
      : null;

    const body = {
      session_id: learningSessionId,
      page: "parsons",
      task_id,
      answer_ids: answer_ids.value,
      answer_lines: buildAnswerLines(),
      video_id: String(videoId.value || ""),
      level: route.query.level ? String(route.query.level) : "L1",
      review_attempt_id: review_attempt_id.value,
      student_id: String(studentId.value || ""),
      participant_id: String(participantId.value || ""),
      started_at: startedAtIso,
      submitted_at: submittedAt.toISOString(),
      duration_sec,
    };

    const res = await fetch(`${API_BASE}/api/parsons/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const r = await res.json();

    if (!res.ok) {
      state.err = r?.message || "答案送出失敗，請稍後再試。";
      result.value = { is_correct: false, feedback: state.err };
      return;
    }

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

    const parts = buildWrongFeedbackParts(r || {});
    primaryWrongIndex.value = (parts.focusIndex != null ? Number(parts.focusIndex) : null);

    if (r?.ok && r?.is_correct === true) {
      localStorage.removeItem(PRACTICE_STATE_KEY);
      localStorage.removeItem(RESTORE_ONCE_KEY);
      localStorage.removeItem(RESTORE_MSG_KEY);
      await openSuccessModal(r);
    }

    if (r?.ok && r?.is_correct === false) {
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

  const hideSemanticZh = !!t.hide_semantic_zh;

  let pool = Array.isArray(t.pool) ? t.pool : [];
  const rawDist = Array.isArray(t.distractor_blocks) ? t.distractor_blocks : [];
  const hiddenDistractorIds = new Set(
    rawDist
      .filter((b) => b && b.enabled === false)
      .map((b) => String(b?.id ?? b?._id ?? ""))
      .filter(Boolean)
  );
  if (!pool.length) {
    const core = Array.isArray(t.solution_blocks) ? t.solution_blocks : [];
    const dist = rawDist.filter((b) => b?.enabled !== false);
    const blocks = Array.isArray(t.blocks) ? t.blocks : [];
    pool = (core.length || dist.length) ? [...core, ...dist] : blocks;
  }

  pool = (pool || []).map((b, i) => ({
    id: b?.id != null ? String(b.id) : `b${i + 1}`,
    text: b?.text != null ? String(b.text) : "",
    type: b?.type ? String(b.type) : (b?.is_distractor ? "distractor" : "core"),
    meaning_zh: hideSemanticZh ? "" : (b?.meaning_zh != null ? String(b.meaning_zh) : (b?.semantic_zh != null ? String(b.semantic_zh) : "")),
    ...b,
  }));

  if (hiddenDistractorIds.size) {
    pool = pool.filter((b) => !hiddenDistractorIds.has(String(b?.id ?? "")));
  }

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
    const _poolMap = new Map((pool || []).map((b) => [String(b?.id ?? ""), b]));
    const _solMap = new Map(((Array.isArray(t.solution_blocks) ? t.solution_blocks : []) || []).map((b) => [String(b?.id ?? ""), b]));

    slots = slots.map((s, idx) => ({
      ...s,
      slot: s?.slot != null ? String(s.slot) : `s${idx + 1}`,
      // 學生端避免暴露 template_slots 的語意提示，統一顯示中性槽位名稱。
      label: `第 ${idx + 1} 格`,
      expected_meaning_zh: (() => {
        if (hideSemanticZh) return "";
        const eid = s?.expected_id != null ? String(s.expected_id) : "";
        if (!eid) return "";
        const fromSol = _solMap.get(eid) || {};
        const fromPool = _poolMap.get(eid) || {};
        return String(
          fromSol.meaning_zh || fromSol.semantic_zh || fromSol.zh ||
          fromPool.meaning_zh || fromPool.semantic_zh || fromPool.zh ||
          ""
        );
      })(),
    }));
  }

  t.hide_semantic_zh = hideSemanticZh;
  t.template_slots = slots;
  return { taskObj: t, pool, slots };
}

// ====== 載入題目 ======
async function loadTask() {
  state.loading = true;
  state.err = "";
  state.noTask = false;

  resetFilled();
  testStartedAt.value = Date.now();

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
      teacherForcedHideSemantic.value = !!norm.taskObj?.hide_semantic_zh;
      // 前後測不顯示中文提示：清空 label
      templateSlots.value = (norm.slots || []).map((s) => ({ ...s, label: "" }));

      await bindBlankFocusHandlers();
      await recordTaskOpen();
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
    teacherForcedHideSemantic.value = !!norm.taskObj?.hide_semantic_zh;
    templateSlots.value = norm.slots;

    const restored = restorePracticeStateIfMatch(task.value);

    await bindBlankFocusHandlers();
    await recordTaskOpen();

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
onMounted(async () => {
  await initializeLearningLogging();
  await loadTask();
});

onMounted(() => {
  _docKeydownHandler = (e) => {
    const tag = String(e.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;

    if (e.key === "Tab") {
      const k = pickTargetSlotKey();
      if (!k) return;
      e.preventDefault();
      const cur = clampIndentPx(slotIndentLevel[String(k)] || 0);
      const next = cur + INDENT_STEP_PX;
      const wrapped = next > (INDENT_STEP_PX * INDENT_MAX_LEVEL);
      const changed = wrapped
        ? (() => {
            slotIndentLevel[String(k)] = 0;
            return cur !== 0;
          })()
        : indentBoxPx(k, +INDENT_STEP_PX);
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

.cloze-row.affected .hint{
  background: linear-gradient(90deg, #ffd888 0%, #ffc55a 100%);
  color: #553a00;
}
.cloze-row.affected .blank{
  border-color: #d97706 !important;
  box-shadow: 0 0 0 2px rgba(217, 119, 6, .24);
}

.hint-affected-icon{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-right: 6px;
  border-radius: 999px;
  background: #b45309;
  color: #fff7ed;
  font-size: 12px;
  font-weight: 900;
  line-height: 1;
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

.fb-divider{
  border-top: 1px dashed #cbd5e1;
  margin: 10px 0;
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

.fb-note{
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 10px;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  color: #7c2d12;
  font-size: 14px;
  font-weight: 800;
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

.fb-focus{
  margin-top: 8px;
  font-size: 14px;
  font-weight: 800;
  color: #1e3a8a;
}

.fb-more{
  margin-top: 10px;
  font-size: 14px;
  font-weight: 800;
  color: #334155;
}

.fb-actions{
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.fb-actions.pick{
  justify-content: flex-start;
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
