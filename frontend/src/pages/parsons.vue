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
            :class="{
              wrong: isPrimaryWrong(idx),
              affected: isAffectedWrong(idx),
              'c-wrong': isCStrategy && isWrongSlot(idx),
            }"
          >

          <div class="hint" v-if="shouldShowSlotSemantic(slot, idx)">
            <span
              v-if="isCStrategy ? isWrongSlot(idx) : isAffectedWrong(idx)"
              class="hint-affected-icon"
              aria-hidden="true"
          >
          </span>

            {{ idx + 1 }}. {{ semanticTextForSlot(slot) }}
          </div>

          <div
            v-if="hasSlotAdjustmentTags(idx)"
            class="slot-error-tags"
          >
            <span
              v-for="tag in slotAdjustmentTags(idx)"
              :key="tag.key"
              class="slot-error-tag"
              :class="tag.kind"
            >
              {{ tag.label }}
            </span>
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

    <div v-if="firstHintPanel.visible" class="first-hint-panel">
      <div class="first-hint-title">❌ {{ firstHintPanel.headline }}</div>
      <div class="first-hint-meta">
        <span>需調整區塊：{{ firstHintPanel.wrongCount }} 個</span>
        <span>順序問題：{{ firstHintPanel.sequenceCount }} 個</span>
        <span>縮排問題：{{ firstHintPanel.indentationCount }} 個</span>
      </div>
      <div class="first-hint-text">{{ firstHintPanel.text }}</div>
    </div>

    <button
      v-if="showFloatingAiHintButton"
      class="floating-ai-hint"
      @click="reopenAiHintFromFloating"
      type="button"
    >
      <span v-if="showSecondHintFloatingBadge" class="floating-ai-hint-badge">1</span>
      <span class="floating-ai-hint-label">{{ floatingAiHintLabel }}</span>
      查看 AI 提示
    </button>

    <!-- 錯誤回饋 Modal（練習模式） -->
    <div
      v-if="feedbackModal.open"
      class="fb-modal-backdrop"
    >
      <div class="fb-modal" role="dialog" aria-modal="true" aria-label="作答回饋">
        <template v-if="feedbackModal.mode === 'ai_hint_flow'">
          <button
            class="ai-hint-close-icon"
            type="button"
            aria-label="關閉 AI 提示"
            @click="closeAiHintModal"
          >
            ×
          </button>
          <div class="fb-title ai-hint-title">💡 AI 提示</div>

          <div class="ai-hint-meta-card">
            <div v-if="activeAiHintConceptScope" class="ai-hint-meta-row">
              <span class="ai-hint-meta-label">概念範圍：</span>
              <span class="ai-hint-meta-value">{{ activeAiHintConceptScope }}</span>
            </div>
            <div class="ai-hint-meta-row">
              <span class="ai-hint-meta-label">提示層級：</span>
              <span class="ai-hint-meta-value">{{ activeAiHintScopeLabel }}</span>
            </div>
          </div>

          <div class="fb-section">
            <div class="fb-label">AI 提示</div>
            <div class="fb-text ai-hint-content">
              {{ activeAiHintText || feedbackModal.hintError || "AI 提示載入中..." }}
            </div>
            <div class="fb-note">
              提示次數：{{ activeAiHintDisplayNo }} / 2
            </div>
            <div v-if="feedbackModal.hintError" class="ai-hint-error">
              {{ feedbackModal.hintError }}
            </div>
          </div>

          <div class="fb-actions">
            <button
              class="btn ghost"
              :disabled="feedbackModal.hintLoading || feedbackModal.secondHintLoading"
              @click="handleAiHintAction"
            >
              {{ aiHintActionLabel }}
            </button>
            <button class="btn submit" @click="returnToFixFromHint">返回題目修正</button>
          </div>
        </template>

        <template v-else-if="feedbackModal.mode === 'fixed_semantic_feedback'">
          <div class="fb-title first-error-title">❌ {{ firstHintPanel.headline }}</div>

          <div class="fb-section first-error-section fixed-feedback-section">
            <div class="fb-label">固定錯誤提示</div>
            <div class="first-hint-meta in-modal">
              <span>需調整區塊：{{ firstHintPanel.wrongCount }} 個</span>
              <span>順序問題：{{ firstHintPanel.sequenceCount }} 個</span>
              <span>縮排問題：{{ firstHintPanel.indentationCount }} 個</span>
            </div>
            <div class="fb-text first-error-text">
              {{ firstHintPanel.text }}
            </div>
            <div class="fb-note first-error-note">
              返回題目後，紅框旁會標示「順序需要調整」或「縮排需要調整」。
            </div>
          </div>

          <div class="fb-actions">
            <button class="btn submit" @click="returnToFixFromFixedFeedback">
              返回題目修正
            </button>
          </div>
        </template>

        <template v-else-if="feedbackModal.mode === 'first_system_hint'">
          <div class="fb-title first-error-title">❌ {{ firstHintPanel.headline }}</div>

          <div class="fb-section first-error-section">
            <div class="fb-label">第一次錯誤提示</div>
            <div class="first-hint-meta in-modal">
              <span>需調整區塊：{{ firstHintPanel.wrongCount }} 個</span>
              <span>順序問題：{{ firstHintPanel.sequenceCount }} 個</span>
              <span>縮排問題：{{ firstHintPanel.indentationCount }} 個</span>
            </div>
            <div class="fb-text first-error-text">
              {{ firstHintPanel.text }}
            </div>
            <div class="fb-note first-error-note">
              返回題目後，紅框旁會標示「順序需要調整」或「縮排需要調整」。
            </div>
          </div>

          <div class="fb-actions">
            <button class="btn submit" @click="returnToFixFromFirstHint">返回題目修正</button>
          </div>
        </template>

        <template v-else-if="feedbackModal.mode === 'system_recheck'">
          <div class="fb-title first-error-title">❌ {{ firstHintPanel.headline }}</div>

          <div class="fb-section first-error-section">
            <div class="fb-label">錯誤提示</div>
            <div class="first-hint-meta in-modal">
              <span>需調整區塊：{{ firstHintPanel.wrongCount }} 個</span>
              <span>順序問題：{{ firstHintPanel.sequenceCount }} 個</span>
              <span>縮排問題：{{ firstHintPanel.indentationCount }} 個</span>
            </div>
            <div class="fb-text first-error-text">
              {{ firstHintPanel.text }}
            </div>
            <div class="fb-note first-error-note">
              返回題目後，紅框旁會標示「順序需要調整」或「縮排需要調整」。
            </div>
          </div>

          <div v-if="showSecondHintModalPrompt" class="second-hint-reminder in-modal">
            <div class="second-hint-reminder-copy">
              <div class="second-hint-reminder-title">{{ secondHintReminderTitle }}</div>
              <div class="second-hint-reminder-text">{{ secondHintReminderText }}</div>
            </div>
            <button
              class="btn submit second-hint-reminder-btn"
              type="button"
              :disabled="feedbackModal.secondHintLoading"
              @click="openSecondHintFromReminder('click_result_second_hint')"
            >
              {{ feedbackModal.secondHintLoading ? secondHintReminderLoadingLabel : secondHintReminderButtonLabel }}
            </button>
          </div>

          <div class="fb-actions">
            <button class="btn submit" @click="returnToFixFromSystemRecheck">
              返回題目修正
            </button>
          </div>
        </template>

        <template v-else-if="feedbackModal.stage === 'choice'">
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
import { useRoute, useRouter, onBeforeRouteLeave } from "vue-router";
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

function currentUnitId() {
  return String(
    task.value?.unit_id
    || task.value?.unit
    || route.query.unit_id
    || route.query.unit
    || route.query.level
    || ""
  ).trim() || null;
}

function currentTargetConcept() {
  const raw = task.value?.target_concept ?? task.value?.concept;
  if (raw != null && String(raw).trim()) return String(raw).trim();
  const tags = Array.isArray(task.value?.tags) ? task.value.tags : [];
  return tags.length && String(tags[0] || "").trim() ? String(tags[0]).trim() : null;
}

