<template>
  <div class="precheck">
    <h2>檢查前測狀態中...</h2>
  </div>
</template>

<script setup>
import { onMounted } from "vue"
import { useRouter } from "vue-router"

const router = useRouter()

onMounted(async () => {
  const studentId = (localStorage.getItem("student_id") || "").trim()
  const testCycleId = (localStorage.getItem("test_cycle_id") || "default").trim() // [新增]

  if (!studentId) {
    router.push("/login")
    return
  }

  try {
    // ✅ [修正] 後端路由是 /api/parsons/test/status（不是 /api/parsons_test/status）
    const res = await fetch(`/api/parsons/test/status?student_id=${encodeURIComponent(studentId)}&test_cycle_id=${encodeURIComponent(testCycleId)}`) // [新增]
    const data = await res.json()

    // ✅ [修正] 後端回傳欄位：pre_done / post_done / post_open
    if (data?.ok && data?.pre_done) { // [新增]
      router.push("/home")
    } else {
      router.push(`/parsons?mode=test&test_role=pre&test_cycle_id=${encodeURIComponent(testCycleId)}`) // [新增]
    }

  } catch (err) {
    console.error(err)
    router.push("/home")
  }
})
</script>