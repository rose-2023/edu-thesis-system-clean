<template>
  <div v-if="items.length" class="chartLayout" role="img" :aria-label="ariaLabel">
    <div class="yAxis" aria-hidden="true">
      <span>{{ formatTick(scaleMax) }}</span>
      <span>{{ formatTick(scaleMax / 2) }}</span>
      <span>0</span>
    </div>

    <div class="chartScroll">
      <div class="chartPlot" :style="{ minWidth: plotMinWidth }">
        <div class="gridLines" aria-hidden="true">
          <span></span>
          <span></span>
          <span></span>
        </div>

        <div class="bars">
          <div
            v-for="item in items"
            :key="item.key"
            class="barGroup"
            :title="item.tooltip || item.label"
            :aria-label="item.tooltip || `${item.label}：${displayValue(item)}`"
          >
            <div class="barValue">{{ displayValue(item) }}</div>
            <div class="barArea">
              <div
                class="bar"
                :style="{
                  height: barHeight(item.value),
                  backgroundColor: item.color || color,
                }"
              ></div>
            </div>
            <div class="barLabel">{{ item.label }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div v-else class="emptyState">目前沒有符合條件的資料可視覺化。</div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  items: {
    type: Array,
    default: () => [],
  },
  maxValue: {
    type: Number,
    default: null,
  },
  valueSuffix: {
    type: String,
    default: "",
  },
  color: {
    type: String,
    default: "#146c64",
  },
  ariaLabel: {
    type: String,
    default: "分析長條圖",
  },
});

const scaleMax = computed(() => {
  if (Number.isFinite(props.maxValue) && props.maxValue > 0) return props.maxValue;
  const values = props.items.map((item) => Number(item.value) || 0);
  return Math.max(1, ...values);
});

const plotMinWidth = computed(() => `${Math.max(520, props.items.length * 88)}px`);

function formatTick(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0";
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

function displayValue(item) {
  if (item.displayValue !== undefined && item.displayValue !== null) {
    return item.displayValue;
  }
  return `${formatTick(Number(item.value) || 0)}${props.valueSuffix}`;
}

function barHeight(value) {
  const number = Math.max(0, Number(value) || 0);
  if (!number) return "0%";
  return `${Math.max(3, Math.min(100, (number / scaleMax.value) * 100))}%`;
}
</script>

<style scoped>
.chartLayout {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  width: 100%;
  min-width: 0;
}

.yAxis {
  display: flex;
  box-sizing: border-box;
  height: 260px;
  padding: 18px 7px 54px 0;
  flex-direction: column;
  justify-content: space-between;
  color: #64748b;
  font-size: 11px;
  text-align: right;
}

.chartScroll {
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
}

.chartPlot {
  position: relative;
  height: 260px;
}

.gridLines {
  position: absolute;
  top: 24px;
  right: 0;
  left: 0;
  display: flex;
  height: 176px;
  flex-direction: column;
  justify-content: space-between;
  pointer-events: none;
}

.gridLines span {
  display: block;
  border-top: 1px solid #dbe2ea;
}

.bars {
  position: relative;
  z-index: 1;
  display: flex;
  height: 260px;
  box-sizing: border-box;
  gap: 14px;
  padding: 0 10px;
}

.barGroup {
  display: grid;
  width: 74px;
  flex: 0 0 74px;
  grid-template-rows: 24px 176px 52px;
}

.barValue {
  overflow: hidden;
  color: #334155;
  font-size: 11px;
  font-weight: 800;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.barArea {
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

.bar {
  width: 42px;
  max-height: 100%;
  border-radius: 4px 4px 0 0;
  opacity: 0.9;
  transition: opacity 160ms ease;
}

.barGroup:hover .bar {
  opacity: 1;
}

.barLabel {
  display: -webkit-box;
  overflow: hidden;
  padding-top: 7px;
  color: #475569;
  font-size: 11px;
  line-height: 1.35;
  text-align: center;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.emptyState {
  display: grid;
  min-height: 180px;
  place-items: center;
  color: #64748b;
  font-size: 14px;
}
</style>
