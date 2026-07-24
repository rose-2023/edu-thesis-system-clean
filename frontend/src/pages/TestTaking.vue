<!-- 前測、後測頁面 -->
<template>
  <div class="test-page">
    <header class="test-header">
      <div>
        <!-- <div class="eyebrow">正式測驗</div>
        <h1>{{ testTitle }}</h1> -->
      </div>
      <div class="progress-pill">
        第 {{ testMeta.current_index }} / {{ testMeta.total }} 題
      </div>
    </header>

    <section class="question-box">
      <div class="question-label">題目</div>
      <div class="question-text">
        {{ task?.question_text || (state.loading ? "載入中..." : "目前沒有題目") }}
      </div>
    </section>

    <p v-if="state.err && !state.loading" class="message error">
      {{ state.err }}
    </p>

    <section v-if="state.loading" class="loading-panel">
      載入測驗題目中...
    </section>

    <section v-else-if="state.noTask" class="loading-panel">
      {{ state.err || "目前沒有可作答的測驗題目。" }}
    </section>

    <section v-else-if="isChoiceQuestion" class="choice-panel">
      <button
        v-for="option in choiceOptions"
        :key="option.key"
        type="button"
        class="choice-option"
        :class="{ selected: selectedChoiceKey === option.key }"
        @click="selectChoice(option)"
      >
        <span class="choice-key">{{ option.key }}</span>
        <span class="choice-text">{{ option.text }}</span>
      </button>
    </section>

    <section v-else class="parsons-board">
      <div class="panel answer-panel">
        <div class="panel-title">題目作答程式區</div>
        <div class="slots">
          <div
            v-for="(slot, index) in templateSlots"
            :key="slot.slot"
            class="slot-row"
          >
            <div class="slot-label">第 {{ index + 1 }} 格</div>
            <div
              class="blank"
              :class="{ filled: !!filled[slot.slot], over: overSlot === slot.slot }"
              @dragenter.prevent="onDragOver(slot.slot, $event)"
              @dragover.prevent="onDragOver(slot.slot, $event)"
              @dragleave="onDragLeave"
              @drop.prevent="onDrop(slot.slot, $event)"
            >
              <span
                v-if="filled[slot.slot]"
                class="filled-block"
                draggable="true"
                :style="{ marginLeft: `${indentSpaces(slot.slot) * 8}px` }"
                @dragstart.stop="onDragStart(filled[slot.slot], $event)"
                @dragend="onDragEnd"
              >
                {{ filled[slot.slot].text }}
              </span>
              <span v-else class="placeholder">把右邊片段拖到這裡</span>
              <button
                v-if="filled[slot.slot]"
                type="button"
                class="remove"
                title="移除片段"
                @click="removeFromSlot(slot.slot)"
              >
                ×
              </button>
            </div>
            <div v-if="filled[slot.slot]" class="indent-tools">
              <span>縮排 {{ indentSpaces(slot.slot) }} 空格</span>
              <button type="button" @click="changeIndent(slot.slot, -4)">−</button>
              <button type="button" @click="changeIndent(slot.slot, 4)">+</button>
            </div>
          </div>
        </div>
      </div>

      <div class="panel pool-panel">
        <div class="panel-title">程式片段</div>
        <div class="pool">
          <div
            v-for="block in poolBlocks"
            :key="block.id"
            class="pill"
            :class="{ used: isUsed(block.id) }"
            draggable="true"
            @dragstart="onDragStart(block, $event)"
            @dragend="onDragEnd"
          >
            {{ block.text }}
          </div>
        </div>
      </div>
    </section>

    <div class="actions">
      <!-- <button class="btn ghost" type="button" :disabled="state.submitting" @click="goHome">
        返回首頁
      </button> -->
      <button
        class="btn submit"
        type="button"
        :disabled="state.loading || state.noTask || state.submitting"
        @click="submitAnswer"
      >
        {{ state.submitting ? "送出中..." : "送出答案" }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";

const API_BASE = (import.meta?.env?.VITE_API_BASE || "").replace(/\/$/, "");
const PRETEST_HISTORY_LOCK_KEY = "__pretest_history_lock";
const route = useRoute();
const router = useRouter();
let hasPretestHistoryLock = false;

const state = reactive({
  loading: false,
  submitting: false,
  noTask: false,
  err: "",
});

const task = ref(null);
const canLeaveTest = ref(false);
const testQuestionType = ref("parsons");
const choiceOptions = ref([]);
const selectedChoiceKey = ref("");
const poolBlocks = ref([]);
const templateSlots = ref([]);
const filled = reactive({});
const slotIndentSpaces = reactive({});
const dragging = ref(null);
const overSlot = ref(null);

const testMeta = reactive({
  total: 1,
  current_index: 1,
  request_index: 1,
  started_at_ms: 0,
});

const studentId = computed(() => (
  localStorage.getItem("student_id") ||
  localStorage.getItem("studentId") ||
  ""
).trim());

const participantId = computed(() => (
  localStorage.getItem("participant_id") ||
  localStorage.getItem("participantId") ||
  ""
).trim());

const testCycleId = computed(() => String(
  route.query.test_cycle_id || localStorage.getItem("test_cycle_id") || ""
).trim());

const testRole = computed(() => {
  const raw = String(route.query.test_role || "pre").trim().toLowerCase();
  if (raw === "post" || raw === "posttest") return "post";
  return "pre";
});

const testTitle = computed(() => (testRole.value === "post" ? "後測" : "前測"));
const isChoiceQuestion = computed(() => testQuestionType.value === "choice");
const testProgressKey = computed(() => {
  if (!studentId.value || !testCycleId.value || !testRole.value) return "";
  return `test-taking-progress:${studentId.value}:${testCycleId.value}:${testRole.value}`;
});
const testDraftKey = computed(() => (
  testProgressKey.value ? `${testProgressKey.value}:draft` : ""
));

function savedTestProgressIndex() {
  if (!testProgressKey.value) return 1;
  try {
    const savedIndex = Number(sessionStorage.getItem(testProgressKey.value));
    return Number.isInteger(savedIndex) && savedIndex > 0 ? savedIndex : 1;
  } catch (_) {
    return 1;
  }
}

function saveTestProgressIndex(index) {
  if (!testProgressKey.value) return;
  const parsedIndex = Number(index);
  if (!Number.isInteger(parsedIndex) || parsedIndex <= 0) return;
  try {
    sessionStorage.setItem(testProgressKey.value, String(parsedIndex));
  } catch (_) {}
}

function clearTestProgress() {
  if (!testProgressKey.value) return;
  try {
    sessionStorage.removeItem(testProgressKey.value);
    sessionStorage.removeItem(testDraftKey.value);
  } catch (_) {}
}

function routeOrSavedTestIndex() {
  const routeIndex = Number(route.query.test_index);
  return Number.isInteger(routeIndex) && routeIndex > 0
    ? routeIndex
    : savedTestProgressIndex();
}

function clearActiveTestDraft() {
  if (!testDraftKey.value) return;
  try {
    sessionStorage.removeItem(testDraftKey.value);
  } catch (_) {}
}

function saveActiveTestDraft() {
  const taskId = String(task.value?.task_id || task.value?.question_id || "").trim();
  if (!testDraftKey.value || !taskId) return;

  const filledBlocks = {};
  const indentSpaces = {};
  for (const slot of templateSlots.value) {
    const slotKey = String(slot.slot);
    if (filled[slotKey]?.id) filledBlocks[slotKey] = String(filled[slotKey].id);
    if (slotIndentSpaces[slotKey] != null) indentSpaces[slotKey] = Number(slotIndentSpaces[slotKey]) || 0;
  }

  try {
    sessionStorage.setItem(testDraftKey.value, JSON.stringify({
      current_index: Number(testMeta.current_index || 1),
      task_id: taskId,
      question_type: testQuestionType.value,
      selected_choice_key: selectedChoiceKey.value,
      filled_blocks: filledBlocks,
      indent_spaces: indentSpaces,
    }));
  } catch (_) {}
}

function restoreActiveTestDraft() {
  const taskId = String(task.value?.task_id || task.value?.question_id || "").trim();
  if (!testDraftKey.value || !taskId) return;

  try {
    const saved = JSON.parse(sessionStorage.getItem(testDraftKey.value) || "null");
    if (
      !saved ||
      Number(saved.current_index) !== Number(testMeta.current_index) ||
      String(saved.task_id || "") !== taskId
    ) return;

    if (testQuestionType.value === "choice") {
      const selected = String(saved.selected_choice_key || "").trim().toUpperCase();
      if (choiceOptions.value.some((option) => option.key === selected)) {
        selectedChoiceKey.value = selected;
      }
      return;
    }

    const savedBlocks = saved.filled_blocks || {};
    const savedIndents = saved.indent_spaces || {};
    for (const slot of templateSlots.value) {
      const slotKey = String(slot.slot);
      const blockId = String(savedBlocks[slotKey] || "");
      const block = poolBlocks.value.find((item) => String(item.id) === blockId);
      if (block) filled[slotKey] = block;
      const indent = Number(savedIndents[slotKey]);
      if (Number.isFinite(indent) && indent >= 0) slotIndentSpaces[slotKey] = indent;
    }
  } catch (_) {
    clearActiveTestDraft();
  }
}

function leaveTest(destination) {
  canLeaveTest.value = true;
  return router.replace(destination);
}

function pretestLocation() {
  return {
    path: "/test/taking",
    query: {
      mode: "test",
      test_role: "pre",
      test_cycle_id: testCycleId.value,
    },
  };
}

function shouldLockPretestNavigation() {
  return testRole.value === "pre" && !canLeaveTest.value;
}

function installPretestHistoryLock() {
  if (!shouldLockPretestNavigation() || hasPretestHistoryLock) return;
  const currentState = window.history.state && typeof window.history.state === "object"
    ? window.history.state
    : {};
  window.history.pushState(
    { ...currentState, [PRETEST_HISTORY_LOCK_KEY]: true },
    "",
    window.location.href,
  );
  hasPretestHistoryLock = true;
}

function restorePretestAfterBrowserNavigation(event) {
  if (!shouldLockPretestNavigation()) return;

  const expectedPath = router.resolve(pretestLocation()).fullPath;
  const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (currentPath === expectedPath && !event?.state?.[PRETEST_HISTORY_LOCK_KEY] && hasPretestHistoryLock) {
    window.history.go(1);
    return;
  }

  window.setTimeout(() => {
    if (!shouldLockPretestNavigation()) return;
    const expectedPath = router.resolve(pretestLocation()).fullPath;
    const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (currentPath !== expectedPath) router.replace(pretestLocation());
  }, 0);
}

function confirmBeforeLeavingPretest(event) {
  if (!shouldLockPretestNavigation()) return undefined;
  event.preventDefault();
  event.returnValue = "";
  return "";
}

function authHeaders(base = {}) {
  const headers = { ...base };
  const token = String(localStorage.getItem("token") || "").trim();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function currentTaskId() {
  return String(task.value?.task_id || task.value?.question_id || task.value?._id || "").trim();
}

function normalizeChoiceOptions(rawOptions = []) {
  const list = Array.isArray(rawOptions) ? rawOptions : [];
  const labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  return list
    .map((option, index) => {
      const fallbackKey = labels[index] || String(index + 1);
      if (option && typeof option === "object") {
        return {
          key: String(option.key || option.label || option.value || fallbackKey).trim().toUpperCase(),
          text: String(option.text || option.label_text || option.content || option.value || "").trim(),
        };
      }
      const rawText = String(option || "").trim();
      const match = rawText.match(/^\s*([A-Za-z])[\.\)]\s*(.+)$/);
      return {
        key: match ? match[1].toUpperCase() : fallbackKey,
        text: match ? match[2].trim() : rawText,
      };
    })
    .filter((option) => option.key && option.text);
}

function normalizeTestQuestionType(payload = {}) {
  const raw = String(payload?.question_type || payload?.type || "").trim().toLowerCase();
  if (["choice", "choices", "mcq", "multiple_choice", "single_choice"].includes(raw)) return "choice";
  if (Array.isArray(payload?.options) && payload.options.length) return "choice";
  return "parsons";
}

function normalizeBlock(block, index = 0) {
  const raw = block && typeof block === "object" ? block : {};
  const id = String(raw.id || raw.block_id || raw._id || `b${index + 1}`).trim();
  return {
    ...raw,
    id,
    text: String(raw.text ?? raw.code ?? ""),
    type: raw.type || "solution",
  };
}

function normalizeParsonsPayload(payload = {}) {
  const base = payload.task && typeof payload.task === "object" ? payload.task : payload;
  const solutionBlocks = Array.isArray(base.solution_blocks)
    ? base.solution_blocks.map(normalizeBlock)
    : [];
  const distractorBlocks = Array.isArray(base.distractor_blocks)
    ? base.distractor_blocks.map((block, index) => normalizeBlock(block, solutionBlocks.length + index))
    : [];
  const poolRaw = Array.isArray(base.pool) && base.pool.length
    ? base.pool
    : [...solutionBlocks, ...distractorBlocks];

  const seen = new Set();
  const pool = poolRaw
    .map(normalizeBlock)
    .filter((block) => {
      if (!block.id || seen.has(block.id)) return false;
      seen.add(block.id);
      return true;
    });

  let slots = Array.isArray(base.template_slots) ? base.template_slots : [];
  if (!slots.length && solutionBlocks.length) {
    slots = solutionBlocks.map((block, index) => ({
      slot: `s${index + 1}`,
      expected_id: block.id,
    }));
  }

  return {
    taskObj: {
      ...base,
      task_id: String(base.task_id || base.question_id || payload.task_id || payload.question_id || ""),
      question_id: String(base.question_id || base.task_id || payload.question_id || payload.task_id || ""),
      question_text: String(base.question_text || payload.question_text || base.instruction || base.title || ""),
    },
    pool,
    slots: slots.map((slot, index) => ({
      ...slot,
      slot: String(slot?.slot || `s${index + 1}`),
      label: `第 ${index + 1} 格`,
    })),
  };
}

function resetAnswerState() {
  selectedChoiceKey.value = "";
  choiceOptions.value = [];
  poolBlocks.value = [];
  templateSlots.value = [];
  for (const key of Object.keys(filled)) delete filled[key];
  for (const key of Object.keys(slotIndentSpaces)) delete slotIndentSpaces[key];
  dragging.value = null;
  overSlot.value = null;
  state.err = "";
}

function applyTaskPayload(payload = {}) {
  resetAnswerState();
  testQuestionType.value = normalizeTestQuestionType(payload);

  if (testQuestionType.value === "choice") {
    task.value = {
      ...payload,
      task_id: String(payload.task_id || payload.question_id || ""),
      question_id: String(payload.question_id || payload.task_id || ""),
      question_type: "choice",
      question_text: String(payload.question_text || payload.stem || payload.question || ""),
    };
    choiceOptions.value = normalizeChoiceOptions(payload.options || []);
    restoreActiveTestDraft();
    return;
  }

  const normalized = normalizeParsonsPayload(payload);
  task.value = normalized.taskObj;
  poolBlocks.value = normalized.pool;
  templateSlots.value = normalized.slots;
  restoreActiveTestDraft();
}

async function loadTask(index = null) {
  if (!studentId.value) {
    leaveTest("/login");
    return;
  }

  state.loading = true;
  state.noTask = false;
  state.err = "";

  try {
    const requestedIndex = Number(index || route.query.test_index || testMeta.request_index || 1);
    testMeta.request_index = Number.isFinite(requestedIndex) && requestedIndex > 0 ? requestedIndex : 1;

    const qs = new URLSearchParams({
      student_id: studentId.value,
      test_role: testRole.value,
      test_cycle_id: testCycleId.value,
      index: String(testMeta.request_index),
    });

    const response = await fetch(`${API_BASE}/api/parsons/test/task?${qs.toString()}`, {
      headers: authHeaders(),
    });

    const data = await response.json().catch(() => ({}));
    if (response.status === 403 && data?.error === "pretest_questionnaire_required") {
      leaveTest("/pretest-survey");
      return;
    }
    if (!response.ok || data?.ok === false) {
      state.noTask = true;
      state.err = data?.message || "測驗題目載入失敗。";
      task.value = null;
      return;
    }

    testMeta.total = Number(data.total || 1);
    testMeta.current_index = Number(data.current_index || testMeta.request_index || 1);
    testMeta.request_index = testMeta.current_index;
    saveTestProgressIndex(testMeta.current_index);
    testMeta.started_at_ms = Date.now();
    applyTaskPayload(data);
  } catch (error) {
    state.noTask = true;
    state.err = error?.message || "測驗題目載入失敗。";
    task.value = null;
  } finally {
    state.loading = false;
  }
}

function selectChoice(option) {
  selectedChoiceKey.value = String(option?.key || "").trim().toUpperCase();
  state.err = "";
  saveActiveTestDraft();
}

function slotKeys() {
  return templateSlots.value.map((slot) => String(slot.slot));
}

function findSlotKeyByBlockId(blockId) {
  const id = String(blockId || "");
  return slotKeys().find((key) => String(filled[key]?.id || "") === id) || null;
}

function findPoolBlockById(blockId) {
  const id = String(blockId || "");
  return poolBlocks.value.find((block) => String(block.id) === id) || null;
}

function isUsed(blockId) {
  return Boolean(findSlotKeyByBlockId(blockId));
}

function onDragStart(block, event = null) {
  dragging.value = {
    block,
    fromSlotKey: findSlotKeyByBlockId(block?.id),
  };
  try {
    if (event?.dataTransfer && block?.id != null) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(block.id));
    }
  } catch (_) {}
}

