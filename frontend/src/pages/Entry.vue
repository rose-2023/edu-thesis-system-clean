<template>
  <div style="padding:24px;">載入中…</div>
</template>

<script setup>
import { onMounted } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";

const router = useRouter();

onMounted(async () => {
  console.log("✅ Entry page loaded");

  const participantId = localStorage.getItem("participant_id");
  if (!participantId) return router.replace("/login");

  try {
    const res = await api.get("/api/student/entry", {
      params: { participant_id: participantId }
    });

    console.log("entry res =", res.data);

    if (!res.data?.ok) return router.replace("/login");

    if (res.data.next === "pre") {
      const sid = res.data.session_id;
      if (!sid) {
        alert("後端沒有回 session_id，請檢查 /api/student/entry");
        return router.replace("/home");
      }
      localStorage.setItem("session_id", sid);
      return router.replace("/quiz");
    }

    return router.replace("/home");
  } catch (e) {
    console.error(e);
    return router.replace("/login");
  }
});
</script>
