<template>
  <div class="page">
    <div class="card">
      <div class="header">
        <div class="logo">🧪</div>
        <div>
          <h1>論文系統登入</h1>
          <p>請使用學號與密碼登入</p>
        </div>
      </div>

      <div class="field">
        <label>學號</label>
        <div class="inputWrap">
          <span class="icon">👤</span>
          <input v-model="studentId" placeholder="例如：A123456789" autocomplete="username" />
        </div>
      </div>

      <div class="field">
        <label>密碼</label>
        <div class="inputWrap">
          <span class="icon">🔒</span>
          <input
            v-model="password"
            type="password"
            placeholder="請輸入密碼"
            autocomplete="current-password"
            @keyup.enter="login"
          />
        </div>
      </div>

      <button class="btn" @click="login" :disabled="loading">
        <span v-if="!loading">登入</span>
        <span v-else>登入中…</span>
      </button>

      <p class="error" v-if="error">{{ error }}</p>
    </div>

    <p class="copyright">
      © {{ new Date().getFullYear() }} Thesis System
    </p>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";

const router = useRouter();

const studentId = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");

async function login() {
  error.value = "";
  const sid = (studentId.value || "").trim();
  const pwd = password.value || "";
  if (!sid || !pwd) {
    error.value = "請輸入學號與密碼";
    return;
  }
  loading.value = true;

  try {
    const res = await api.post("/api/auth/login", {
      student_id: sid,
      password: pwd,
    });

    // ✅ 存 token / role / participant_id
    if (res.data?.token) localStorage.setItem("token", res.data.token);
    if (res.data?.role) localStorage.setItem("role", res.data.role);
    if (res.data?.participant_id) localStorage.setItem("participant_id", res.data.participant_id);

    const role = res.data?.role || "student";

    // ✅ 只導一次：老師→admin dashboard；學生→quiz/home
    if (role === "teacher" || role === "admin") {
      router.replace("/admin/dashboard");
    } else {
      const pretestDone = localStorage.getItem("pretest_done") === "true";
      router.replace(pretestDone ? "/home" : "/quiz");
    }
  } catch (e) {
    error.value = e?.response?.data?.message || e.message || "連線失敗（請確認 Flask 有跑）";
  } finally {
    loading.value = false;
  }
}

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

.footer{
  margin-top: 14px;
  display: flex;
  justify-content: center;
  gap: 8px;
  font-size: 13px;
}

.muted{ color: var(--muted); }

.link{
  text-decoration: none;
  font-weight: 600;
}
.link:hover{ text-decoration: underline; }

copyright{
  margin-top: 14px;
}

.copyright{
  margin-top: 14px;
  font-size: 12px;
  color: var(--muted);
}
</style>