function onDragEnd() {
  dragging.value = null;
  overSlot.value = null;
}

function onDragOver(slotKey, event = null) {
  overSlot.value = slotKey;
  try {
    if (event?.dataTransfer) event.dataTransfer.dropEffect = "move";
  } catch (_) {}
}

function onDragLeave() {
  overSlot.value = null;
}

function writeSlotRecords(records) {
  const keys = slotKeys();
  for (const key of keys) delete filled[key];
  records.slice(0, keys.length).forEach((block, index) => {
    if (block) filled[keys[index]] = block;
  });
}

function insertBlockLikePuzzle(targetSlotKey, block, fromSlotKey = null) {
  const keys = slotKeys();
  const targetIndex = keys.indexOf(String(targetSlotKey));
  if (targetIndex < 0 || !block) return;

  const records = keys.map((key) => filled[key] || null);
  const sourceIndex = fromSlotKey ? keys.indexOf(String(fromSlotKey)) : -1;

  if (sourceIndex >= 0) {
    const [moving] = records.splice(sourceIndex, 1);
    const insertIndex = sourceIndex < targetIndex ? targetIndex : targetIndex;
    records.splice(insertIndex, 0, moving || block);
  } else {
    records.splice(targetIndex, 0, block);
  }

  while (records.length > keys.length) records.pop();
  while (records.length < keys.length) records.push(null);
  writeSlotRecords(records);
}