function authHeaders(base = {}) {
  const headers = { ...base };
  const token = String(localStorage.getItem("token") || "").trim();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function logLearningEvent(eventType, extra = {}) {
  if (!studentId.value) return null;
  const headers = authHeaders({ "Content-Type": "application/json" });
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
    const sendBody = async (payload) => fetch(`${API_BASE}/api/learning_logs`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    let response = await sendBody(body);
    if (!response.ok && response.status === 400 && eventType === "view_hint") {
      response = await sendBody({ ...body, event_type: "review_open" });
    } else if (!response.ok && response.status === 400 && eventType === "hide_hint") {
      response = await sendBody({ ...body, event_type: "review_close" });
    }
    if (!response.ok) {
      console.warn("learning log write failed", eventType, response.status);
      return null;
    }
    return await response.json().catch(() => null);
  } catch (err) {
    console.warn("learning log write failed", eventType, err);
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

let enteredParsonsTaskLogKey = "";

async function recordEnterParsonsTaskFromVideo() {
  const fromVideoId = String(route.query.from_video_id || videoId.value || "").trim();
  const watchSessionId = String(route.query.watch_session_id || "").trim();
  if (!fromVideoId && !watchSessionId) return;

  const taskId = currentTaskId();
  const key = `${watchSessionId}|${fromVideoId}|${taskId || ""}`;
  if (key === enteredParsonsTaskLogKey) return;
  enteredParsonsTaskLogKey = key;

  await logLearningEvent("enter_parsons_task", {
    task_id: taskId,
    unit_id: currentUnitId(),
    question_type: "parsons",
    from_video_id: fromVideoId || null,
    from_video_title: String(route.query.from_video_title || "").trim() || null,
    watch_session_id: watchSessionId || null,
    metadata: {
      from_video_id: fromVideoId || null,
      from_video_title: String(route.query.from_video_title || "").trim() || null,
      watch_session_id: watchSessionId || null,
    },
  });
}

// AI 回饋（練習模式用）
const aiFeedbackDetail = ref(null);
const aiConceptExplanation = ref("");
const aiPossibleCauses = ref([]);
const aiImpact = ref("");
const aiGuidingQuestion = ref("");

const testMeta = reactive({
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

// 回饋策略：舊資料沒有欄位時預設 B，避免影響原本功能
const feedbackStrategy = computed(() => {
  const strategy = String(
    task.value?.feedback_strategy || "B"
  ).trim().toUpperCase();

  // 後端未來會用 Z 分配寫入 A/B/C。
  // A：AI 回饋 + 反思題；B：第二次錯誤後 AI 回饋；C：固定中文回饋。
  // 目前只先完成 C 的特殊分支，A 暫時不改動既有 B 流程。
  return ["A", "B", "C"].includes(strategy) ? strategy : "B";
});

const isCStrategy = computed(() => {
  return feedbackStrategy.value === "C";
});

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
  // 保存最新縮排
  savePracticeState({
    task_status: "editing",
    save_reason: "indent_change",
  });
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
const wrongSlotIssueMap = ref({});

// ====== UI 狀態 ======
const state = reactive({
  loading: false,
  submitting: false,
  noTask: false,
  err: "",
});

const feedbackModal = reactive({
  open: false,
  mode: "legacy",
  stage: "choice",
  headline: "",
  locationText: "",
  hintQuestion: "",
  slotLabel: "",
  diagnosis: "",
  errorClass: "",
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
  reviewOpenLogId: "",
  taskId: "",
  targetConcept: "",
  hintClicked: false,
  videoClicked: false,
  source: "default",
  aiDiagnosisSummary: "",
  hintRecord: null,
  hintId: "",
  firstSystemHintText: "",
  aiHint1Text: "",
  aiHint1Meta: {},
  aiHint2Text: "",
  aiHint2Meta: {},
  activeAiHintNo: 1,
  secondHintLoading: false,
  secondHintNoticeVisible: false,
  secondHintReminderShownLogged: false,
  secondHintReminderResolved: false,
  firstHint: "",
  secondHint: "",
  possibleCauses: [],
  actualText: "",
  expectedText: "",
  debugText: "",
});

const firstHintPanel = reactive({
  visible: false,
  headline: "",
  text: "",
  wrongCount: 0,
  sequenceCount: 0,
  indentationCount: 0,
  wrongPositionText: "紅色標記的位置",
  errorTypeText: "待檢查",
  hintRecord: null,
});

const showFloatingAiHintButton = computed(() => {
  return Boolean(
    !isCStrategy.value &&
    !feedbackModal.open &&
    feedbackModal.hintRecord &&
    feedbackModal.aiHint1Text
  );
});

const hasSavedSecondAiHint = computed(() => Boolean(
  feedbackModal.hintRecord &&
  feedbackModal.aiHint1Text &&
  feedbackModal.aiHint2Text
));

const hasPendingSecondAiHint = computed(() => Boolean(
  !isCStrategy.value &&
  feedbackModal.hintRecord &&
  feedbackModal.aiHint1Text &&
  !feedbackModal.aiHint2Text &&
  (
    feedbackModal.secondHintNoticeVisible ||
    (
      result.value &&
      result.value.is_correct === false &&
      Number(feedbackModal.attemptNo || result.value?.attempt_no || 0) >= 2
    )
  )
));

const showSecondHintModalPrompt = computed(() => Boolean(
  feedbackModal.open &&
  feedbackModal.mode === "system_recheck" &&
  hasPendingSecondAiHint.value
));

const showSecondHintFloatingBadge = computed(() => Boolean(
  showFloatingAiHintButton.value &&
  hasPendingSecondAiHint.value
));

const secondHintReminderTitle = "\u4f60\u9084\u6709 1 \u6b21 AI \u63d0\u793a\u67e5\u770b\u6a5f\u6703\u3002";
const secondHintReminderText = "\u76ee\u524d\u932f\u8aa4\u4f4d\u7f6e\u5df2\u6a19\u793a\u5b8c\u6210\uff0c\u53ef\u4ee5\u67e5\u770b\u66f4\u805a\u7126\u7684\u7b2c\u4e8c\u6b21\u63d0\u793a\u5f8c\u518d\u4fee\u6b63\u3002";
const secondHintReminderButtonLabel = "\u67e5\u770b\u7b2c\u4e8c\u6b21 AI \u63d0\u793a";
const secondHintReminderLoadingLabel = "\u7b2c\u4e8c\u6b21 AI \u63d0\u793a\u8f09\u5165\u4e2d...";

const floatingAiHintLabel = computed(() => {
  if (hasSavedSecondAiHint.value) return "\u67e5\u770b\u5df2\u4fdd\u5b58\u63d0\u793a";
  if (hasPendingSecondAiHint.value) return "\u7b2c 2 \u6b21\u63d0\u793a\u53ef\u67e5\u770b";
  return "\u67e5\u770b AI \u63d0\u793a";
});

const generatedAiHintCount = computed(() => {
  const stored = Number(
    feedbackModal.hintRecord?.hint_generation_count
    ?? feedbackModal.hintRecord?.ai_hint_generation_count
    ?? 0
  );
  const inferred = (feedbackModal.aiHint1Text ? 1 : 0) + (feedbackModal.aiHint2Text ? 1 : 0);
  return Math.min(2, inferred || stored);
});

const activeAiHintText = computed(() => {
  return feedbackModal.activeAiHintNo === 2
    ? feedbackModal.aiHint2Text
    : feedbackModal.aiHint1Text;
});

const topLevelRemovedAiHintMetaKeys = new Set([
  "wrong_index",
  "concept",
  "concept_tag",
  "concept_scope",
]);

const alwaysRemovedAiHintMetaKeys = new Set([
  "subtitle_range",
  "subtitle_ranges",
  "subtitle_broad_range",
  "subtitle_narrow_range",
  "subtitle_range_available",
  "subtitle_scope",
]);

function sanitizeHintMeta(meta, depth = 0) {
  if (!meta || typeof meta !== "object" || Array.isArray(meta)) return {};
  const cleaned = {};
  for (const [key, value] of Object.entries(meta)) {
    if (alwaysRemovedAiHintMetaKeys.has(key)) continue;
    if (depth === 0 && topLevelRemovedAiHintMetaKeys.has(key)) continue;
    if (Array.isArray(value)) {
      cleaned[key] = value.map((item) => (
        item && typeof item === "object" && !Array.isArray(item)
          ? sanitizeHintMeta(item, depth + 1)
          : item
      ));
    } else if (value && typeof value === "object") {
      cleaned[key] = sanitizeHintMeta(value, depth + 1);
    } else {
      cleaned[key] = value;
    }
  }
  return cleaned;
}

function firstNonEmptyHintMeta(...candidates) {
  for (const candidate of candidates) {
    const meta = sanitizeHintMeta(candidate);
    if (Object.keys(meta).length) return meta;
  }
  return {};
}

const activeAiHintMeta = computed(() => {
  return feedbackModal.activeAiHintNo === 2
    ? firstNonEmptyHintMeta(
        feedbackModal.aiHint2Meta,
        feedbackModal.hintRecord?.ai_hint_2_meta
      )
    : firstNonEmptyHintMeta(
        feedbackModal.aiHint1Meta,
        feedbackModal.hintRecord?.ai_hint_1_meta
      );
});

const activeAiHintConceptScope = computed(() => {
  const meta = activeAiHintMeta.value || {};
  const scopes = Array.isArray(meta.concept_scopes)
    ? meta.concept_scopes
        .map((item) => String(item || "").trim())
        .filter(Boolean)
    : [];
  if (scopes.length) {
    return Array.from(new Set(scopes)).join("、");
  }
  return String(meta.concept_label || "").trim();
});

const activeAiHintScopeLabel = computed(() => {
  const meta = activeAiHintMeta.value || {};
  const level = Number(meta.hint_level);
  const scope = String(meta.scope || "").trim().toLowerCase();
  if (level === 2 || scope === "narrow") return "聚焦概念提示";
  return "廣泛概念提示";
});

const activeAiHintDisplayNo = computed(() => {
  return feedbackModal.activeAiHintNo === 2 ? 2 : 1;
});

const aiHintActionLabel = computed(() => {
  if (feedbackModal.secondHintLoading) return "提示產生中...";
  if (generatedAiHintCount.value < 2 || !feedbackModal.aiHint2Text) return "產生第二次 AI 提示";
  return feedbackModal.activeAiHintNo === 2
    ? "查看第一次 AI 提示"
    : "查看第二次 AI 提示";
});

const maxHintRetry = 1;
const maxHintCount = 2;
const hintRetryUsed = ref(0);
const isRegeneratingHint = ref(false);
const hintRemaining = computed(() =>
  Math.max(
    0,
    maxHintRetry - hintRetryUsed.value
  )
);

function currentHintNo() {
  if (feedbackModal.mode === "ai_hint_flow") {
    return feedbackModal.activeAiHintNo === 2 ? 2 : 1;
  }
  return Math.min(maxHintCount, Math.max(1, Number(hintRetryUsed.value || 0) + 1));
}

const successModal = reactive({
  open: false,
  hasNext: false,
  nextVideoId: "",
  unitName: "",
});

// ========= localStorage 暫存 =========
// 每位學生、每一道題都使用不同 key。
// 舊版只用一個全域 key，切換到其他題的空白畫面時，會把前一題未完成狀態刪除或覆蓋。
const PRACTICE_STATE_KEY_PREFIX = "parsons_practice_state_v2";
const LEGACY_PRACTICE_STATE_KEY = "parsons_practice_state_v1";
const RESTORE_ONCE_KEY = "parsons_restore_once_v1";
const RESTORE_MSG_KEY = "parsons_restore_msg_v1";

function practiceStateStorageKey(taskIdValue, studentIdValue = studentId.value) {
  const taskKey = encodeURIComponent(
    String(taskIdValue || "").trim()
  );

  const studentKey = encodeURIComponent(
    String(studentIdValue || "anonymous").trim() ||
    "anonymous"
  );

  if (!taskKey) return "";

  return `${PRACTICE_STATE_KEY_PREFIX}:${studentKey}:${taskKey}`;
}

function parsePracticeState(raw) {
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object"
      ? parsed
      : null;
  } catch (_) {
    return null;
  }
}

function readPracticeStateForTask(taskIdValue) {
  const taskIdText = String(taskIdValue || "").trim();
  if (!taskIdText) return null;

  const storageKey =
    practiceStateStorageKey(taskIdText);

  if (!storageKey) return null;

  const currentState = parsePracticeState(
    localStorage.getItem(storageKey)
  );

  if (currentState) {
    return currentState;
  }

  // 相容舊版：若舊全域資料剛好屬於目前這一題，
  // 搬移到新的 per-task key，避免使用者更新程式後遺失未完成作答。
  const legacyState = parsePracticeState(
    localStorage.getItem(
      LEGACY_PRACTICE_STATE_KEY
    )
  );

  if (!legacyState) return null;

  const legacyTaskId = String(
    legacyState?.task_id || ""
  ).trim();

  const legacyStudentId = String(
    legacyState?.student_id || ""
  ).trim();

  const currentStudentId = String(
    studentId.value || ""
  ).trim();

  const sameTask =
    legacyTaskId === taskIdText;

  const sameStudent =
    !legacyStudentId ||
    !currentStudentId ||
    legacyStudentId === currentStudentId;

  if (!sameTask || !sameStudent) {
    return null;
  }

  localStorage.setItem(
    storageKey,
    JSON.stringify(legacyState)
  );

  localStorage.removeItem(
    LEGACY_PRACTICE_STATE_KEY
  );

  return legacyState;
}

// 每題作答狀態保存
function clearPracticeStateForTask(taskIdValue) {
  const taskIdText = String(taskIdValue || "").trim();
  if (!taskIdText) return;

  const storageKey =
    practiceStateStorageKey(taskIdText);

  if (storageKey) {
    localStorage.removeItem(storageKey);
  }

  // 只清除此題可能殘留的舊版全域資料，
  // 不可清除其他題的未完成狀態。
  const legacyState = parsePracticeState(
    localStorage.getItem(
      LEGACY_PRACTICE_STATE_KEY
    )
  );

  if (
    legacyState &&
    String(legacyState?.task_id || "").trim()
      === taskIdText
  ) {
    localStorage.removeItem(
      LEGACY_PRACTICE_STATE_KEY
    );
  }

  localStorage.removeItem(RESTORE_ONCE_KEY);
  localStorage.removeItem(RESTORE_MSG_KEY);
}

function resetFilled() {
  for (const k of Object.keys(filled)) delete filled[k];
  wrongIndices.value = [];
  primaryWrongIndex.value = null;
  wrongSlotIssueMap.value = {};
  result.value = null;
  state.err = "";
  feedbackModal.aiHint1Meta = {};
  feedbackModal.aiHint2Meta = {};

  for (const k of Object.keys(slotIndentLevel)) delete slotIndentLevel[k];
  activeSlotKey.value = null;
}

function isPrimaryWrong(idx) {
  if (primaryWrongIndex.value == null || primaryWrongIndex.value === "") return false;
  const p = Number(primaryWrongIndex.value);
  if (!Number.isFinite(p)) return false;
  return p === Number(idx);
}

// 檢查指定索引的插槽是否受影響（即是否為錯誤插槽但非主要錯誤插槽）
function isWrongSlot(idx) {
  if (
    !Array.isArray(wrongIndices.value) ||
    wrongIndices.value.length === 0
  ) {
    return false;
  }

  const targetIndex = Number(idx);

  if (!Number.isFinite(targetIndex)) {
    return false;
  }

  return wrongIndices.value.some(
    (value) => Number(value) === targetIndex
  );
}

// B 策略會有一個主要錯誤格與其他受影響錯誤格。
// C 策略也沿用這個判斷作紅色標記，但顯示內容改成固定中文語意。
function isAffectedWrong(idx) {
  return isWrongSlot(idx) && !isPrimaryWrong(idx);
}

function addSlotIssue(map, idx, kind) {
  const slotIndex = Number(idx);
  if (!Number.isFinite(slotIndex) || slotIndex < 0) return;

  const issueKind = String(kind || "").trim();
  if (!["sequence", "indentation"].includes(issueKind)) return;

  const key = String(slotIndex);
  const current = Array.isArray(map[key]) ? map[key] : [];
  if (!current.includes(issueKind)) {
    map[key] = [...current, issueKind];
  }
}

function normalizeSlotIssueMap(value = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }

  const map = {};
  for (const [rawKey, rawIssues] of Object.entries(value)) {
    const idx = Number(rawKey);
    if (!Number.isFinite(idx) || idx < 0) continue;
    const issues = Array.isArray(rawIssues) ? rawIssues : [rawIssues];
    for (const issue of issues) {
      addSlotIssue(map, idx, issue);
    }
  }
  return map;
}

function defaultSequenceIssueMap(positions = []) {
  const map = {};
  for (const idx of normalizeWrongPositions(positions)) {
    addSlotIssue(map, idx, "sequence");
  }
  return map;
}

function mergeSlotLists(...lists) {
  return Array.from(
    new Set(
      lists
        .flatMap((list) => normalizeWrongPositions(list))
        .filter((idx) => Number.isFinite(Number(idx)))
    )
  ).sort((a, b) => a - b);
}

function collectErrorTypes(...candidates) {
  return Array.from(
    new Set(
      candidates
        .flatMap((candidate) => Array.isArray(candidate) ? candidate : [])
        .map((item) => String(item || "").trim().toLowerCase())
        .filter(Boolean)
    )
  );
}

function buildWrongSlotIssueMap(r = {}, flow = {}, record = {}) {
  const positions = firstNonEmptyWrongPositions(
    flow.current_error_positions,
    flow.second_error_positions,
    flow.first_error_positions,
    record.latest_error_positions,
    record.second_error_positions,
    record.first_error_positions,
    r.current_error_positions,
    r.incorrect_slots,
    r.wrong_indices_all,
    r.wrong_indices,
    r.wrong_slots,
    r.indent_errors,
    r.wrong_index != null ? [r.wrong_index] : []
  );

  const sequenceSlots = mergeSlotLists(
    flow.sequence_slots,
    r.sequence_slots,
    r.wrong_slots
  );

  const indentationSlots = mergeSlotLists(
    flow.indentation_slots,
    r.indentation_slots,
    r.indent_errors
  );

  const map = {};
  sequenceSlots.forEach((idx) => addSlotIssue(map, idx, "sequence"));
  indentationSlots.forEach((idx) => addSlotIssue(map, idx, "indentation"));

  const detailCandidates = [
    r.wrong_slot_details,
    flow.wrong_slot_details,
    flow.current_wrong_slot_details,
    record.latest_wrong_slot_details,
  ];

  for (const details of detailCandidates) {
    if (!Array.isArray(details)) continue;
    for (const detail of details) {
      const idx = Number(
        detail?.slot_index ?? detail?.index ?? detail?.wrong_index
      );
      if (!Number.isFinite(idx)) continue;
      const types = collectErrorTypes(detail?.error_types);
      if (types.some((type) => type.includes("indent"))) {
        addSlotIssue(map, idx, "indentation");
      }
      if (types.some((type) => (
        type.includes("sequence") ||
        type.includes("structure") ||
        type.includes("order") ||
        type.includes("branch") ||
        type.includes("logic")
      ))) {
        addSlotIssue(map, idx, "sequence");
      }
    }
  }

  const globalTypes = collectErrorTypes(
    flow.current_error_types,
    flow.second_error_types,
    flow.first_error_types,
    record.latest_error_types,
    record.second_error_types,
    record.first_error_types,
    r.current_error_types,
    r.error_types
  );
  const globalHasIndent = globalTypes.some((type) => type.includes("indent"));
  const globalHasSequence = globalTypes.some((type) => (
    type.includes("sequence") ||
    type.includes("structure") ||
    type.includes("order") ||
    type.includes("branch") ||
    type.includes("logic")
  ));

  for (const idx of positions) {
    const key = String(idx);
    if (Array.isArray(map[key]) && map[key].length) continue;
    if (globalHasIndent && !globalHasSequence) {
      addSlotIssue(map, idx, "indentation");
    } else {
      addSlotIssue(map, idx, "sequence");
    }
  }

  return map;
}

function summarizeSlotIssues(issueMap = {}, fallbackPositions = []) {
  const map = normalizeSlotIssueMap(issueMap);
  const slotSet = new Set(
    Object.keys(map)
      .map((idx) => Number(idx))
      .filter((idx) => Number.isFinite(idx))
  );

  for (const idx of normalizeWrongPositions(fallbackPositions)) {
    slotSet.add(Number(idx));
  }

  let sequenceCount = 0;
  let indentationCount = 0;

  for (const idx of slotSet) {
    const issues = Array.isArray(map[String(idx)]) ? map[String(idx)] : [];
    if (issues.includes("sequence")) sequenceCount += 1;
    if (issues.includes("indentation")) indentationCount += 1;
    if (!issues.length) sequenceCount += 1;
  }

  return {
    total: slotSet.size,
    sequenceCount,
    indentationCount,
  };
}

function buildAdjustmentHeadline(count = 0) {
  const total = Math.max(0, Number(count) || 0);
  return `有 ${total} 個程式區塊需要調整`;
}

function buildAdjustmentFeedbackText(summary = {}, fallbackCount = 0) {
  const total = Math.max(0, Number(summary.total || fallbackCount) || 0);
  const sequenceCount = Math.max(0, Number(summary.sequenceCount || 0) || 0);
  const indentationCount = Math.max(0, Number(summary.indentationCount || 0) || 0);
  const issueParts = [];

  if (sequenceCount > 0) {
    issueParts.push(`順序問題 ${sequenceCount} 個`);
  }
  if (indentationCount > 0) {
    issueParts.push(`縮排問題 ${indentationCount} 個`);
  }

  const issueText = issueParts.length
    ? `其中 ${issueParts.join("、")}。`
    : "";

  return `目前有 ${total} 個程式區塊需要調整。${issueText}返回題目後，請依照紅框旁的標籤修正。`;
}

function applyWrongSlotIssuesFromResult(r = {}, flow = {}, record = {}) {
  const map = buildWrongSlotIssueMap(r, flow, record);
  wrongSlotIssueMap.value = normalizeSlotIssueMap(map);
  return summarizeSlotIssues(wrongSlotIssueMap.value, collectCurrentWrongPositions(r));
}

function applyFirstHintPanelAdjustmentSummary(summary = {}, fallbackCount = 0) {
  const total = Math.max(0, Number(summary.total || fallbackCount) || 0);
  firstHintPanel.wrongCount = total;
  firstHintPanel.sequenceCount = Math.max(0, Number(summary.sequenceCount || 0) || 0);
  firstHintPanel.indentationCount = Math.max(0, Number(summary.indentationCount || 0) || 0);
  firstHintPanel.headline = buildAdjustmentHeadline(total);
  firstHintPanel.text = buildAdjustmentFeedbackText(summary, total);
}

function slotAdjustmentTags(idx) {
  const issues = Array.isArray(wrongSlotIssueMap.value?.[String(Number(idx))])
    ? wrongSlotIssueMap.value[String(Number(idx))]
    : [];

  return issues.map((issue) => {
    if (issue === "indentation") {
      return {
        key: "indentation",
        kind: "indentation",
        label: "縮排需要調整",
      };
    }
    return {
      key: "sequence",
      kind: "sequence",
      label: "順序需要調整",
    };
  });
}

function hasSlotAdjustmentTags(idx) {
  return slotAdjustmentTags(idx).length > 0;
}

// 取得該槽位要顯示的中文語意。
function semanticTextForSlot(slot) {
  const expectedMeaning = String(
    slot?.expected_meaning_zh || ""
  ).trim();

  // C 策略只能顯示「該位置標準答案」的中文語意。
  // 不能顯示學生目前拖入之錯誤 block 的語意。
  // 這是研究設計中的固定提示，送出當下不呼叫 AI。
  if (isCStrategy.value) {
    return expectedMeaning;
  }

  // B 策略維持目前原始行為，不做變更。
  return String(
    expectedMeaning ||
    filled[slot?.slot]?.meaning_zh ||
    ""
  ).trim();
}

// 中文語意顯示條件
function shouldShowSlotSemantic(slot, idx) {
  if (!effectiveShowSemanticZh.value) {
    return false;
  }

  const semanticText = semanticTextForSlot(slot);

  if (!semanticText) {
    return false;
  }

  // C：尚未送出時 wrongIndices 為空，因此完全不顯示。
  // 送出後只有錯誤格顯示。
  // 修正成功的格子會從 wrongIndices 消失，因此提示同步消失。
  if (isCStrategy.value) {
    return isWrongSlot(idx);
  }

  // B：完全維持原本「有中文語意就顯示」的行為。
  return true;
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

  filled[String(slotKey)] = normalizeFilledBlock(payload.block);

  setActiveSlotKey(slotKey);
  dragging.value = null;

  // 拖曳完成後保存目前排列，未送出就離開也能恢復。
  savePracticeState({
    task_status: "editing",
    save_reason: "drop",
  });
}

function removeFromSlot(slotKey) {
  if (slotKey == null) return;

  delete filled[String(slotKey)];

  const idx = templateSlots.value.findIndex(
    (slot) => String(slot.slot) === String(slotKey)
  );

  if (idx >= 0) {
    wrongIndices.value = (wrongIndices.value || []).filter(
      (wrongIndex) => Number(wrongIndex) !== Number(idx)
    );
    const nextIssueMap = {
      ...(wrongSlotIssueMap.value || {}),
    };
    delete nextIssueMap[String(idx)];
    wrongSlotIssueMap.value = nextIssueMap;
  }

  delete slotIndentLevel[String(slotKey)];

  if (String(activeSlotKey.value || "") === String(slotKey)) {
    activeSlotKey.value = null;
  }

  applyIndentStylesToDOM();

  // 移除片段後也更新暫存，避免回來又出現已刪除的舊片段。
  savePracticeState({
    task_status: "editing",
    save_reason: "remove_block",
  });
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
        testMeta.total = j?.total || total;
        testMeta.current_index = j?.current_index || (cur + 1);

        const norm = normalizeTaskPayload(j);
        task.value = norm.taskObj;
        poolBlocks.value = norm.pool;
        templateSlots.value = norm.slots;

        resetFilled();
        wrongIndices.value = [];
        primaryWrongIndex.value = null;
        wrongSlotIssueMap.value = {};
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
    await logLearningEvent("hide_hint", feedbackLearningContext(currentHintMetadata({
      close_method: "click_hint_retry",
      next_hint_no: nextUsed + 1,
    })));
    feedbackModal.hintQuestion = buildRetryHintQuestion(parts, nextUsed);
    hintRetryUsed.value = nextUsed;
    const openEvent = await logLearningEvent("view_hint", feedbackLearningContext(currentHintMetadata({
      trigger_method: "click_hint_retry",
      button_name: "再次提示",
      hint_loaded: true,
      hint_retry_count: nextUsed,
    })));
    feedbackModal.reviewOpenLogId = String(openEvent?.log_id || "");
    await updateReviewOpenHintLog({
      trigger_method: "click_hint_retry",
      button_name: "再次提示",
      hint_loaded: true,
      hint_retry_count: nextUsed,
    });
    if (feedbackModal.attemptId) {
      await sendChoice(feedbackModal.attemptId, "no", {
        click_type: "hint_retry",
        hint_retry_count: nextUsed,
        trigger_method: "click_hint_retry",
        button_name: "再次提示",
        hint_no: currentHintNo(),
        hint_click_no: currentHintNo(),
        max_hint_count: maxHintCount,
        hint_text: feedbackModal.hintQuestion,
        hint_content: feedbackModal.hintQuestion,
        hint_source: feedbackModal.source || "frontend",
        hint_loaded: true,
        error_type: feedbackModal.errorClass || null,
        wrong_slots: Array.isArray(wrongIndices.value)
          ? wrongIndices.value.map((v) => Number(v)).filter((v) => Number.isFinite(v))
          : [],
        ai_diagnosis_summary: feedbackModal.aiDiagnosisSummary || feedbackModal.diagnosis || "",
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
  const flow =
    r?.hint_flow && typeof r.hint_flow === "object"
      ? r.hint_flow
      : {};
  const record =
    flow.hint_record && typeof flow.hint_record === "object"
      ? flow.hint_record
      : {};
  const issueMap = buildWrongSlotIssueMap(r || {}, flow, record);
  if (Object.keys(issueMap).length) {
    wrongSlotIssueMap.value = normalizeSlotIssueMap(issueMap);
  }
  const issueSummary = summarizeSlotIssues(
    wrongSlotIssueMap.value,
    collectCurrentWrongPositions(r || {})
  );
  const adjustmentCount =
    issueSummary.total ||
    collectCurrentWrongPositions(r || {}).length ||
    Number(r?.error_count || 0) ||
    wrongIndices.value.length ||
    0;
  feedbackModal.open = true;
  feedbackModal.mode = "legacy";
  feedbackModal.stage = "detail";
  feedbackModal.headline = buildAdjustmentHeadline(adjustmentCount);
  feedbackModal.locationText = "紅框標示的程式區塊";
  feedbackModal.hintQuestion = "";
  feedbackModal.slotLabel = parts.slotLabel;
  feedbackModal.diagnosis = parts.diagnosis;
  feedbackModal.errorClass = String(parts.errorClass || "");
  feedbackModal.conceptHint = parts.conceptHint;
  feedbackModal.reflectionQuestions = parts.reflectionQuestions;
  feedbackModal.impactHint = parts.impactHint;
  feedbackModal.source = String(parts.source || "default");
  feedbackModal.firstHint = String(parts.firstHint || "");
  feedbackModal.secondHint = String(parts.secondHint || "");
  feedbackModal.hintRecord = null;
  feedbackModal.hintId = "";
  feedbackModal.firstSystemHintText = "";
  // 第一次、第二次提示切換
  feedbackModal.aiHint1Text = "";  // 第一次提示文字
  feedbackModal.aiHint1Meta = {};
  feedbackModal.aiHint2Text = "";  // 第二次提示文字
  feedbackModal.aiHint2Meta = {};
  feedbackModal.secondHintLoading = false;
  feedbackModal.secondHintNoticeVisible = false;
  feedbackModal.secondHintReminderShownLogged = false;
  feedbackModal.secondHintReminderResolved = false;
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
  feedbackModal.aiDiagnosisSummary = "";
  feedbackModal.start = parts.startSec;
  feedbackModal.end = parts.endSec;
  feedbackModal.chapterStart = getChapterBoundary(parts.chapterRecommendation, ["start", "start_sec"]);
  feedbackModal.chapterEnd = getChapterBoundary(parts.chapterRecommendation, ["end", "end_sec"]);
  feedbackModal.jumpVideoId = String(r?.jump?.video_id || "");
  feedbackModal.attemptId = String(r?.attempt_id || "");
  feedbackModal.attemptV2Id = String(r?.attempt_v2_id || "");
  feedbackModal.attemptNo = Number.isFinite(Number(r?.attempt_no)) ? Number(r.attempt_no) : null;
  feedbackModal.reviewAttemptId = String(r?.review_attempt_id || r?.attempt_id || "");
  feedbackModal.reviewOpenLogId = "";
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

function applyHintRecordToFeedbackModal(record = {}, extra = {}) {
  const hintRecord = (record && typeof record === "object") ? record : {};
  const requestedHintNo = Number(extra.requested_hint_no || 1) === 2 ? 2 : 1;
  feedbackModal.hintRecord = hintRecord;
  feedbackModal.hintId = String(hintRecord.hint_id || "");
  feedbackModal.attemptId = String(hintRecord.last_attempt_id || feedbackModal.attemptId || "");
  feedbackModal.taskId = String(hintRecord.task_id || feedbackModal.taskId || currentTaskId() || "");
  feedbackModal.firstSystemHintText = String(
    hintRecord.first_system_hint_text
    || extra.first_system_hint_text
    || ""
  );
  feedbackModal.aiHint1Text = String(
    hintRecord.ai_hint_1_text
    || extra.ai_hint_1_text
    || (requestedHintNo === 1 ? extra.hint : "")
    || ""
  );
  feedbackModal.aiHint2Text = String(
    hintRecord.ai_hint_2_text
    || extra.ai_hint_2_text
    || (requestedHintNo === 2 ? extra.hint : "")
    || ""
  );
  const nextHint1Meta = firstNonEmptyHintMeta(
    hintRecord.ai_hint_1_meta
    , extra.ai_hint_1_meta
    , requestedHintNo === 1 ? extra.hint_meta : null
  );
  const nextHint2Meta = firstNonEmptyHintMeta(
    hintRecord.ai_hint_2_meta
    , extra.ai_hint_2_meta
    , requestedHintNo === 2 ? extra.hint_meta : null
  );
  feedbackModal.aiHint1Meta = Object.keys(nextHint1Meta).length
    ? nextHint1Meta
    : sanitizeHintMeta(feedbackModal.aiHint1Meta);
  feedbackModal.aiHint2Meta = Object.keys(nextHint2Meta).length
    ? nextHint2Meta
    : sanitizeHintMeta(feedbackModal.aiHint2Meta);
  feedbackModal.source = String(extra.source || feedbackModal.source || "default");
  feedbackModal.hintQuestion = activeAiHintText.value || feedbackModal.aiHint1Text || String(extra.hint || "");
  feedbackModal.hintLoaded = Boolean(feedbackModal.aiHint1Text);
  feedbackModal.hintLoading = false;
  feedbackModal.hintError = "";
}

function hasUnviewedSecondAiHintOpportunity(record = {}) {
  const hintRecord = (record && typeof record === "object") ? record : {};
  const hasFirstHint = Boolean(
    String(hintRecord.ai_hint_1_text || feedbackModal.aiHint1Text || "").trim()
  );
  const hasSecondHint = Boolean(
    String(hintRecord.ai_hint_2_text || feedbackModal.aiHint2Text || "").trim()
  );
  return hasFirstHint && !hasSecondHint;
}

function normalizeWrongPositions(list) {
  if (!Array.isArray(list)) return [];

  return Array.from(
    new Set(
      list
        .map((item) => Number(item))
        .filter(
          (number) =>
            Number.isFinite(number)
            && number >= 0
        )
    )
  ).sort((a, b) => a - b);
}

function firstNonEmptyWrongPositions(...candidates) {
  for (const candidate of candidates) {
    const positions = normalizeWrongPositions(candidate);

    if (positions.length > 0) {
      return positions;
    }
  }

  return [];
}

// 第一次系統提示使用第一次送出的錯誤快照
function collectFirstWrongPositions(
  r = {},
  flow = {},
  record = {}
) {
  return firstNonEmptyWrongPositions(
    flow.first_error_positions,
    record.first_error_positions,
    r.incorrect_slots,
    r.wrong_slots,
    r.wrong_indices_all,
    r.wrong_indices,
    r.indent_errors,
    r.wrong_index != null
      ? [r.wrong_index]
      : []
  );
}

// 第二次 AI 提示使用第二次當下的錯誤位置
function collectSecondWrongPositions(
  r = {},
  flow = {},
  record = {}
) {
  return firstNonEmptyWrongPositions(
    flow.second_error_positions,
    record.second_error_positions,
    r.incorrect_slots,
    r.wrong_slots,
    r.wrong_indices_all,
    r.wrong_indices,
    r.indent_errors,
    r.wrong_index != null
      ? [r.wrong_index]
      : []
  );
}

// 每一次送出後，畫面紅色標記使用本次最新結果
function collectCurrentWrongPositions(r = {}) {
  return firstNonEmptyWrongPositions(
    r.incorrect_slots,
    r.wrong_slots,
    r.wrong_indices_all,
    r.wrong_indices,
    r.indent_errors,
    r.wrong_index != null
      ? [r.wrong_index]
      : []
  );
}
function formatWrongPositionText(positions = []) {
  if (!Array.isArray(positions) || !positions.length) return "紅色標記的位置";
  return positions.map((idx) => `第 ${Number(idx) + 1} 格`).join("、");
}

function formatErrorTypeText(types = [], fallback = "") {
  const labels = {
    sequence_error: "結構順序錯誤",
    indentation_error: "縮排錯誤",
    structure_error: "結構順序錯誤",
    branch_error: "if-else 結構錯誤",
    logic_error: "邏輯錯誤",
  };
  const list = Array.isArray(types) ? types : [];
  const mapped = list
    .map((item) => labels[String(item || "").trim()] || String(item || "").trim())
    .filter(Boolean);
  if (mapped.length) return Array.from(new Set(mapped)).join("、");
  return fallback || "待檢查";
}

function showFirstSystemHint(r, taskId) {
  openFeedbackModal(r, taskId);
  const parts = buildWrongFeedbackParts(r || {});
  const flow = (r?.hint_flow && typeof r.hint_flow === "object") ? r.hint_flow : {};
  const record = (flow.hint_record && typeof flow.hint_record === "object") ? flow.hint_record : {};
  const positions = collectFirstWrongPositions(
  r || {},
  flow,
  record
);
  if (positions.length) {
    wrongIndices.value = positions;
  }
  primaryWrongIndex.value = parts.focusIndex != null
    ? Number(parts.focusIndex)
    : (positions.length ? positions[0] : null);
  const errorTypes = Array.isArray(flow.first_error_types)
    ? flow.first_error_types
    : (Array.isArray(record.first_error_types) ? record.first_error_types : []);
  const issueSummary = applyWrongSlotIssuesFromResult(r || {}, flow, record);
  feedbackModal.mode = "first_system_hint";
  feedbackModal.open = true;
  feedbackModal.stage = "detail";
  feedbackModal.hintOpen = false;
  firstHintPanel.visible = false;
  applyFirstHintPanelAdjustmentSummary(
    issueSummary,
    positions.length || Number(r?.error_count || 0) || wrongIndices.value.length || 0
  );
  firstHintPanel.wrongPositionText = formatWrongPositionText(positions.length ? positions : wrongIndices.value);
  firstHintPanel.errorTypeText = formatErrorTypeText(errorTypes, parts.diagnosis);
  firstHintPanel.hintRecord = record;
  feedbackModal.hintRecord = record;
  feedbackModal.hintId = String(record.hint_id || "");
  feedbackModal.taskId = String(taskId || "");
  feedbackModal.attemptId = String(r?.attempt_id || "");
  feedbackModal.attemptV2Id = String(r?.attempt_v2_id || "");
  feedbackModal.attemptNo = Number.isFinite(Number(r?.attempt_no)) ? Number(r.attempt_no) : null;
  feedbackModal.targetConcept = String(r?.target_concept || currentTargetConcept() || "");
}


function showSystemRecheck(r, taskId) {
  openFeedbackModal(r, taskId);

  const flow =
    r?.hint_flow && typeof r.hint_flow === "object"
      ? r.hint_flow
      : {};

  const record =
    flow.hint_record && typeof flow.hint_record === "object"
      ? flow.hint_record
      : {};

  const positions = firstNonEmptyWrongPositions(
    flow.current_error_positions,
    r?.incorrect_slots,
    r?.wrong_slots,
    r?.wrong_indices_all,
    r?.wrong_indices,
    r?.indent_errors,
    r?.wrong_index != null ? [r.wrong_index] : []
  );

  wrongIndices.value = positions;
  primaryWrongIndex.value =
    positions.length > 0
      ? positions[0]
      : null;

  const errorTypes = Array.isArray(flow.current_error_types)
    ? flow.current_error_types
    : (Array.isArray(r?.error_types) ? r.error_types : []);

  const attemptNo = Number(r?.attempt_no || 0);
  const wrongCount = Number(
    flow.current_error_count
    ?? r?.error_count
    ?? positions.length
  );
  const issueSummary = applyWrongSlotIssuesFromResult(r || {}, flow, record);

  firstHintPanel.visible = false;
  applyFirstHintPanelAdjustmentSummary(
    issueSummary,
    Number.isFinite(wrongCount)
      ? wrongCount
      : positions.length
  );
  firstHintPanel.wrongPositionText =
    formatWrongPositionText(positions);
  firstHintPanel.errorTypeText =
    formatErrorTypeText(errorTypes, "待調整");
  firstHintPanel.hintRecord = record;

  feedbackModal.mode = "system_recheck";
  feedbackModal.open = true;
  feedbackModal.stage = "detail";
  feedbackModal.hintOpen = false;
  feedbackModal.taskId = String(taskId || "");
  feedbackModal.attemptId = String(r?.attempt_id || "");
  feedbackModal.attemptV2Id = String(r?.attempt_v2_id || "");
  feedbackModal.attemptNo = Number.isFinite(attemptNo)
    ? attemptNo
    : null;
  feedbackModal.targetConcept = String(
    r?.target_concept
    || currentTargetConcept()
    || ""
  );

  applyHintRecordToFeedbackModal(record, {});
  feedbackModal.secondHintReminderShownLogged = false;
  feedbackModal.secondHintReminderResolved = false;
  feedbackModal.secondHintNoticeVisible = hasUnviewedSecondAiHintOpportunity(record);
  feedbackModal.mode = "system_recheck";
  feedbackModal.open = true;
  feedbackModal.hintOpen = false;
  if (feedbackModal.secondHintNoticeVisible) {
    void logSecondHintReminderShown("system_recheck_modal");
  }
}
// c策略頁面每次答錯都呼叫這個函式
function showFixedSemanticFeedbackModal(r, taskId) {
  const flow =
    r?.hint_flow && typeof r.hint_flow === "object"
      ? r.hint_flow
      : {};

  // C 策略每次答錯都只呈現固定中文回饋。
  // 這裡不呼叫 AI、不讀 AI hint record，也不進入 B 策略的 ai_hint_flow。
  const positions = firstNonEmptyWrongPositions(
    flow.current_error_positions,
    r?.incorrect_slots,
    r?.wrong_slots,
    r?.wrong_indices_all,
    r?.wrong_indices,
    r?.indent_errors,
    r?.wrong_index != null ? [r.wrong_index] : []
  );

  wrongIndices.value = positions;
  primaryWrongIndex.value =
    positions.length > 0
      ? positions[0]
      : null;

  const errorTypes = Array.isArray(flow.current_error_types)
    ? flow.current_error_types
    : (Array.isArray(r?.error_types) ? r.error_types : []);

  const wrongCount = Number(
    flow.current_error_count
    ?? r?.error_count
    ?? positions.length
  );
  const attemptNo = Number(r?.attempt_no || 0);
  const displayAttemptNo =
    Number.isFinite(attemptNo) && attemptNo > 0
      ? attemptNo
      : 1;
  const displayWrongCount =
    Number.isFinite(wrongCount) && wrongCount > 0
      ? wrongCount
      : positions.length;
  const issueSummary = applyWrongSlotIssuesFromResult(r || {}, flow, {});
  const positionText = formatWrongPositionText(positions);
  const errorTypeText = formatErrorTypeText(errorTypes, "結構順序錯誤");

  firstHintPanel.visible = false;
  applyFirstHintPanelAdjustmentSummary(issueSummary, displayWrongCount);
  firstHintPanel.wrongPositionText = positionText;
  firstHintPanel.errorTypeText = errorTypeText;
  firstHintPanel.hintRecord = null;

  feedbackModal.mode = "fixed_semantic_feedback";
  feedbackModal.open = true;
  feedbackModal.stage = "detail";
  feedbackModal.hintOpen = false;
  feedbackModal.hintRecord = null;
  feedbackModal.hintId = "";
  feedbackModal.aiHint1Text = "";
  feedbackModal.aiHint2Text = "";
  feedbackModal.taskId = String(taskId || "");
  feedbackModal.attemptId = String(r?.attempt_id || "");
  feedbackModal.attemptV2Id = String(r?.attempt_v2_id || "");
  feedbackModal.attemptNo = Number.isFinite(attemptNo)
    ? attemptNo
    : null;
  feedbackModal.targetConcept = String(
    r?.target_concept
    || currentTargetConcept()
    || ""
  );
}

function openAiHintModalFromFlow(r, taskId) {
  openFeedbackModal(r, taskId);

  const flow =
    r?.hint_flow && typeof r.hint_flow === "object"
      ? r.hint_flow
      : {};

  const record =
    flow.hint_record && typeof flow.hint_record === "object"
      ? flow.hint_record
      : {};

  // 第二次 AI 提示只使用第二次作答的錯誤位置，
  // 不可以再合併第一次錯誤位置。
  const positions = collectSecondWrongPositions(
    r || {},
    flow,
    record
  );

  wrongIndices.value = positions;
  primaryWrongIndex.value =
    positions.length > 0
      ? positions[0]
      : null;
  applyWrongSlotIssuesFromResult(r || {}, flow, record);

  feedbackModal.mode = "ai_hint_flow";
  feedbackModal.open = true;
  feedbackModal.stage = "detail";
  feedbackModal.hintOpen = true;
  feedbackModal.hintLoaded = true;
  feedbackModal.source = String(
    flow.source || "parsons_hint_records"
  );

  feedbackModal.aiDiagnosisSummary = String(
    flow.ai_diagnosis_summary
    || feedbackModal.diagnosis
    || ""
  );

  applyHintRecordToFeedbackModal(record, {
    hint: flow.hint,
    hint_meta: flow.hint_meta,
    requested_hint_no: 1,
    ai_feedback_detail: flow.ai_feedback_detail,
    ai_diagnosis_summary: flow.ai_diagnosis_summary,
    source: flow.source,
  });

  if (
    flow.ai_feedback_detail
    && typeof flow.ai_feedback_detail === "object"
  ) {
    applyAiHintToFeedbackModal({
      source: flow.source || "parsons_hint_records",
      hint: flow.hint,
      ai_feedback_detail: flow.ai_feedback_detail,
      ai_diagnosis_summary: flow.ai_diagnosis_summary,
    });

    applyHintRecordToFeedbackModal(record, {
      hint: flow.hint,
      hint_meta: flow.hint_meta,
      requested_hint_no: 1,
      source: flow.source,
    });
  }

  firstHintPanel.visible = false;
  // 目前顯示哪一組，由activeAiHintNo控制
  feedbackModal.activeAiHintNo = 1;

  if (feedbackModal.attemptId) {
    feedbackModal.hintLoading = true;
    feedbackModal.hintError = "";

    fetchHintRecord(1, {
      trigger_method: "auto_second_wrong",
      button_name: "AI 提示",
    })
      .catch((err) => {
        feedbackModal.hintError =
          err?.message || "AI 提示載入失敗";
      })
      .finally(() => {
        feedbackModal.hintLoading = false;
      });
  }
}

async function fetchHintRecord(requestedHintNo, extra = {}) {
  if (!feedbackModal.attemptId) return null;
  const normalizedHintNo = Number(requestedHintNo) === 2 ? 2 : 1;
  const res = await fetch(`${API_BASE}/api/parsons/hint`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      session_id: learningSessionId,
      page: "parsons",
      activity_type: isTestMode.value ? "test" : "practice",
      test_role: isTestMode.value ? String(testRole.value || "") : null,
      attempt_id: feedbackModal.attemptId,
      attempt_v2_id: feedbackModal.attemptV2Id || null,
      attempt_no: feedbackModal.attemptNo,
      task_id: feedbackModal.taskId || currentTaskId(),
      target_concept: feedbackModal.targetConcept || currentTargetConcept(),
      requested_hint_no: normalizedHintNo,
      ...extra,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    if (data?.hint_record) applyHintRecordToFeedbackModal(data.hint_record);
    throw new Error(data?.message || "AI 提示載入失敗");
  }
  feedbackModal.activeAiHintNo = normalizedHintNo;
  applyHintRecordToFeedbackModal(data.hint_record, {
    hint: data.hint,
    hint_meta: data.hint_meta,
    requested_hint_no: normalizedHintNo,
    ai_feedback_detail: data.ai_feedback_detail,
    ai_diagnosis_summary: data.ai_diagnosis_summary,
    source: data.source,
  });
  if (normalizedHintNo === 2 && !feedbackModal.aiHint2Text) {
    throw new Error("第二次 AI 提示產生失敗，請稍後再試。");
  }
  if (normalizedHintNo === 2) {
    feedbackModal.secondHintNoticeVisible = false;
    feedbackModal.secondHintReminderResolved = true;
  }
  return data;
}

// 「產生第二次 AI 提示」與已保存提示切換
async function openSecondHintFromReminder(triggerMethod = "click_second_hint_reminder") {
  if (feedbackModal.hintLoading || feedbackModal.secondHintLoading) return;
  feedbackModal.mode = "ai_hint_flow";
  feedbackModal.open = true;
  feedbackModal.stage = "detail";
  feedbackModal.hintOpen = true;
  feedbackModal.activeAiHintNo = 2;
  feedbackModal.hintError = "";
  firstHintPanel.visible = false;

  try {
    await logSecondHintReminderClicked(triggerMethod);
    feedbackModal.secondHintLoading = true;
    await fetchHintRecord(2, {
      trigger_method: triggerMethod,
      button_name: "查看第二次 AI 提示",
    });
    feedbackModal.secondHintNoticeVisible = false;
  } catch (err) {
    feedbackModal.hintError = err?.message || "第二次 AI 提示載入失敗";
  } finally {
    feedbackModal.secondHintLoading = false;
  }
}

async function handleAiHintAction() {
  if (feedbackModal.hintLoading || feedbackModal.secondHintLoading) return;
  if (generatedAiHintCount.value < 2 || !feedbackModal.aiHint2Text) {
    try {
      feedbackModal.secondHintLoading = true;
      await fetchHintRecord(2, {
        trigger_method: "generate_second_ai_hint",
        button_name: "產生第二次 AI 提示",
      });
      feedbackModal.activeAiHintNo = 2;
    } catch (err) {
      feedbackModal.hintError = err?.message || "第二次 AI 提示產生失敗";
    } finally {
      feedbackModal.secondHintLoading = false;
    }
    return;
  }
  feedbackModal.activeAiHintNo = feedbackModal.activeAiHintNo === 2 ? 1 : 2;
}

async function reviewFirstHintFromModal() {
  firstHintPanel.visible = true;
  firstHintPanel.headline = feedbackModal.headline || "第一次系統提示";
  firstHintPanel.text = feedbackModal.firstSystemHintText || "請先檢查紅色標記的位置，重新確認程式區塊的順序或結構。";
  await logLearningEvent("review_code_from_hint", feedbackLearningContext(currentHintMetadata({
    trigger_method: "click_review_first_hint",
    button_name: "回看第一次提示",
  })));
}

async function closeAiHintModal() {
  await logLearningEvent("ai_hint_modal_close", feedbackLearningContext(currentHintMetadata({
    close_method: "click_x_icon",
    button_name: "X",
  })));
  feedbackModal.open = false;
}

async function returnToFixFromHint() {
  await logLearningEvent("return_to_fix_from_hint", feedbackLearningContext(currentHintMetadata({
    return_method: "click_return_to_fix",
    button_name: "返回題目修正",
  })));
  feedbackModal.open = false;
}

async function returnToFixFromFirstHint() {
  await logLearningEvent("return_to_task", {
    task_id: feedbackModal.taskId || currentTaskId(),
    attempt_id: feedbackModal.attemptV2Id || null,
    attempt_no: feedbackModal.attemptNo,
    target_concept: feedbackModal.targetConcept || currentTargetConcept(),
    metadata: {
      return_method: "first_error_hint_modal",
      wrong_slots: Array.isArray(wrongIndices.value)
        ? wrongIndices.value.map((v) => Number(v)).filter((v) => Number.isFinite(v))
        : [],
      error_type: feedbackModal.errorClass || null,
      error_text: firstHintPanel.errorTypeText,
    },
  });
  feedbackModal.open = false;
  feedbackModal.mode = "legacy";
}

async function returnToFixFromSystemRecheck() {
  await logSecondHintReminderIgnored("system_recheck_return_to_fix");
  await logLearningEvent("return_to_task", {
    task_id: feedbackModal.taskId || currentTaskId(),
    attempt_id: feedbackModal.attemptV2Id || null,
    attempt_no: feedbackModal.attemptNo,
    target_concept: feedbackModal.targetConcept || currentTargetConcept(),
    metadata: {
      return_method: "system_recheck_modal",
      wrong_slots: Array.isArray(wrongIndices.value)
        ? wrongIndices.value
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value))
        : [],
      error_text: firstHintPanel.errorTypeText,
    },
  });
  feedbackModal.open = false;
  feedbackModal.mode = "legacy";
}

// 點「返回題目修正」會關閉 C 固定提示框，並寫入 return_to_task，metadata 標記
async function returnToFixFromFixedFeedback() {
  await logLearningEvent("return_to_task", {
    task_id: feedbackModal.taskId || currentTaskId(),
    attempt_id: feedbackModal.attemptV2Id || null,
    attempt_no: feedbackModal.attemptNo,
    target_concept: feedbackModal.targetConcept || currentTargetConcept(),
    metadata: {
      feedback_strategy: "C",
      feedback_type: "fixed_semantic_feedback",
      return_method: "fixed_semantic_feedback_modal",
      wrong_slots: Array.isArray(wrongIndices.value)
        ? wrongIndices.value
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value))
        : [],
      error_text: firstHintPanel.errorTypeText,
    },
  });
  feedbackModal.open = false;
  feedbackModal.mode = "legacy";
}

async function reopenAiHintFromFloating() {
  if (hasPendingSecondAiHint.value) {
    await openSecondHintFromReminder("click_floating_second_hint");
    return;
  }
  if (feedbackModal.attemptId) {
    try {
      // 提示 API 會自動判斷要回傳第一次或第二次提示，這裡不需要指定 requested_hint_no
      await fetchHintRecord(1, {
        trigger_method: "click_floating_ai_hint",
        button_name: "查看 AI 提示",
      });
    } catch (err) {
      feedbackModal.hintError = err?.message || "AI 提示載入失敗";
    }
  }
  feedbackModal.mode = "ai_hint_flow";
  feedbackModal.open = true;
  feedbackModal.stage = "detail";
  feedbackModal.hintOpen = true;
  feedbackModal.activeAiHintNo = 1;
  firstHintPanel.visible = false;
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
  feedbackModal.aiDiagnosisSummary = String(data?.ai_diagnosis_summary || feedbackModal.diagnosis || "");
  feedbackModal.conceptHint = String(detail?.concept_hint || detail?.concept_explanation || feedbackModal.conceptHint || "");
  feedbackModal.firstHint = String(detail?.first_hint || data?.hint || feedbackModal.firstHint || "");
  feedbackModal.secondHint = String(detail?.second_hint || feedbackModal.secondHint || "");
  const requestedHintNo = Number(data?.requested_hint_no || feedbackModal.activeAiHintNo || 1) === 2 ? 2 : 1;
  const resolvedHintText = String(
    data?.hint
    || (requestedHintNo === 2 ? detail?.second_hint : detail?.first_hint)
    || detail?.guiding_question
    || detail?.concept_explanation
    || detail?.concept_hint
    || ""
  ).trim();
  if (resolvedHintText) {
    if (requestedHintNo === 2) {
      feedbackModal.aiHint2Text = resolvedHintText;
    } else {
      feedbackModal.aiHint1Text = resolvedHintText;
    }
  }
  const nextMeta = sanitizeHintMeta(data?.hint_meta);
  if (Object.keys(nextMeta).length) {
    if (requestedHintNo === 2) {
      feedbackModal.aiHint2Meta = nextMeta;
    } else {
      feedbackModal.aiHint1Meta = nextMeta;
    }
  }
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
    await updateReviewOpenHintLog({ hint_loaded: true });
    return;
  }

  feedbackModal.hintLoading = true;
  feedbackModal.hintError = "";
  feedbackModal.hintQuestion = "提示產生中...";

  try {
    const res = await fetch(`${API_BASE}/api/parsons/hint`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
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
    await updateReviewOpenHintLog({ hint_loaded: true });
  } catch (err) {
    feedbackModal.hintError = err?.message || "提示產生失敗";
    feedbackModal.hintQuestion = "AI 提示暫時無法產生，請稍後再試。";
    await updateReviewOpenHintLog({
      hint_loaded: false,
      hint_error: feedbackModal.hintError,
    });
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

function currentHintMetadata(extra = {}) {
  const detail = (aiFeedbackDetail.value && typeof aiFeedbackDetail.value === "object")
    ? aiFeedbackDetail.value
    : {};
  const safeAiFeedbackDetail = sanitizeHintMeta(detail);
  delete safeAiFeedbackDetail.subtitle_excerpt;
  const hintNo = Number.isFinite(Number(extra.hint_no))
    ? Number(extra.hint_no)
    : currentHintNo();
  const hintMeta = activeAiHintMeta.value || {};
  return {
    review_type: "ai_hint",
    hint_id: feedbackModal.hintId || feedbackModal.hintRecord?.hint_id || null,
    requested_hint_no: Number.isFinite(Number(extra.requested_hint_no))
      ? Number(extra.requested_hint_no)
      : hintNo,
    hint_no: hintNo,
    hint_click_no: hintNo,
    max_hint_count: maxHintCount,
    question_id: feedbackModal.taskId || currentTaskId(),
    unit_id: currentUnitId(),
    question_type: "parsons",
    hint_type: "ai_hint",
    hint_generation_count: Number(feedbackModal.hintRecord?.hint_generation_count ?? feedbackModal.hintRecord?.ai_hint_generation_count ?? 0),
    hint_view_count: Number(feedbackModal.hintRecord?.hint_view_count ?? feedbackModal.hintRecord?.ai_hint_view_count ?? 0),
    ai_hint_generation_count: Number(feedbackModal.hintRecord?.ai_hint_generation_count ?? feedbackModal.hintRecord?.hint_generation_count ?? 0),
    ai_hint_view_count: Number(feedbackModal.hintRecord?.ai_hint_view_count ?? feedbackModal.hintRecord?.hint_view_count ?? 0),
    first_system_hint_text: feedbackModal.firstSystemHintText || feedbackModal.hintRecord?.first_system_hint_text || "",
    ai_hint_1_text: feedbackModal.aiHint1Text || feedbackModal.hintRecord?.ai_hint_1_text || "",
    ai_hint_2_text: feedbackModal.aiHint2Text || feedbackModal.hintRecord?.ai_hint_2_text || "",
    ai_hint_1_meta: sanitizeHintMeta(feedbackModal.aiHint1Meta || feedbackModal.hintRecord?.ai_hint_1_meta),
    ai_hint_2_meta: sanitizeHintMeta(feedbackModal.aiHint2Meta || feedbackModal.hintRecord?.ai_hint_2_meta),
    hint_text: String(activeAiHintText.value || feedbackModal.hintQuestion || "").trim(),
    hint_content: String(activeAiHintText.value || feedbackModal.hintQuestion || "").trim(),
    hint_level: hintMeta.hint_level ?? null,
    scope: hintMeta.scope || "",
    answer_leakage_check: hintMeta.answer_leakage_check || "",
    hint_source: hintMeta.hint_source || feedbackModal.source || "unknown",
    hint_loaded: Boolean(feedbackModal.hintLoaded),
    hint_error: feedbackModal.hintError || "",
    hint_retry_count: hintRetryUsed.value,
    error_type: feedbackModal.errorClass || null,
    error_types: Array.isArray(extra.error_types)
      ? extra.error_types
      : (Array.isArray(result.value?.error_types) ? result.value.error_types : []),
    repeated_error: typeof extra.repeated_error === "boolean"
      ? extra.repeated_error
      : Boolean(result.value?.repeated_error),
    wrong_slots: Array.isArray(wrongIndices.value)
      ? wrongIndices.value.map((v) => Number(v)).filter((v) => Number.isFinite(v))
      : [],
    ai_feedback_detail: safeAiFeedbackDetail,
    ai_diagnosis_summary: feedbackModal.aiDiagnosisSummary || feedbackModal.diagnosis || "",
    concept_hint: feedbackModal.conceptHint || "",
    first_hint: feedbackModal.firstHint || "",
    second_hint: feedbackModal.secondHint || "",
    possible_causes: Array.isArray(feedbackModal.possibleCauses) ? feedbackModal.possibleCauses : [],
    reflection_questions: Array.isArray(feedbackModal.reflectionQuestions) ? feedbackModal.reflectionQuestions : [],
    impact: feedbackModal.impactHint || "",
    guiding_question: aiGuidingQuestion.value || "",
    generated_at: new Date().toISOString(),
    ...extra,
  };
}

function secondHintReminderMetadata(extra = {}) {
  return currentHintMetadata({
    requested_hint_no: 2,
    hint_no: 2,
    hint_click_no: 2,
    reminder_type: "second_ai_hint",
    reminder_visible: Boolean(feedbackModal.secondHintNoticeVisible),
    reminder_shown_logged: Boolean(feedbackModal.secondHintReminderShownLogged),
    reminder_resolved: Boolean(feedbackModal.secondHintReminderResolved),
    ...extra,
  });
}

async function logSecondHintReminderEvent(action, extra = {}) {
  const cleanAction = String(action || "").trim();
  if (!cleanAction) return null;
  return logLearningEvent(
    `second_hint_reminder_${cleanAction}`,
    feedbackLearningContext(secondHintReminderMetadata({
      reminder_action: cleanAction,
      ...extra,
    }))
  );
}

async function logSecondHintReminderShown(triggerMethod = "system_recheck_modal") {
  if (!feedbackModal.secondHintNoticeVisible) return null;
  if (feedbackModal.secondHintReminderShownLogged) return null;
  if (feedbackModal.aiHint2Text) return null;
  feedbackModal.secondHintReminderShownLogged = true;
  return logSecondHintReminderEvent("shown", {
    trigger_method: triggerMethod,
  });
}

async function logSecondHintReminderClicked(triggerMethod = "click_result_second_hint") {
  if (feedbackModal.secondHintReminderResolved) return null;
  feedbackModal.secondHintReminderResolved = true;
  return logSecondHintReminderEvent("clicked", {
    trigger_method: triggerMethod,
    button_name: secondHintReminderButtonLabel,
  });
}

async function logSecondHintReminderIgnored(returnMethod = "return_to_fix") {
  if (!feedbackModal.secondHintNoticeVisible) return null;
  if (feedbackModal.secondHintReminderResolved) return null;
  if (feedbackModal.aiHint2Text) return null;
  feedbackModal.secondHintReminderResolved = true;
  return logSecondHintReminderEvent("ignored", {
    return_method: returnMethod,
  });
}

async function updateReviewOpenHintLog(extra = {}) {
  const logId = String(feedbackModal.reviewOpenLogId || "").trim();
  if (!logId) return null;
  try {
    const token = String(localStorage.getItem("token") || "").trim();
    const headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(`${API_BASE}/api/learning_logs/${encodeURIComponent(logId)}/metadata`, {
      method: "PATCH",
      headers,
      body: JSON.stringify({ metadata: currentHintMetadata(extra) }),
    });
    if (!response.ok) {
      console.warn("learning log hint metadata update failed", response.status);
      return null;
    }
    return await response.json().catch(() => null);
  } catch (err) {
    console.warn("learning log hint metadata update failed", err);
    return null;
  }
}

async function closeHintReview(closeMethod) {
  if (!feedbackModal.hintOpen) return false;
  feedbackModal.hintOpen = false;
  await logLearningEvent("hide_hint", feedbackLearningContext(currentHintMetadata({
    close_method: closeMethod,
  })));
  return true;
}

async function closeHintAndReturnToTask(closeMethod) {
  const learningContext = feedbackLearningContext(currentHintMetadata({
    return_method: closeMethod,
  }));
  const closed = await closeHintReview(closeMethod);
  if (!closed) return;
  if (feedbackModal.attemptId) {
    await sendChoice(feedbackModal.attemptId, "no");
  }
  feedbackModal.open = false;
  await logLearningEvent("return_to_task", learningContext);
}

async function dismissFeedbackModal() {
  if (feedbackModal.mode === "ai_hint_flow") {
    await logLearningEvent("ai_hint_modal_close", feedbackLearningContext(currentHintMetadata({
      close_method: "click_blank_area",
    })));
    feedbackModal.open = false;
    return;
  }
  if (feedbackModal.mode === "system_recheck") {
    await logSecondHintReminderIgnored("system_recheck_dismiss");
  }
  const hadOpenHint = feedbackModal.hintOpen;
  const learningContext = feedbackLearningContext(currentHintMetadata({
    close_method: "click_blank_area",
  }));
  if (hadOpenHint) {
    feedbackModal.hintOpen = false;
    await logLearningEvent("hide_hint", learningContext);
  }
  if (feedbackModal.attemptId) {
    await sendChoice(feedbackModal.attemptId, "no");
  }
  feedbackModal.open = false;
  feedbackModal.mode = "legacy";
  feedbackModal.stage = "choice";
  feedbackModal.hintOpen = false;
  feedbackModal.hintLoading = false;
  feedbackModal.hintLoaded = false;
  feedbackModal.hintError = "";
  feedbackModal.hintLimitReached = false;
  feedbackModal.reviewOpen = false;
  feedbackModal.attemptV2Id = "";
  feedbackModal.attemptNo = null;
  feedbackModal.reviewOpenLogId = "";
  feedbackModal.targetConcept = "";
  feedbackModal.hintClicked = false;
  feedbackModal.videoClicked = false;
  feedbackModal.debugText = "";
  feedbackModal.source = "default";
  feedbackModal.aiDiagnosisSummary = "";
  feedbackModal.hintRecord = null;
  feedbackModal.hintId = "";
  feedbackModal.firstSystemHintText = "";
  feedbackModal.aiHint1Text = "";
  feedbackModal.aiHint1Meta = {};
  feedbackModal.aiHint2Text = "";
  feedbackModal.aiHint2Meta = {};
  feedbackModal.secondHintLoading = false;
  feedbackModal.secondHintNoticeVisible = false;
  feedbackModal.secondHintReminderShownLogged = false;
  feedbackModal.secondHintReminderResolved = false;
  feedbackModal.errorClass = "";
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
      metadata: currentHintMetadata({ return_method: "click_blank_area" }),
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
  const openEvent = await logLearningEvent("view_hint", feedbackLearningContext(currentHintMetadata({
    review_type: "ai_hint",
    trigger_method: "click_ai_hint",
    button_name: "AI提示",
  })));
  feedbackModal.reviewOpenLogId = String(openEvent?.log_id || "");
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
  await updateReviewOpenHintLog({
    trigger_method: "click_ai_hint",
    button_name: "AI提示",
    hint_loaded: feedbackModal.hintLoaded,
  });
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
    const token = String(localStorage.getItem("token") || "").trim();
    const headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    await fetch(`${API_BASE}/api/parsons/review_choice`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        attempt_id,
        student_id: (localStorage.getItem("student_id") || ""),
        student_choice: choice,
        session_id: learningSessionId,
        page: "parsons",
        activity_type: isTestMode.value ? "test" : "practice",
        test_role: isTestMode.value ? String(testRole.value || "") : null,
        task_id: feedbackModal.taskId || currentTaskId(),
        attempt_v2_id: feedbackModal.attemptV2Id || null,
        attempt_no: feedbackModal.attemptNo,
        target_concept: feedbackModal.targetConcept || currentTargetConcept(),
        question_id: feedbackModal.taskId || currentTaskId(),
        unit_id: currentUnitId(),
        question_type: "parsons",
        ...extra,
      }),
    });
  } catch (_) {}
}

// 每題作答狀態保存
function savePracticeState(extra = {}) {
  try {
    // 前測、後測不使用本機作答恢復
    if (isTestMode.value) return;

    const taskId =
      task.value?.task_id ||
      task.value?._id ||
      "";

    if (!taskId) return;
    if (!(templateSlots.value || []).length) return;

    const storageKey =
      practiceStateStorageKey(taskId);

    if (!storageKey) return;

    const {
      force = false,
      ...safeExtra
    } = extra || {};

    // 先讀取「目前這一題」自己的狀態。
    // 不再讀取其他題共用的全域 key。
    const previousState =
      readPracticeStateForTask(taskId) || {};

    // 保存每個格子目前放入的 block id。
    const filledMap = (templateSlots.value || []).map(
      (slot) =>
        filled[String(slot.slot)]?.id ||
        null
    );

    const filledBySlot = {};
    const filledBlockSnapshots = {};

    for (const slot of (templateSlots.value || [])) {
      const slotKey = String(slot?.slot ?? "");
      if (!slotKey) continue;

      const block = filled[slotKey];
      if (!block?.id) continue;

      filledBySlot[slotKey] = String(block.id);
      filledBlockSnapshots[slotKey] = JSON.parse(
        JSON.stringify(block)
      );
    }

    const indentMap = JSON.parse(
      JSON.stringify(slotIndentLevel || {})
    );

    const currentWrongIndices =
      Array.isArray(wrongIndices.value)
        ? wrongIndices.value
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value))
        : [];

    const hasCurrentProgress =
      filledMap.some(Boolean) ||
      Object.keys(indentMap).length > 0 ||
      currentWrongIndices.length > 0 ||
      force === true;

    const hadSavedState =
      String(previousState?.task_id || "")
        === String(taskId);

    // 新題第一次進入、完全沒有操作時，不建立空白狀態。
    // 但也絕對不能刪除其他題或本題先前的未完成狀態。
    if (!hasCurrentProgress && !hadSavedState) {
      return;
    }

    const stateObj = {
      ...previousState,

      student_id: String(studentId.value || ""),

      videoId: String(
        task.value?.video_id
        || previousState.videoId
        || videoId.value
        || ""
      ),

      level: String(
        task.value?.level
        || previousState.level
        || route.query.level
        || "L1"
      ),

      task_id: String(taskId),

      // 只有答對時才會透過 clearPracticeStateForTask() 刪除此題。
      is_passed: false,

      task_status:
        safeExtra.task_status ||
        previousState.task_status ||
        "editing",

      filled_map: filledMap,
      filled_by_slot: filledBySlot,
      filled_block_snapshots: filledBlockSnapshots,

      slot_indent_map: indentMap,
      wrong_indices: currentWrongIndices,
      wrong_slot_issue_map: normalizeSlotIssueMap(wrongSlotIssueMap.value),

      primary_wrong_index:
        Number.isFinite(
          Number(primaryWrongIndex.value)
        )
          ? Number(primaryWrongIndex.value)
          : null,

      saved_at: Date.now(),

      ...safeExtra,
    };

    localStorage.setItem(
      storageKey,
      JSON.stringify(stateObj)
    );
  } catch (error) {
    console.warn(
      "savePracticeState failed:",
      error
    );
  }
}


