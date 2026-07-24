<!-- 學生端-前測問卷 -->
<template>
  <main class="survey-page">
    <section class="survey-card" aria-labelledby="survey-title">
      <header class="survey-header">
        <div>
          <h1 id="survey-title">學習背景問卷</h1>
          <p class="subtitle">請完成所有題目後，系統會自動帶您進入前測。</p>
        </div>
      </header>

      <p v-if="state.error" class="message error">{{ state.error }}</p>
      <section v-else-if="state.loading" class="loading-panel">載入問卷中…</section>

      <form v-else-if="currentPage" class="survey-form" @submit.prevent="submitSurvey">
        <section class="page-heading">
          <h2>{{ currentPage.title }}</h2>
          <p>所有題目皆為必填，送出前可回到上一頁修改。</p>
        </section>

        <fieldset
          v-for="question in currentPage.questions"
          :key="question.id"
          class="question"
          :aria-describedby="`${question.id}-error`"
        >
          <legend>
            {{ question.label }} <span class="required" aria-label="必填">*</span>
          </legend>

          <div v-if="question.type === 'single_choice'" class="choice-list">
            <label v-for="option in question.options" :key="option.code" class="choice-option">
              <input v-model="answers[question.id]" type="radio" :name="question.id" :value="option.code" />
              <span>{{ option.label }}</span>
            </label>
          </div>

          <div v-else-if="question.type === 'multiple_choice'" class="choice-list">
            <label v-for="option in question.options" :key="option.code" class="choice-option">
              <input
                :checked="isMultipleChoiceSelected(question, option.code)"
                type="checkbox"
                :name="question.id"
                :value="option.code"
                @change="toggleMultipleChoice(question, option.code, $event)"
              />
              <span>{{ option.label }}</span>
            </label>
          </div>

          <label
            v-if="question.other_text_field && hasOtherSelected(question)"
            class="other-input"
          >
            <span>請說明其他內容</span>
            <input v-model.trim="answers[question.other_text_field]" type="text" maxlength="500" />
          </label>

          <div v-if="question.type === 'rating'" class="rating-wrap">
            <span class="rating-label">{{ question.min_label }}</span>
            <div class="rating-options" role="radiogroup" :aria-label="question.label">
              <label v-for="option in ratingOptions(question)" :key="option.value" class="rating-option">
                <input v-model.number="answers[question.id]" type="radio" :name="question.id" :value="option.value" />
                <span class="rating-score">{{ option.value }}</span>
                <span class="rating-option-label">{{ option.label }}</span>
              </label>
            </div>
            <span class="rating-label right">{{ question.max_label }}</span>
          </div>

          <p v-if="fieldErrors[question.id]" :id="`${question.id}-error`" class="field-error">
            {{ fieldErrors[question.id] }}
          </p>
          <p
            v-if="question.other_text_field && fieldErrors[question.other_text_field]"
            :id="`${question.other_text_field}-error`"
            class="field-error"
          >
            {{ fieldErrors[question.other_text_field] }}
          </p>
        </fieldset>

        <footer class="actions">
          <button
            v-if="currentPageIndex > 0"
            class="button secondary"
            type="button"
            :disabled="state.submitting"
            @click="previousPage"
          >
            上一頁
          </button>
          <span v-else class="action-spacer" aria-hidden="true" />

          <span class="progress" aria-live="polite">
            第 {{ currentPageIndex + 1 }} / {{ pages.length }} 頁
          </span>

          <button
            v-if="!isLastPage"
            class="button primary"
            type="button"
            :disabled="state.submitting"
            @click="nextPage"
          >
            下一頁
          </button>
          <button v-else class="button primary" type="submit" :disabled="state.submitting">
            {{ state.submitting ? '送出中…' : '送出問卷並開始前測' }}
          </button>
        </footer>
      </form>
    </section>

    <div v-if="submission.showSuccess" class="success-backdrop" role="presentation">
      <section class="success-dialog" role="dialog" aria-modal="true" aria-labelledby="success-title">
        <div class="success-icon" aria-hidden="true">✓</div>
        <h2 id="success-title">謝謝作答！</h2>
        <p>您的問卷已成功儲存。按下確定後，即可開始進行測驗。</p>
        <button class="button primary" type="button" @click="continueToTest">確定，開始測驗</button>
      </section>
    </div>
  </main>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";