function onDrop(slotKey, event = null) {
  overSlot.value = null;
  let payload = dragging.value;

  if (!payload?.block) {
    const fallbackId = event?.dataTransfer?.getData("text/plain") || "";
    const fallbackBlock = findPoolBlockById(fallbackId);
    if (fallbackBlock) {
      payload = {
        block: fallbackBlock,
        fromSlotKey: findSlotKeyByBlockId(fallbackBlock.id),
      };
    }
  }

  if (!payload?.block) return;
  insertBlockLikePuzzle(slotKey, payload.block, payload.fromSlotKey);
  dragging.value = null;
  state.err = "";
  saveActiveTestDraft();
}

function removeFromSlot(slotKey) {
  delete filled[String(slotKey)];
  delete slotIndentSpaces[String(slotKey)];
  saveActiveTestDraft();
}

function indentSpaces(slotKey) {
  return Number(slotIndentSpaces[String(slotKey)] || 0);
}

function changeIndent(slotKey, delta) {
  const key = String(slotKey);
  const next = Math.max(0, Math.min(12, indentSpaces(key) + Number(delta || 0)));
  slotIndentSpaces[key] = next;
  saveActiveTestDraft();
}

const answerIds = computed(() => (
  templateSlots.value.map((slot) => filled[String(slot.slot)]?.id || null)
));

