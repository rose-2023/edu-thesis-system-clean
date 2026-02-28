<template>
  <div class="page">
    <div class="card">
      <div class="header">
        <div class="logo" aria-hidden="true">🧪</div>
        <div>
          <h1>論文系統登入</h1>
          <p>請使用學號與密碼登入</p>
        </div>
      </div>

      <!-- ✅ 教學重點：用 form 統一處理 Enter / Submit -->
      <form @submit.prevent="login" novalidate>
        <div class="field">
          <label for="studentId">學號</label>
          <div class="inputWrap">
            <span class="icon" aria-hidden="true">👤</span>
            <input
              id="studentId"
              ref="studentIdInput"
              v-model="studentId"
              placeholder="例如：A123456789"
              autocomplete="username"
              inputmode="text"
              :disabled="loading"
              aria-label="學號"
            />
          </div>
          <p class="hint">提示：可輸入測試帳號或你的學號</p>
        </div>

        <div class="field">
          <label for="password">密碼</label>
          <div class="inputWrap">
            <span class="icon" aria-hidden="true">🔒</span>

            <input
              id="password"
              v-model="password"
              :type="showPwd ? 'text' : 'password'"
              placeholder="請輸入密碼"
              autocomplete="current-password"
              :disabled="loading"
              aria-label="密碼"
            />

            <!-- ✅ 教學重點：可視化密碼（減少打錯） -->
            <button
              class="iconBtn"
              type="button"
              @click="showPwd = !showPwd"
              :disabled="loading"
              :aria-label="showPwd ? '隱藏密碼' : '顯示密碼'"
              :title="showPwd ? '隱藏密碼' : '顯示密碼'"
            >
              {{ showPwd ? "🙈" : "👁️" }}
            </button>
          </div>
        </div>

        <!-- ✅ 教學重點：提交按鈕 disabled 條件要包含 loading + 欄位檢查 -->
        <button class="btn" type="submit" :disabled="loading || !canSubmit">
          <span v-if="!loading">登入</span>
          <span v-else>登入中…</span>
        </button>

        <!-- ✅ 教學重點：錯誤訊息用 role=alert（無障礙、也更顯眼） -->
        <p class="error" v-if="error" role="alert">{{ error }}</p>
      </form>
    </div>

    <p class="copyright">
      © {{ new Date().getFullYear() }} Thesis System
    </p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";

const router = useRouter();

const studentId = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");
const showPwd = ref(false);

const studentIdInput = ref(null);

// ✅ 教學重點：可提交條件集中管理
const canSubmit = computed(() => {
  const sid = (studentId.value || "").trim();
  const pwd = password.value || "";
  return sid.length > 0 && pwd.length > 0;
});

function setError(msg) {
  error.value = msg || "";
}

async function login() {
  setError("");

  const sid = (studentId.value || "").trim();
  const pwd = password.value || "";

  if (!sid || !pwd) {
    setError("請輸入學號與密碼");
    return;
  }

  loading.value = true;

  try {
    const res = await api.post("/api/auth/login", {
      student_id: sid,
      password: pwd,
    });

    // ✅ 教學重點：記住學號（開發測試很省時間）
    localStorage.setItem("last_student_id", sid);

    // ✅【重要】V1.7: 直接存學號（用於後續記錄回看和答題）
    localStorage.setItem("student_id", sid);

    // ✅ 存 token / role / participant_id
    if (res.data?.token) localStorage.setItem("token", res.data.token);
    if (res.data?.role) localStorage.setItem("role", res.data.role);
    if (res.data?.participant_id) localStorage.setItem("participant_id", res.data.participant_id);

    const role = res.data?.role || "student";

    // ✅ 只導一次：老師→admin dashboard；學生→precheck/home
    if (role === "teacher" || role === "admin") {
      router.replace("/admin/dashboard");
    } else {
      router.replace("/precheck");
    }
  } catch (e) {
    // ✅ 教學重點：把常見錯誤變成「人看得懂」的訊息
    const status = e?.response?.status;
    const msgFromServer = e?.response?.data?.message || e?.response?.data?.error;

    if (status === 401 || status === 403) {
      setError("學號或密碼錯誤，請再試一次。");
    } else if (status >= 500) {
      setError("後端伺服器錯誤（500）。請確認 Flask 有啟動，或稍後再試。");
    } else {
      setError(msgFromServer || e?.message || "連線失敗（請確認 Flask 有跑）");
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  // ✅ 自動帶入上次登入的學號
  const last = localStorage.getItem("last_student_id") || "";
  if (last) studentId.value = last;

  await nextTick();
  try {
    studentIdInput.value?.focus?.();
  } catch (_) {}
});
</script>

<style scoped>
.page{
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 28px 16px;
}

.card{
  width: 100%;
  max-width: 420px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 22px 22px 18px;
  backdrop-filter: blur(6px);
}

.header{
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 18px;
}

.logo{
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: rgba(99,102,241,.10);
  border: 1px solid rgba(99,102,241,.18);
  font-size: 22px;
}

h1{
  margin: 0;
  font-size: 20px;
  letter-spacing: .2px;
}

.header p{
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--muted);
}

.field{ margin: 14px 0; }

label{
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 6px;
}

.hint{
  margin: 6px 2px 0;
  font-size: 12px;
  color: var(--muted);
}

.inputWrap{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: rgba(255,255,255,.65);
  transition: border .15s, box-shadow .15s, transform .05s;
}

.inputWrap:focus-within{
  border-color: rgba(99,102,241,.55);
  box-shadow: 0 0 0 4px rgba(99,102,241,.12);
}

.icon{ opacity: .75; }

input{
  border: none;
  outline: none;
  width: 100%;
  background: transparent;
  font-size: 14px;
  color: var(--text);
}

.iconBtn{
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 16px;
  opacity: .85;
  padding: 2px 6px;
  border-radius: 10px;
}
.iconBtn:hover{ background: rgba(0,0,0,.05); }
.iconBtn:disabled{
  cursor: not-allowed;
  opacity: .5;
}

.btn{
  width: 100%;
  border: none;
  border-radius: 12px;
  padding: 11px 12px;
  cursor: pointer;
  font-weight: 600;
  font-size: 14px;
  color: white;
  background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 55%, #ec4899 100%);
  box-shadow: 0 10px 18px rgba(99,102,241,.22);
  transition: transform .06s, filter .15s, opacity .15s;
}

.btn:hover{ filter: brightness(1.02); }
.btn:active{ transform: translateY(1px); }
.btn:disabled{
  opacity: .65;
  cursor: not-allowed;
}

.error{
  margin: 10px 0 0;
  font-size: 13px;
  color: #b91c1c;
  background: rgba(185,28,28,.08);
  border: 1px solid rgba(185,28,28,.18);
  padding: 10px 12px;
  border-radius: 12px;
}

.copyright{
  margin-top: 14px;
  font-size: 12px;
  color: var(--muted);
}
</style>