const router = useRouter();
const form = ref(null);
const currentPageIndex = ref(0);
const answers = reactive({});
const fieldErrors = reactive({});
const state = reactive({ loading: true, submitting: false, error: "" });
const surveyContext = reactive({ testCycleId: "", formVersion: "" });
const submission = reactive({ showSuccess: false, testCycleId: "" });

const pages = computed(() => form.value?.pages || []);
const currentPage = computed(() => pages.value[currentPageIndex.value] || null);
const isLastPage = computed(() => currentPageIndex.value === pages.value.length - 1);
const studentId = computed(() => (
  localStorage.getItem("student_id") || localStorage.getItem("studentId") || ""
).trim());
const draftKey = computed(() => (
  surveyContext.testCycleId && surveyContext.formVersion
    ? `pretest-questionnaire-draft:${studentId.value}:${surveyContext.testCycleId}:${surveyContext.formVersion}`
    : ""
));

function clearErrors() {
  Object.keys(fieldErrors).forEach((key) => delete fieldErrors[key]);
}

function clearFieldError(questionId) {
  delete fieldErrors[questionId];
}

function ratingValues(question) {
  const start = Number(question?.min || 1);
  const end = Number(question?.max || 5);
  return Array.from({ length: Math.max(0, end - start + 1) }, (_, index) => start + index);
}

function ratingOptions(question) {
  const labels = Array.isArray(question?.scale_labels) ? question.scale_labels : [];
  return ratingValues(question).map((value) => {
    const matched = labels.find((item) => Number(item?.code) === value);
    return { value, label: matched?.label || String(value) };
  });
}

function multipleChoiceValues(question) {
  const value = answers[question.id];
  return Array.isArray(value) ? value : [];
}

function isMultipleChoiceSelected(question, code) {
  return multipleChoiceValues(question).includes(code);
}

function hasOtherSelected(question) {
  return question?.type === "multiple_choice"
    ? isMultipleChoiceSelected(question, "other")
    : answers[question?.id] === "other";
}

function toggleMultipleChoice(question, code, event) {
  const selected = multipleChoiceValues(question);
  const isSelected = event?.target?.checked === true;
  const exclusiveCodes = new Set(question.exclusive_option_codes || []);
  let nextValues;

  if (!isSelected) {
    nextValues = selected.filter((value) => value !== code);
  } else if (exclusiveCodes.has(code)) {
    nextValues = [code];
  } else {
    nextValues = selected.filter((value) => !exclusiveCodes.has(value));
    if (!nextValues.includes(code)) nextValues.push(code);
  }

  answers[question.id] = nextValues;
  if (question.other_text_field && !nextValues.includes("other")) {
    delete answers[question.other_text_field];
  }
}

function restoreDraft() {
  if (!draftKey.value) return;
  try {
    const raw = sessionStorage.getItem(draftKey.value);
    const draft = raw ? JSON.parse(raw) : null;
    if (draft?.answers && typeof draft.answers === "object") {
      Object.assign(answers, draft.answers);
    }
    if (Number.isInteger(draft?.currentPageIndex)) {
      currentPageIndex.value = Math.max(0, Math.min(draft.currentPageIndex, pages.value.length - 1));
    }
  } catch (_) {
    sessionStorage.removeItem(draftKey.value);
  }
}

function saveDraft() {
  if (!draftKey.value || state.loading || state.submitting) return;
  sessionStorage.setItem(draftKey.value, JSON.stringify({
    answers: { ...answers },
    currentPageIndex: currentPageIndex.value,
  }));
}

function clearDraft() {
  if (draftKey.value) sessionStorage.removeItem(draftKey.value);
}