function buildAnswerLines() {
  return templateSlots.value.map((slot) => {
    const key = String(slot.slot);
    const block = filled[key];
    const rawText = String(block?.text || "").replace(/\r\n/g, "\n");
    const text = rawText.replace(/^[ \t]+/, "");
    return `${" ".repeat(indentSpaces(key))}${text}`;
  });
}

function answerCompletenessStatus() {
  const total = templateSlots.value.length;
  const missingIndices = [];
  templateSlots.value.forEach((slot, index) => {
    if (!filled[String(slot.slot)]?.id) missingIndices.push(index);
  });

  const missingCount = missingIndices.length;
  const allBlank = total > 0 && missingCount === total;
  return {
    complete: total > 0 && missingCount === 0,
    missingCount,
    missingIndices,
    allBlank,
    message: allBlank
      ? "請先將所有程式片段放入作答區。"
      : `尚有 ${missingCount} 個程式片段未放入作答區，請填答完整後再送出。`,
  };
}

function validateBeforeSubmit() {
  if (isChoiceQuestion.value) {
    if (!selectedChoiceKey.value) {
      state.err = "請先選擇一個答案後再送出。";
      return false;
    }
    return true;
  }

  const completeness = answerCompletenessStatus();
  if (!completeness.complete) {
    state.err = completeness.message;
    return false;
  }
  return true;
}

