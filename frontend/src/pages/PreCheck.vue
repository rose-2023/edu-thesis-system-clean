<template>
  <div class="precheck">
    <h2>檢查前測狀態中...</h2>
  </div>
</template>

<script setup>
import { onMounted } from "vue"
import { useRouter } from "vue-router"

const router = useRouter()

// [新增] 統一 API_BASE（若你專案有 VITE_API_BASE 就用它，沒有就走同網域 proxy）
const API_BASE = (import.meta?.env?.VITE_API_BASE || "").trim()

onMounted(async () => {
  const studentId = (localStorage.getItem("student_id") || "").trim()
  const testCycleId = (localStorage.getItem("test_cycle_id") || "default").trim() // [保留]

  if (!studentId) {
    router.replace("/login") // [修改]
    return
  }

  try {
    // [修改] 兼容有/無 API_BASE 的情境
    const url = `${API_BASE}/api/parsons/test/status?student_id=${encodeURIComponent(studentId)}&test_cycle_id=${encodeURIComponent(testCycleId)}` // [修改]
    const res = await fetch(url) // [修改]
    const data = await res.json()

    // [新增] 若後端有回 pre_open，就尊重是否開放；沒有就當作開放
    const preOpen = (typeof data?.pre_open === "boolean") ? data.pre_open : true // [新增]

    // ✅ 後端回傳欄位：pre_done / post_done / post_open（你已驗證過）
    if (data?.ok && data?.pre_done) {
      // [新增] 已完成提示（你說要 ALERT）
      window.alert("前測已完成，將進入首頁。") // [新增]
      router.replace("/home") // [修改]
      return
    }

    if (!preOpen) { // [新增]
      window.alert("目前前測尚未開放，將進入首頁。") // [新增]
      router.replace("/home") // [新增]
      return // [新增]
    }

    // [修改] ✅ 你的前測實際路徑是 /posttest/parsons（不是 /parsons）
    router.replace(`/posttest/parsons?mode=test&test_role=pre&test_cycle_id=${encodeURIComponent(testCycleId)}`) // [修改]

  } catch (err) {
    console.error(err)
    router.replace("/home") // [修改]
  }
})
</script>