function validateQuestions(questions) {
  clearErrors();
  let valid = true;
  for (const question of questions || []) {
    const value = answers[question.id];
    if (question.type === "single_choice" && !String(value || "").trim()) {
      fieldErrors[question.id] = "請選擇一個答案。";
      valid = false;
    }
    if (question.type === "multiple_choice") {
      const values = Array.isArray(value) ? value : [];
      const exclusiveCodes = new Set(question.exclusive_option_codes || []);
      const allowedCodes = new Set((question.options || []).map((option) => option.code));
      if (!values.length) {
        fieldErrors[question.id] = "請至少選擇一個答案。";
        valid = false;
      } else if (
        values.some((item) => typeof item !== "string" || !allowedCodes.has(item))
        || new Set(values).size !== values.length
      ) {
        fieldErrors[question.id] = "選項資料無效，請重新選擇。";
        valid = false;
      } else if (exclusiveCodes.size && values.some((item) => exclusiveCodes.has(item)) && values.length > 1) {
        fieldErrors[question.id] = "「都學過」或「都未學過」不可與其他選項同時選擇。";
        valid = false;
      }
    }
    if (question.type === "rating" && !ratingValues(question).includes(Number(value))) {
      fieldErrors[question.id] = "請選擇 1 到 5 分。";
      valid = false;
    }
    if (question.other_text_field && hasOtherSelected(question) && !String(answers[question.other_text_field] || "").trim()) {
      fieldErrors[question.other_text_field] = "選擇「其他」時請補充說明。";
      valid = false;
    }
  }
  return valid;
}

function nextPage() {
  if (!validateQuestions(currentPage.value?.questions)) return;
  currentPageIndex.value += 1;
  saveDraft();
}

function previousPage() {
  clearErrors();
  currentPageIndex.value = Math.max(0, currentPageIndex.value - 1);
  saveDraft();
}

function goToTest(testCycleId) {
  const cycleId = String(testCycleId || surveyContext.testCycleId || "").trim();
  if (!cycleId) {
    router.replace("/home");
    return;
  }
  localStorage.setItem("test_cycle_id", cycleId);
  router.replace({
    path: "/test/taking",
    query: { mode: "test", test_role: "pre", test_cycle_id: cycleId },
  });
}

function continueToTest() {
  submission.showSuccess = false;
  goToTest(submission.testCycleId);
}

async function loadSurvey() {
  state.loading = true;
  state.error = "";
  try {
    const { data } = await api.get("/api/student/pretest-survey");
    if (!data?.ok) throw new Error(data?.message || "無法載入前測問卷。");

    surveyContext.testCycleId = String(data.test_cycle_id || "").trim();
    surveyContext.formVersion = String(data.form?.form_version || "").trim();
    form.value = data.form || null;

    if (!form.value?.pages?.length) throw new Error("問卷題目尚未設定。");
    if (data.submitted) {
      clearDraft();
      goToTest(data.test_cycle_id);
      return;
    }
    restoreDraft();
  } catch (error) {
    state.error = error?.response?.data?.message || error?.message || "無法載入前測問卷。";
  } finally {
    state.loading = false;
  }
}

async function submitSurvey() {
  const allQuestions = pages.value.flatMap((page) => page.questions || []);
  if (!validateQuestions(allQuestions)) {
    const firstInvalidPage = pages.value.findIndex((page) => !validateQuestions(page.questions));
    if (firstInvalidPage >= 0) currentPageIndex.value = firstInvalidPage;
    return;
  }

  state.submitting = true;
  state.error = "";
  try {
    const { data } = await api.post("/api/student/pretest-survey/submit", {
      answers: { ...answers },
    });
    if (!data?.ok) throw new Error(data?.message || "問卷送出失敗。");
    clearDraft();
    submission.testCycleId = String(data.test_cycle_id || surveyContext.testCycleId || "").trim();
    submission.showSuccess = true;
  } catch (error) {
    state.error = error?.response?.data?.message || error?.message || "問卷送出失敗。";
  } finally {
    state.submitting = false;
  }
}

watch(answers, saveDraft, { deep: true });
watch(currentPageIndex, saveDraft);
watch(answers, () => {
  Object.keys(fieldErrors).forEach((key) => {
    if (key in answers) clearFieldError(key);
  });
}, { deep: true });

onMounted(loadSurvey);
</script>

<style scoped>
:global(body) { margin: 0; }