async function submitAnswer() {
  if (state.loading || state.submitting || state.noTask) return;
  state.err = "";
  if (!validateBeforeSubmit()) return;

  state.submitting = true;
  try {
    const submittedAt = new Date();
    const startedAtIso = testMeta.started_at_ms
      ? new Date(Number(testMeta.started_at_ms)).toISOString()
      : null;
    const durationSec = testMeta.started_at_ms
      ? Math.max(0, Math.round((submittedAt.getTime() - Number(testMeta.started_at_ms)) / 1000))
      : 0;

    const body = {
      session_id: sessionStorage.getItem("test_taking_session_id") || "",
      page: "test_taking",
      student_id: studentId.value,
      participant_id: participantId.value,
      test_cycle_id: testCycleId.value,
      test_role: testRole.value,
      task_id: currentTaskId(),
      question_type: testQuestionType.value,
      answer_lines: isChoiceQuestion.value ? [] : buildAnswerLines(),
      answer_ids: isChoiceQuestion.value ? [] : answerIds.value,
      selected_answer: isChoiceQuestion.value ? selectedChoiceKey.value : null,
      started_at: startedAtIso,
      submitted_at: submittedAt.toISOString(),
      duration_seconds: durationSec,
    };

    const response = await fetch(`${API_BASE}/api/parsons/test/submit`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));

    if (response.status === 403 && data?.error === "pretest_questionnaire_required") {
      leaveTest("/pretest-survey");
      return;
    }
    if (response.status === 403) {
      window.alert("測驗目前未開放，請洽老師。");
      leaveTest("/home");
      return;
    }

    if (!response.ok || data?.ok === false) {
      state.err = data?.message || "答案送出失敗，請稍後再試。";
      return;
    }

    clearActiveTestDraft();
    await advanceAfterSubmit();
  } catch (error) {
    state.err = error?.message || "答案送出失敗。";
  } finally {
    state.submitting = false;
  }
}

