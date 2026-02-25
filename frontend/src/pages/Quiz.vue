<template>
  <div style="max-width:900px;margin:24px auto;padding:0 16px;font-family:system-ui;">
    <h2>前測（選擇題）</h2>

    <div v-if="msg" style="margin:10px 0;color:#6b7280;">{{ msg }}</div>
    <div v-if="error" style="margin:10px 0;color:#b91c1c;">{{ error }}</div>

    <div v-if="q" style="border:1px solid rgba(0,0,0,.08);border-radius:14px;padding:16px;background:white;">
      <div style="white-space:pre-wrap;font-size:16px;line-height:1.7;">{{ q.stem }}</div>

      <div style="margin-top:14px;display:grid;gap:10px;">
        <label v-for="(opt, idx) in q.options" :key="idx"
          style="display:flex;gap:10px;align-items:flex-start;border:1px solid rgba(0,0,0,.08);padding:10px 12px;border-radius:12px;cursor:pointer;">
          <input type="radio" name="ans" :value="letters[idx]" v-model="answer" />
          <div><b>{{ letters[idx] }}.</b> {{ opt }}</div>
        </label>
      </div>

      <button @click="submit" :disabled="loading || !answer"
        style="margin-top:14px;padding:10px 12px;border-radius:10px;border:none;cursor:pointer;color:white;background:#111827;width:100%;">
        <span v-if="!loading">送出</span>
        <span v-else>送出中…</span>
      </button>

      <div v-if="result" style="margin-top:12px;">
        <b>{{ result.is_correct ? "✅ 答對" : "❌ 答錯" }}</b>
        <span v-if="!result.is_correct">（正解：{{ result.correct_answer }}）</span>
        <div style="color:#6b7280;margin-top:4px;">已完成：{{ result.total_answered }}/10</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { api } from "../api";
import { useRouter } from "vue-router";
const router = useRouter();

const letters = ["A","B","C","D"];
const q = ref(null);
const answer = ref("");
const loading = ref(false);
const error = ref("");
const msg = ref("");
const result = ref(null);

let startAt = 0;

async function loadNext() {
  error.value = "";
  msg.value = "";
  result.value = null;
  answer.value = "";
  q.value = null;

  const sid = localStorage.getItem("session_id");
  if (!sid) {
    window.location.href = "/home";
    return;
  }

  const res = await api.get("/api/quiz/next", { params: { session_id: sid } });

  if (!res.data?.ok) {
    if (res.data?.code === "NO_QUESTIONS" || res.data?.code === "SESSION_ENDED") {
      msg.value = res.data.message || "前測已完成";
      window.location.href = "/home";
      return;
    }
    error.value = res.data?.message || "取得題目失敗";
    return;
  }

  q.value = res.data.question;
  startAt = Date.now();
}

async function submit() {
  const sid = localStorage.getItem("session_id");
  if (!sid || !q.value) return;

  loading.value = true;
  error.value = "";
  try {
    const timeSpent = (Date.now() - startAt) / 1000;

    const res = await api.post("/api/quiz/submit", {
      session_id: sid,
      question_id: q.value.question_id,
      answer: answer.value,
      time_spent: timeSpent,
      hint_count: 0
    });

    if (!res.data?.ok) {
      error.value = res.data?.message || "提交失敗";
      return;
    }

    result.value = res.data;

    if (res.data.ended) {
      localStorage.setItem("pretest_done", "true");
      msg.value = "前測完成，返回首頁";
      router.replace("/home");
      return;
    }

    // 0.8 秒後出下一題
    setTimeout(loadNext, 800);
  } catch (e) {
    error.value = e?.response?.data?.message || e.message || "連線失敗";
  } finally {
    loading.value = false;
  }
}

onMounted(loadNext);
</script>