.survey-page {
  min-height: 100dvh;
  box-sizing: border-box;
  padding: 32px 18px;
  background: linear-gradient(145deg, #eef6ff, #f9fbff 48%, #effaf5);
  color: #1d2939;
  font-family: "Noto Sans TC", "Segoe UI", sans-serif;
}

.survey-card {
  width: min(920px, 100%);
  margin: 0 auto;
  padding: clamp(24px, 5vw, 48px);
  border: 1px solid #dbe7f2;
  border-radius: 22px;
  background: #fff;
  box-shadow: 0 18px 48px rgba(44, 81, 112, 0.12);
}

.survey-header, .rating-wrap {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.eyebrow { margin: 0 0 8px; color: #0b6b55; font-weight: 800; }
h1, h2 { margin: 0; color: #172033; }
h1 { font-size: clamp(28px, 5vw, 38px); }
h2 { font-size: 22px; }
.subtitle, .page-heading p { color: #667085; line-height: 1.6; }
.progress { flex: 0 0 auto; padding: 10px 14px; border-radius: 999px; background: #e5f3ee; color: #0b6b55; font-weight: 800; }
.page-heading { margin: 36px 0 10px; }
.loading-panel, .message { margin: 28px 0 0; padding: 18px; border-radius: 12px; }
.loading-panel { background: #f0f6fb; }
.message.error, .field-error { color: #b42318; }
.message.error { background: #fef3f2; }

.question { margin: 22px 0; padding: 22px; border: 1px solid #e3e9f0; border-radius: 16px; }
legend { padding: 0 4px; font-size: 17px; font-weight: 800; line-height: 1.55; }
.required { color: #d92d20; }
.choice-list { display: grid; gap: 12px; margin-top: 16px; }
.choice-option { display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: 9px; cursor: pointer; }
.choice-option:hover { background: #f5f9fd; }
input[type="radio"], input[type="checkbox"] { width: 18px; height: 18px; accent-color: #168668; }
.other-input { display: grid; gap: 8px; margin: 16px 10px 0; font-weight: 700; }
.other-input input { width: min(100%, 520px); box-sizing: border-box; padding: 10px 12px; border: 1px solid #b8c5d1; border-radius: 8px; font: inherit; }
.field-error { margin: 12px 4px 0; font-size: 14px; }

.rating-wrap { align-items: end; margin-top: 18px; }
.rating-label { max-width: 150px; color: #475467; font-size: 14px; }
.rating-label.right { text-align: right; }
.rating-options { display: grid; grid-template-columns: repeat(5, minmax(86px, 1fr)); width: min(100%, 660px); gap: 8px; }
.rating-option { display: grid; min-width: 0; justify-items: center; gap: 6px; color: #344054; cursor: pointer; text-align: center; }
.rating-score { font-weight: 800; }
.rating-option-label { color: #475467; font-size: 13px; line-height: 1.35; overflow-wrap: anywhere; }
.actions { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: center; gap: 18px; margin-top: 34px; }
.actions .secondary { justify-self: start; }
.actions .primary { justify-self: end; }
.action-spacer { min-width: 0; }
.button { min-width: 120px; padding: 12px 18px; border: 0; border-radius: 10px; font: inherit; font-weight: 800; cursor: pointer; }
.button.primary { background: #157a60; color: #fff; }
.button.primary:hover { background: #0f664f; }
.button.secondary { background: #e9eff5; color: #344054; }
.button:disabled { cursor: not-allowed; opacity: 0.58; }

.success-backdrop {
  position: fixed;
  z-index: 20;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 18px;
  background: rgba(15, 35, 52, 0.48);
}

.success-dialog {
  width: min(420px, 100%);
  padding: 32px;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 22px 55px rgba(15, 35, 52, 0.28);
  text-align: center;
}

.success-dialog h2 { margin: 14px 0 8px; }
.success-dialog p { margin: 0 0 24px; color: #667085; line-height: 1.7; }
.success-icon { display: grid; width: 52px; height: 52px; margin: 0 auto; place-items: center; border-radius: 50%; background: #dff5e9; color: #087f54; font-size: 32px; font-weight: 900; }

@media (max-width: 640px) {
  .survey-page { padding: 16px 10px; }
  .survey-card { padding: 24px 18px; border-radius: 16px; }
  .survey-header { align-items: flex-start; flex-direction: column; }
  .rating-wrap { align-items: stretch; flex-direction: column; }
  .rating-label.right { text-align: left; }
  .rating-options { grid-template-columns: 1fr; width: 100%; }
  .rating-option { grid-template-columns: auto auto minmax(0, 1fr); justify-items: start; text-align: left; }
  .actions { gap: 8px; }
  .button { min-width: 0; padding: 12px 10px; }
  .progress { padding: 9px 10px; white-space: nowrap; }
}
</style>