async function advanceAfterSubmit() {
  const total = Number(testMeta.total || 1);
  const current = Number(testMeta.current_index || 1);
  if (total <= 1 || current >= total) {
    window.alert(
      testRole.value === "pre"
        ? "測驗已完成，感謝您的作答!將進入首頁可以開始觀看影片進行練習。"
        : `${testTitle.value}已完成。`,
    );
    clearTestProgress();
    leaveTest("/home");
    return;
  }

  testMeta.request_index = current + 1;
  saveTestProgressIndex(testMeta.request_index);
  await loadTask(testMeta.request_index);
}

function goHome() {
  leaveTest("/home");
}

onBeforeRouteLeave(() => {
  if (testRole.value !== "pre" || canLeaveTest.value) return true;
  window.alert("前測進行中，請完成全部題目後再離開。");
  return false;
});

onMounted(() => {
  if (!sessionStorage.getItem("test_taking_session_id")) {
    sessionStorage.setItem("test_taking_session_id", `${Date.now()}-${Math.random().toString(16).slice(2)}`);
  }
  installPretestHistoryLock();
  window.addEventListener("popstate", restorePretestAfterBrowserNavigation);
  window.addEventListener("beforeunload", confirmBeforeLeavingPretest);
  testMeta.request_index = routeOrSavedTestIndex();
  loadTask();
});