function saveCurrentPracticeBeforeLeave() {
  // 前測、後測不使用本機恢復，避免影響正式測驗。
  if (isTestMode.value) return;

  // 已答對的題目不再保存，避免重新進入後恢復舊答案。
  if (result.value?.is_correct === true) return;

  const hasCurrentProgress =
    Object.values(filled).some((block) => Boolean(block?.id)) ||
    Object.keys(slotIndentLevel).length > 0 ||
    (Array.isArray(wrongIndices.value) && wrongIndices.value.length > 0);

  savePracticeState({
    task_status:
      Array.isArray(wrongIndices.value) && wrongIndices.value.length > 0
        ? "revising"
        : "editing",
    save_reason: "leave_page",
    force: hasCurrentProgress,
  });
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
    const loadedTaskId =
      loadedTask?.task_id ||
      loadedTask?._id ||
      "";

    if (!loadedTaskId) return false;

    // 只讀取「目前這一道題」自己的 localStorage key。
    const st =
      readPracticeStateForTask(loadedTaskId);

    if (!st) return false;

    // 已通過的題目不得恢復舊答案。
    // 正常情況答對時已經會直接刪除此題狀態。
    if (st?.is_passed === true) {
      clearPracticeStateForTask(loadedTaskId);
      return false;
    }

    // 避免同一台電腦切換帳號後，讀到其他學生的答案
    const savedStudentId =
      String(st?.student_id || "").trim();

    const currentStudentId =
      String(studentId.value || "").trim();

    if (
      savedStudentId &&
      currentStudentId &&
      savedStudentId !== currentStudentId
    ) {
      return false;
    }

    // task_id 已足以唯一確認同一題。
    // 不再用 route 的 videoId 作嚴格拒絕條件，避免返回上一頁後
    // route 已更新而把原本可恢復的排列判定為不相符。
    if (String(st.task_id) !== String(loadedTaskId)) {
      return false;
    }

    const byId = new Map(
      (poolBlocks.value || []).map(
        (block) => [String(block.id), block]
      )
    );

    for (const key of Object.keys(filled)) {
      delete filled[key];
    }

    const slots = templateSlots.value || [];
    let restoredCount = 0;

    // 新版：直接依 slot key 還原排列。
    const filledBySlot =
      st?.filled_by_slot &&
      typeof st.filled_by_slot === "object"
        ? st.filled_by_slot
        : {};

    const snapshots =
      st?.filled_block_snapshots &&
      typeof st.filled_block_snapshots === "object"
        ? st.filled_block_snapshots
        : {};

    for (const slot of slots) {
      const slotKey = String(slot?.slot ?? "");
      if (!slotKey) continue;

      const blockId = filledBySlot[slotKey];
      if (!blockId) continue;

      const poolBlock = byId.get(String(blockId));
      const snapshot = snapshots[slotKey];

      const restoredBlock =
        poolBlock ||
        (
          snapshot &&
          String(snapshot?.id || "") === String(blockId)
            ? snapshot
            : null
        );

      if (!restoredBlock) continue;

      filled[slotKey] =
        normalizeFilledBlock(restoredBlock);

      restoredCount += 1;
    }

    // 相容舊版 localStorage：若沒有 filled_by_slot，
    // 再使用原本的 index-based filled_map。
    if (
      restoredCount === 0 &&
      Array.isArray(st.filled_map)
    ) {
      st.filled_map.forEach((blockId, index) => {
        if (!blockId) return;

        const slotKey = slots[index]?.slot;
        if (slotKey == null) return;

        const poolBlock =
          byId.get(String(blockId));

        if (!poolBlock) return;

        filled[String(slotKey)] =
          normalizeFilledBlock(poolBlock);

        restoredCount += 1;
      });
    }

    // localStorage 有資料但一格都無法還原時，不假裝成功。
    if (restoredCount === 0) {
      return false;
    }

    // 回到同題時保留上次錯誤標記，直到學生修正正確為止。
    const restoredWrong = Array.isArray(st.wrong_indices)
      ? st.wrong_indices.map((v) => Number(v)).filter((v) => Number.isFinite(v))
      : [];
    wrongIndices.value = restoredWrong;
    const restoredIssueMap = normalizeSlotIssueMap(st.wrong_slot_issue_map);
    wrongSlotIssueMap.value = Object.keys(restoredIssueMap).length
      ? restoredIssueMap
      : defaultSequenceIssueMap(restoredWrong);

    const restoredPrimary = Number(st.primary_wrong_index);
    if (Number.isFinite(restoredPrimary)) {
      primaryWrongIndex.value = restoredPrimary;
    } else {
      primaryWrongIndex.value = restoredWrong.length ? restoredWrong[0] : null;
    }

    try {
      const m = st.slot_indent_map || {};
      for (const k of Object.keys(slotIndentLevel)) delete slotIndentLevel[k];
      for (const k of Object.keys(m)) {
        slotIndentLevel[String(k)] =
          Number(m[k] || 0);
      }

      nextTick(() => {
        applyIndentStylesToDOM();
      });
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

// 收集 block IDs
// 組合含縮排的 answer_lines
// 呼叫 /api/parsons/submit
// 更新紅色位置
// 保存未完成狀態
// 依 hint_flow 顯示不同 Modal

async function submit() {
  if (state.submitting || state.noTask) return;
  state.submitting = true;
  state.err = "";
  result.value = null;
  wrongIndices.value = [];
  primaryWrongIndex.value = null;
  firstHintPanel.visible = false;
  feedbackModal.open = false;

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
        task_id: String(currentTaskId() || ""),
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
      headers: authHeaders({ "Content-Type": "application/json" }),
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

    // 每次送出後，以本次作答的系統判定結果更新紅色標記。
    // 優先使用 incorrect_slots，包含順序錯誤與縮排錯誤。
    wrongIndices.value = collectCurrentWrongPositions(r || {});
    wrongSlotIssueMap.value = buildWrongSlotIssueMap(
      r || {},
      r?.hint_flow && typeof r.hint_flow === "object" ? r.hint_flow : {},
      {}
    );

    const parts = buildWrongFeedbackParts(r || {});
    primaryWrongIndex.value = (parts.focusIndex != null ? Number(parts.focusIndex) : null);

    // 尚未答對時保存本次送出的排列、縮排與紅色錯誤標記。
    if (r?.ok && r?.is_correct === false) {
      savePracticeState({
        task_status: "revising",
        attempt_id: String(r?.attempt_id || ""),
        attempt_v2_id: String(r?.attempt_v2_id || ""),
        attempt_no: Number(r?.attempt_no || 0) || null,
        feedback_shown: true,
        save_reason: "incorrect_submit",
        force: true,
      });
    }

    if (r?.ok && r?.is_correct === true) {
      // 只有答對，才清除此題的排列、縮排、紅標與修正狀態。
      // 其他尚未完成的題目仍各自保留。
      clearPracticeStateForTask(task_id);
      wrongSlotIssueMap.value = {};
      await openSuccessModal(r);
    }

  const submitHintFlow =
    r?.hint_flow && typeof r.hint_flow === "object"
      ? r.hint_flow
      : {};
  const submitFeedbackStrategy = String(
    r?.feedback_strategy ||
    submitHintFlow?.feedback_strategy ||
    feedbackStrategy.value ||
    "B"
  ).trim().toUpperCase();

  // C 策略：錯誤位置與固定中文語意直接顯示在題目槽位。
  // 此研究條件不顯示第一次錯誤 Modal，也不開啟 AI Modal。
  if (
    r?.ok &&
    r?.is_correct === false &&
    (
      submitFeedbackStrategy === "C" ||
      submitHintFlow?.type === "fixed_semantic_feedback"
    )
  ) {
    // 此時 wrongIndices 已由後端結果更新。
    // C 策略每次答錯都顯示固定回饋 modal，並保留錯誤格紅色標記。
    showFixedSemanticFeedbackModal(r, task_id);
    return;
  }

  const usesAiHintFlow = ["A", "B"].includes(submitFeedbackStrategy);

  // A 策略未正式接上反思題前，不借用 B 策略的 AI modal。
  // 這樣可以確保 A/B/C 三組研究條件不會混在一起。
  if (
    r?.ok &&
    r?.is_correct === false &&
    !usesAiHintFlow
  ) {
    feedbackModal.open = false;
    return;
  }

  // B 策略：維持既有「第二次錯誤後 AI 提示」流程。
  if (
    r?.ok &&
    r?.is_correct === false &&
    usesAiHintFlow
  ) {
    const flow = submitHintFlow;

    const attemptNo = Number(r?.attempt_no || 0);
    const wrongAttemptCount = Number(
      flow.wrong_attempt_count
      ?? r?.wrong_attempt_count
      ?? 0
    );
    const aiHintRecoveryNeeded =
      flow.type === "system_recheck" &&
      flow.existing_ai_hint_available !== true &&
      (wrongAttemptCount >= 2 || attemptNo >= 2);

    // 第二次錯誤：系統先重新判斷位置，再開啟 AI 提示。
    if (
      flow.type === "ai_hint"
      || flow.auto_open_ai === true
      || wrongAttemptCount === 2
      || attemptNo === 2
      || aiHintRecoveryNeeded
    ) {
      openAiHintModalFromFlow(r, task_id);
      return;
    }

    // 第一次錯誤：只顯示系統判定的錯誤位置
    if (
      flow.type === "first_system_hint"
      || (!flow.type && wrongAttemptCount <= 1)
      || (!flow.type && !wrongAttemptCount && attemptNo === 1)
    ) {
      showFirstSystemHint(r, task_id);
      return;
    }

    // 第三次以上錯誤：只顯示本次最新錯誤位置、格數與紅色標記，
    // 不再把第一次或第二次的位置合併進來，也不自動重新生成 AI。
    if (
      flow.type === "system_recheck"
      || (!flow.type && wrongAttemptCount >= 3)
      || (!flow.type && !wrongAttemptCount && attemptNo >= 3)
    ) {
      showSystemRecheck(r, task_id);
      return;
    }

    // 若後端未回傳提示類型，採保守處理
    showFirstSystemHint(r, task_id);
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

  const payloadFeedbackStrategy = String(
    t.feedback_strategy || "B"
  ).trim().toUpperCase();

  // 後端會負責 Z 分配並寫入 A/B/C。
  // 這裡保留原始策略值，避免未來 A 策略被前端誤改成 B。
  // 目前只有 C 會切換固定中文語意回饋；A 尚未實作時暫走既有 B 流程。
  t.feedback_strategy = ["A", "B", "C"].includes(payloadFeedbackStrategy)
    ? payloadFeedbackStrategy
    : "B";

  // B 維持老師原本 hide_semantic_zh 設定。
  // C 必須保留中文語意資料，但仍只在錯誤格顯示。
  const hideSemanticZh =
    t.feedback_strategy === "C"
      ? false
      : !!t.hide_semantic_zh;

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

async function loadPersistedHintState() {
  const taskId = currentTaskId();

  // C 策略不使用 AI 提示，也不應載入過去 B 策略留下的 hint record。
  if (
    !taskId ||
    isTestMode.value ||
    isCStrategy.value
  ) {
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/parsons/hint_state?task_id=${encodeURIComponent(taskId)}`, {
      headers: authHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    const record = data?.hint_record;
    if (!res.ok || !record) return;
    feedbackModal.hintRecord = record;
    feedbackModal.hintId = String(record.hint_id || "");
    feedbackModal.attemptId = String(record.last_attempt_id || feedbackModal.attemptId || "");
    feedbackModal.taskId = String(record.task_id || taskId || "");
    feedbackModal.firstSystemHintText = String(record.first_system_hint_text || "");
    feedbackModal.aiHint1Text = String(record.ai_hint_1_text || "");
    feedbackModal.aiHint1Meta = sanitizeHintMeta(record.ai_hint_1_meta);
    feedbackModal.aiHint2Text = String(record.ai_hint_2_text || "");
    feedbackModal.aiHint2Meta = sanitizeHintMeta(record.ai_hint_2_meta);
    feedbackModal.activeAiHintNo = 1;
  } catch (_) {}
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
      const norm = normalizeTaskPayload(r);
      task.value = norm.taskObj;
      poolBlocks.value = norm.pool;
      teacherForcedHideSemantic.value = !!norm.taskObj?.hide_semantic_zh;
      // 前後測不顯示中文提示：清空 label
      templateSlots.value = (norm.slots || []).map((s) => ({ ...s, label: "" }));

      await bindBlankFocusHandlers();
      await recordEnterParsonsTaskFromVideo();
      await recordTaskOpen();
      return;
    }

    // 練習模式
    const level = route.query.level ? String(route.query.level) : "L1";
    const url = `${API_BASE}/api/parsons/task?video_id=${encodeURIComponent(String(videoId.value || ""))}&level=${encodeURIComponent(level)}`;
    // 載入 Parsons 題目時必須帶登入 token。
    // 後端會用目前登入學生的 users.feedback_strategy 判斷要走 B 或 C 策略。
    const res = await fetch(url, {
      headers: authHeaders(),
    });

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

    // C 策略不得保留上一個 B 題目的 AI 狀態
    if (isCStrategy.value) {
      firstHintPanel.visible = false;
      feedbackModal.open = false;
      feedbackModal.hintRecord = null;
      feedbackModal.hintId = "";
      feedbackModal.aiHint1Text = "";
      feedbackModal.aiHint1Meta = {};
      feedbackModal.aiHint2Text = "";
      feedbackModal.aiHint2Meta = {};
    }

    const restored = restorePracticeStateIfMatch(task.value);
    await loadPersistedHintState();

    await bindBlankFocusHandlers();
    await recordEnterParsonsTaskFromVideo();
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

  window.addEventListener(
    "pagehide",
    saveCurrentPracticeBeforeLeave
  );

  window.addEventListener(
    "beforeunload",
    saveCurrentPracticeBeforeLeave
  );
});


onBeforeRouteLeave(() => {
  saveCurrentPracticeBeforeLeave();
});

onBeforeUnmount(() => {
  // SPA 返回上一頁時，onBeforeRouteLeave 已經在舊 route 上保存。
  // 這裡不可再次保存，否則可能使用新 route 覆蓋正確的題目身分。
  if (_docKeydownHandler) {
    document.removeEventListener(
      "keydown",
      _docKeydownHandler,
      true
    );
  }

  window.removeEventListener(
    "pagehide",
    saveCurrentPracticeBeforeLeave
  );

  window.removeEventListener(
    "beforeunload",
    saveCurrentPracticeBeforeLeave
  );

  try {
    (_blankCleanupFns || []).forEach((fn) => {
      try {
        fn();
      } catch (_) {}
    });
  } catch (_) {}

  _blankCleanupFns = [];
});

watch(
  () => [
    videoId.value,
    route.query.level,
    route.query.mode,
    route.query.test_role,
    route.query.test_cycle_id,
    route.query.test_index,
  ],
  async () => {
    // 同一個 Parsons 元件切換到另一題／另一影片時，
    // 先保存舊題排列，再讓 loadTask() 清空畫面。
    saveCurrentPracticeBeforeLeave();
    await loadTask();
  }
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

.slot-error-tags{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.slot-error-tag{
  display: inline-flex;
  align-items: center;
  width: fit-content;
  max-width: 100%;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 13px;
  font-weight: 900;
  line-height: 1.25;
  border: 1px solid transparent;
}

.slot-error-tag.sequence{
  color: #7f1d1d;
  background: #fee2e2;
  border-color: #fecaca;
}

.slot-error-tag.indentation{
  color: #7c2d12;
  background: #ffedd5;
  border-color: #fed7aa;
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
  background: linear-gradient(90deg, #ffe1e1 0%, #ffc8c8 100%);
  color: #7f1d1d;
}
.cloze-row.affected .blank{
  border-color: var(--danger) !important;
  box-shadow: 0 0 0 2px rgba(185, 28, 28, .12);
}

/* C 策略固定提示樣式：
   答錯後只在錯誤格顯示題目預先存好的 expected_meaning_zh。
   這不是 AI 生成提示，所以只用紅色語意條與紅色虛線框提醒學生檢查。 */
.cloze-row.c-wrong .hint{
  background: linear-gradient(90deg, #fecaca 0%, #fda4af 100%);
  color: #6b1f1f;
  border-radius: 10px;
}

.cloze-row.c-wrong .blank{
  border-style: dashed !important;
  border-color: #dc2626 !important;
  background: #fffafa;
  box-shadow: 0 0 0 2px rgba(220, 38, 38, .12);
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

.first-hint-panel{
  margin-top: 12px;
  padding: 14px 16px;
  border-radius: var(--radius-md);
  border: 1px solid #fecaca;
  background: #fff7f7;
  box-shadow: var(--shadow-soft);
}

.first-hint-title{
  font-size: 18px;
  font-weight: 900;
  color: #b91c1c;
  margin-bottom: 8px;
}

.first-hint-meta{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 10px;
}

.first-hint-meta span{
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 4px 10px;
  background: #fee2e2;
  color: #7f1d1d;
  font-size: 13px;
  font-weight: 900;
}

.first-hint-text{
  font-size: 15px;
  font-weight: 800;
  line-height: 1.6;
  color: #1f2937;
}

.second-hint-reminder{
  max-width: 980px;
  margin: 14px auto 0;
  padding: 14px 16px;
  border: 1px solid #bfdbfe;
  border-radius: 12px;
  background: #eff6ff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.1);
}

.second-hint-reminder.in-modal{
  max-width: none;
  margin: 12px 0 0;
}

.second-hint-reminder-copy{
  min-width: 0;
}

.second-hint-reminder-title{
  color: #1d4ed8;
  font-size: 16px;
  font-weight: 900;
}

.second-hint-reminder-text{
  margin-top: 4px;
  color: #334155;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.45;
}

.second-hint-reminder-btn{
  flex: 0 0 auto;
  white-space: nowrap;
}

.floating-ai-hint{
  position: fixed;
  right: 22px;
  bottom: 22px;
  z-index: 900;
  border: 0;
  border-radius: 999px;
  padding: 12px 18px;
  background: #2563eb;
  color: #fff;
  font-weight: 900;
  box-shadow: 0 14px 30px rgba(37, 99, 235, 0.25);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 0;
}

.floating-ai-hint-label{
  font-size: 14px;
  line-height: 1;
}

.floating-ai-hint-badge{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: #dc2626;
  color: #fff;
  font-size: 12px;
  line-height: 1;
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.95);
}

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
  position: relative;
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

.ai-hint-close-icon{
  position: absolute;
  top: 14px;
  right: 16px;
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: #64748b;
  font-size: 34px;
  line-height: 1;
  cursor: pointer;
}

.ai-hint-close-icon:hover{
  background: #eef2ff;
  color: #1d4ed8;
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

.fb-title.ai-hint-title{
  color: #1557b7;
  padding-right: 44px;
}

.fb-title.first-error-title{
  color: #b91c1c;
}

.fb-section{
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 12px;
  background: #ffffff;
  margin-top: 10px;
}

.ai-hint-content{
  min-height: 128px;
  padding: 16px 18px;
  border-radius: 12px;
  border: 1px solid #bfdbfe;
  background: #eff6ff;
  white-space: pre-wrap;
}

.ai-hint-meta-card{
  margin: 8px 0 10px;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid #dbeafe;
  background: #f8fbff;
  color: #1e3a8a;
  font-size: 13px;
  line-height: 1.55;
}

.ai-hint-meta-row{
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
}

.ai-hint-meta-row + .ai-hint-meta-row{
  margin-top: 4px;
}

.ai-hint-meta-label{
  flex: 0 0 auto;
  font-weight: 900;
  color: #334155;
}

.ai-hint-meta-value{
  min-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
  font-weight: 800;
}

.fb-title.ai-hint-title ~ .fb-section .fb-note{
  display: inline-block;
  margin-top: 12px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #1e3a8a;
  font-size: 15px;
}

.ai-hint-error{
  margin-top: 10px;
  color: #b91c1c;
  font-size: 14px;
  font-weight: 800;
}

.first-error-section{
  border-color: #fecaca;
  background: #fffafa;
}

.fixed-feedback-section{
  border-color: #fecaca;
  background: #fffafa;
}

.fixed-feedback-section .first-hint-meta span{
  background: #fee2e2;
  color: #7f1d1d;
}

.first-hint-meta.in-modal{
  margin-top: 4px;
}

.first-error-text{
  margin-top: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid #fecaca;
  background: #fff1f2;
  color: #7f1d1d;
  white-space: pre-wrap;
}

.first-error-note{
  margin-top: 12px;
  color: #7f1d1d;
  background: #fff7ed;
  border-color: #fed7aa;
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

.fb-text.muted{
  color: #64748b;
  font-weight: 800;
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
  .floating-ai-hint{
    right: 14px;
    bottom: 14px;
  }
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
