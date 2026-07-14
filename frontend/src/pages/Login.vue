<template>
  <div class="page">
    <div class="card">
      <div class="header">
        <div class="logo" aria-hidden="true">🧪</div>
        <div>
          <h1>影音學習平台</h1>
          <p>請使用學號與密碼登入</p>
        </div>
      </div>

      <!-- ✅ 教學重點：用 form 統一處理 Enter / Submit -->
      <form @submit.prevent="login" novalidate>
        <div class="field">
          <label for="studentId">學號</label>
          <input
            id="studentId"
            ref="studentIdInput"
            v-model="studentId"
            type="text"
            inputmode="numeric"
            autocomplete="username"
            placeholder="請輸入學號"
            :disabled="loading"
          />
        </div>

        <div class="field">
          <label for="password">密碼</label>
          <div class="pwd">
            <input
              id="password"
              v-model="password"
              :type="showPwd ? 'text' : 'password'"
              autocomplete="current-password"
              placeholder="請輸入密碼"
              :disabled="loading"
            />
            <button type="button" class="toggle" @click="showPwd = !showPwd" :disabled="loading">
              {{ showPwd ? "隱藏" : "顯示" }}
            </button>
          </div>
        </div>

        <div v-if="error" class="error">{{ error }}</div>

        <button class="btn" type="submit" :disabled="loading || !canSubmit">
          <span v-if="loading">登入中…</span>
          <span v-else>登入</span>
        </button>
      </form>
    </div>
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

// [新增] 測驗週期（你現在用 default）
const DEFAULT_TEST_CYCLE_ID = "default"; // [新增]

// ✅ 教學重點：可提交條件集中管理
const canSubmit = computed(() => {
  const sid = (studentId.value || "").trim();
  const pwd = password.value || "";
  return sid.length > 0 && pwd.length > 0;
});

function setError(msg) {
  error.value = msg || "";
}

// [新增] 登入成功後：若前測開放且未完成 → 強制導前測
async function maybeRedirectToPretest(sid) { // [新增]
  try { // [新增]
    const sres = await api.get("/api/parsons/test/status", { // [新增]
      params: { student_id: sid, test_cycle_id: DEFAULT_TEST_CYCLE_ID }, // [新增]
    }); // [新增]
    const sdata = sres?.data; // [新增]
    if (sdata?.ok && sdata?.pre_open && !sdata?.pre_done) { // [新增]
      router.replace({ // [新增]
        path: "/posttest/parsons", // [新增]
        query: { mode: "test", test_role: "pre", test_cycle_id: DEFAULT_TEST_CYCLE_ID }, // [新增]
      }); // [新增]
      return true; // [新增]
    } // [新增]
  } catch (e) { // [新增]
    // 失敗就不要擋路，回到原本流程
    console.warn("pretest status check failed:", e); // [新增]
  } // [新增]
  return false; // [新增]
} // [新增]

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

    // ✅ 只導一次：老師→admin dashboard；學生→先回 home，若前測真的開啟再由 helper 轉走
    if (role === "teacher" || role === "admin") {
      router.replace("/admin/dashboard");
    } else {
      // [新增] 先導前測（若需要）
      const redirected = await maybeRedirectToPretest(sid); // [新增]
      if (redirected) return; // [新增]

      // 前測關閉時直接進學生首頁
      router.replace("/home");
    }
  } catch (e) {
    // ✅ 教學重點：把常見錯誤變成「人看得懂」的訊息
    const status = e?.response?.status;
    const msgFromServer = e?.response?.data?.message || e?.response?.data?.error;

    if (status === 401 || status === 403) {
      setError(msgFromServer || "學號或密碼錯誤，請再試一次。");
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
.page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: #f6f7fb;
  padding: 24px;
  box-sizing: border-box;
}

.card {
  width: min(520px, 100%);
  background: #fff;
  border: 1px solid #eee;
  border-radius: 18px;
  padding: 26px;
  box-sizing: border-box;
}

.header {
  display: flex;
  gap: 14px;
  align-items: center;
  margin-bottom: 18px;
}

.logo {
  width: 46px;
  height: 46px;
  border-radius: 14px;
  background: #f0f3ff;
  display: grid;
  place-items: center;
  font-size: 22px;
}

h1 {
  margin: 0;
  font-size: 20px;
}

p {
  margin: 6px 0 0;
  color: #666;
  font-size: 14px;
}

.field {
  margin-top: 14px;
}

label {
  display: block;
  font-size: 13px;
  color: #333;
  margin-bottom: 6px;
}

input {
  width: 100%;
  padding: 12px 12px;
  border: 1px solid #ddd;
  border-radius: 12px;
  font-size: 14px;
  outline: none;
  box-sizing: border-box;
}

input:focus {
  border-color: #8fa8ff;
}

.pwd {
  display: flex;
  gap: 10px;
  align-items: center;
}

.toggle {
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid #ddd;
  background: #fff;
  cursor: pointer;
  white-space: nowrap;
}

.error {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: #fff3f3;
  border: 1px solid #ffd1d1;
  color: #b00020;
  font-size: 14px;
}

.btn {
  width: 100%;
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: 14px;
  border: none;
  background: linear-gradient(90deg, #7c8cff, #ff8ac5);
  color: #fff;
  font-weight: 800;
  cursor: pointer;
  font-size: 14px;
}

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.hint {
  margin-top: 14px;
  color: #777;
  font-size: 13px;
}
</style>