onBeforeUnmount(() => {
  window.removeEventListener("popstate", restorePretestAfterBrowserNavigation);
  window.removeEventListener("beforeunload", confirmBeforeLeavingPretest);
});

watch(
  () => [route.query.test_role, route.query.test_cycle_id, route.query.test_index],
  () => {
    testMeta.request_index = routeOrSavedTestIndex();
    loadTask();
  },
);
</script>

<style scoped>
:global(body) {
  margin: 0;
}

.test-page {
  min-height: 100dvh;
  box-sizing: border-box;
  max-width: 100%;
  margin: 0 auto;
  padding: 22px;
  color: #172033;
  background:
    radial-gradient(circle at 14% 10%, rgba(255, 247, 219, 0.9), transparent 32%),
    radial-gradient(circle at 90% 4%, rgba(223, 242, 255, 0.9), transparent 30%),
    linear-gradient(180deg, #f7fafc 0%, #f1f6fb 100%);
  font-family: "Noto Sans TC", "Segoe UI", "PingFang TC", sans-serif;
}

.test-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.eyebrow {
  color: #475569;
  font-size: 14px;
  font-weight: 900;
}

h1 {
  margin: 3px 0 0;
  color: #0f172a;
  font-size: 28px;
  line-height: 1.2;
}

.progress-pill {
  border: 1px solid #cbd5e1;
  border-radius: 999px;
  padding: 8px 14px;
  background: #ffffff;
  color: #0f5c84;
  font-weight: 900;
  white-space: nowrap;
}

.question-box,
.panel,
.choice-panel,
.loading-panel {
  border: 1px solid #d3deea;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
}

.question-box {
  padding: 18px;
}

.question-label {
  margin-bottom: 6px;
  color: #475569;
  font-size: 14px;
  font-weight: 900;
}

.question-text {
  color: #0f172a;
  font-size: 22px;
  font-weight: 900;
  line-height: 1.45;
  white-space: pre-wrap;
}

.loading-panel {
  margin-top: 16px;
  padding: 18px;
  font-weight: 900;
}

.message {
  margin: 14px 0 0;
  padding: 12px 14px;
  border-radius: 12px;
  font-weight: 900;
}

.message.error {
  border: 1px solid #fecaca;
  background: #fff1f2;
  color: #b91c1c;
}

.choice-panel {
  display: grid;
  gap: 12px;
  margin-top: 16px;
  padding: 18px;
}

.choice-option {
  width: 100%;
  min-height: 58px;
  display: grid;
  grid-template-columns: 42px 1fr;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  background: #ffffff;
  color: #1f2937;
  cursor: pointer;
  text-align: left;
  font: inherit;
  font-weight: 900;
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.05);
}

