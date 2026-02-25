<template>
  <div class="homePage">
    <!-- 上方資訊列 -->
    <header class="topBar">
      <div class="leftProfile">
        <div class="avatar">
          <img
            v-if="avatarUrl"
            :src="avatarUrl"
            alt="avatar"
            class="avatarImg"
          />
          <div v-else class="avatarPlaceholder"></div>
        </div>

        <div class="profileText">
          <div class="hello">你好，{{ studentName }}</div>
          <div class="level">等級：{{ level }}</div>
        </div>
      </div>

      <div class="rightProgress">
        <div class="progressTitle">
          未完成課程：<span>{{ ongoingUnit }}</span> Parsons
        </div>
        <div class="dots">
          <span
            v-for="i in totalDots"
            :key="i"
            class="dot"
            :class="{ on: i <= doneDots }"
          ></span>
        </div>
      </div>
    </header>

    <!-- 單元列表 -->
    <main class="main">
      <h1 class="title">單元列表</h1>

      <div class="unitGrid">
        <section
          v-for="u in units"
          :key="u.unit"
          class="unitCard"
          :class="u.theme"
        >
          <div class="unitHeader">
            <div class="unitName">{{ u.unit }} {{ u.name }}</div>
          </div>

          <div class="unitFooter">
            <div class="progressText">進度{{ u.progress }}%</div>

            <button class="enterBtn" @click="goUnit(u.unit)">
              進入
            </button>
          </div>

          <div class="progressBar">
            <div class="progressFill" :style="{ width: u.progress + '%' }"></div>
          </div>
        </section>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";

const studentName = ref("許同學");
const level = ref("L2");
const avatarUrl = ref("");

const ongoingUnit = ref("U1-2");

const totalDots = 12;
const doneDots = ref(8);

const units = ref([
  { unit: "U1", name: "迴圈基礎", progress: 80, theme: "blue" },
  { unit: "U2", name: "串列與字串", progress: 20, theme: "green" },
]);

const router = useRouter();

function goUnit(unit) {
  // ✅ 先用你要的單元導向（之後再依 unit 變化）
  router.push(`/learn/${unit}`);
}
</script>



<style scoped>
.homePage {
  min-height: 100vh;
  background: #ffffff;
}

/* 上方資訊列 */
.topBar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 22px 32px;
  border-bottom: 1px solid #f0f0f0;
}

.leftProfile {
  display: flex;
  align-items: center;
  gap: 14px;
}

.avatar {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  overflow: hidden;
  background: #fde2d1;
  display: grid;
  place-items: center;
}

.avatarImg {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatarPlaceholder {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.15);
}

.profileText .hello {
  font-weight: 900;
  font-size: 18px;
}

.profileText .level {
  margin-top: 4px;
  color: #4b5563;
  font-weight: 800;
}

.rightProgress {
  min-width: 320px;
  text-align: left;
}

.progressTitle {
  font-weight: 900;
  margin-bottom: 10px;
}

.dots {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.dot {
  width: 14px;
  height: 14px;
  border-radius: 4px;
  border: 1.5px solid #1f2937;
  background: transparent;
}

.dot.on {
  background: #1f2937;
}

/* 主內容 */
.main {
  padding: 30px 32px;
}

.title {
  font-size: 40px;
  margin: 10px 0 24px;
  font-weight: 1000;
  letter-spacing: 1px;
}

/* 單元卡片 */
.unitGrid {
  display: flex;
  gap: 36px;
  flex-wrap: wrap;
}

.unitCard {
  width: 360px;
  height: 170px;
  border-radius: 26px;
  padding: 22px 22px 18px;
  position: relative;
  overflow: hidden;
  box-shadow: 0 10px 22px rgba(0, 0, 0, 0.06);
}

.unitHeader .unitName {
  color: #fff;
  font-weight: 1000;
  font-size: 22px;
}

/* 底部：進度文字 + 進入按鈕 */
.unitFooter {
  position: absolute;
  left: 22px;
  right: 22px;
  bottom: 46px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.progressText {
  color: #fff;
  font-weight: 900;
}

.enterBtn {
  border: 0;
  padding: 10px 18px;
  border-radius: 999px;
  font-weight: 1000;
  cursor: pointer;
  background: #f6b24a;
  color: #ffffff;
}

/* 進度條 */
.progressBar {
  position: absolute;
  left: 22px;
  right: 22px;
  bottom: 18px;
  height: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.35);
  overflow: hidden;
}

.progressFill {
  height: 100%;
  border-radius: 999px;
  background: #f6b24a;
}

/* 顏色主題（像截圖） */
.unitCard.blue {
  background: #0b6aa6;
}

.unitCard.green {
  background: #5fa371;
}

/* RWD */
@media (max-width: 900px) {
  .topBar {
    gap: 18px;
    flex-direction: column;
    align-items: flex-start;
  }
  .rightProgress {
    min-width: auto;
  }
  .unitCard {
    width: min(420px, 100%);
  }
}
</style>