.choice-option.selected {
  border-color: #0f5c84;
  background: #eef9ff;
  box-shadow: 0 0 0 3px rgba(15, 92, 132, 0.14);
}

.choice-key {
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: #e2e8f0;
  color: #0f172a;
}

.choice-option.selected .choice-key {
  background: #0f5c84;
  color: #ffffff;
}

.choice-text {
  min-width: 0;
  line-height: 1.45;
  word-break: break-word;
}

.parsons-board {
  display: grid;
  grid-template-columns: 1.25fr 1fr;
  gap: 16px;
  margin-top: 16px;
}

.panel {
  min-width: 0;
  min-height: 460px;
  padding: 16px;
}

.panel-title {
  margin-bottom: 12px;
  color: #0f172a;
  font-size: 18px;
  font-weight: 900;
}

.slots {
  display: grid;
  gap: 12px;
}

.slot-row {
  display: grid;
  gap: 7px;
}

.slot-label {
  width: fit-content;
  border-radius: 999px;
  padding: 4px 10px;
  color: #7f1d1d;
  background: #fee2e2;
  font-size: 13px;
  font-weight: 900;
}

.blank {
  position: relative;
  min-height: 50px;
  display: flex;
  align-items: center;
  border: 2px dashed #8da2b8;
  border-radius: 14px;
  padding: 8px 44px 8px 8px;
  background: #ffffff;
  transition: border-color .2s ease, box-shadow .2s ease, background .2s ease;
}

.blank.over {
  border-color: #0f5c84;
  background: #eef9ff;
  box-shadow: 0 0 0 3px rgba(14, 165, 233, .16);
}

.blank.filled {
  border-style: solid;
  border-color: #4f46e5;
  background: #eef2ff;
}

.filled-block {
  flex: 1 1 auto;
  min-width: 0;
  align-self: stretch;
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-radius: 10px;
  background: linear-gradient(180deg, #ffffff 0%, #eef2ff 100%);
  box-shadow: 0 6px 14px rgba(79, 70, 229, .12);
  cursor: grab;
  user-select: none;
  white-space: pre-wrap;
}

.filled-block:active {
  cursor: grabbing;
}

.placeholder {
  color: #64748b;
  font-weight: 800;
}

.remove {
  position: absolute;
  right: 10px;
  top: 50%;
  width: 28px;
  height: 28px;
  transform: translateY(-50%);
  border: 0;
  border-radius: 999px;
  background: #fee2e2;
  color: #7f1d1d;
  cursor: pointer;
  font-weight: 900;
}

.indent-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #475569;
  font-size: 13px;
  font-weight: 900;
}

.indent-tools button {
  width: 28px;
  height: 28px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
  cursor: pointer;
  font-weight: 900;
}

.pool {
  display: grid;
  gap: 10px;
}

.pill {
  border: 1px solid #d7e1ed;
  border-radius: 12px;
  padding: 12px 14px;
  background: #ffffff;
  cursor: grab;
  font-weight: 900;
  box-shadow: 0 8px 16px rgba(15, 23, 42, 0.06);
  white-space: pre-wrap;
}

.pill.used {
  opacity: 0.55;
}

.actions {
  display: flex;
  justify-content: center;
  gap: 16px;
  margin-top: 18px;
}

.btn {
  min-width: 150px;
  border: 0;
  border-radius: 999px;
  padding: 12px 22px;
  cursor: pointer;
  font-weight: 900;
}

.btn.ghost {
  background: #dce5ef;
  color: #1f2937;
}

.btn.submit {
  background: linear-gradient(90deg, #2f8b68 0%, #25a18e 100%);
  color: #ffffff;
}

.btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

@media (max-width: 900px) {
  .test-page {
    padding: 14px;
  }

  .test-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .question-text {
    font-size: 20px;
  }

  .parsons-board {
    grid-template-columns: 1fr;
  }

  .panel {
    min-height: 0;
  }
}
</style>
