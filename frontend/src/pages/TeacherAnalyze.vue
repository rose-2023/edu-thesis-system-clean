<template>
  <div class="layout">
    <TeacherSidebar active="analyze" />

    <main class="content">
      <header class="pageHeader">
        <div>
          <h1>Parsons 學習分析</h1>
          <p>以 Parsons 作答紀錄與操作事件檢視班級、題目、概念與單一學生資料。</p>
        </div>
        <div class="headerActions">
          <input
            ref="studentCsvInput"
            class="hiddenFile"
            type="file"
            accept=".csv"
            @change="uploadStudentCsv"
          />
          <button class="btn" type="button" :disabled="csvBusy" @click="openStudentCsvPicker">
            匯入學生 CSV
          </button>
          <button class="btn" type="button" :disabled="csvBusy" @click="exportGroupLearningData">
            匯出組別學習歷程與作答紀錄
          </button>
          <button class="btn primary" type="button" :disabled="loading" @click="fetchAnalysis">
            {{ loading ? "載入中" : "重新整理" }}
          </button>
        </div>
      </header>

      <section class="panel">
        <div class="modeTabs" aria-label="分析模式">
          <button
            v-for="option in modeOptions"
            :key="option.value"
            class="modeBtn"
            :class="{ active: selectedMode === option.value }"
            type="button"
            @click="setMode(option.value)"
          >
            {{ option.label }}
          </button>
        </div>

        <div class="filters">
          <label class="field">
            <span>班級</span>
            <input v-model.trim="className" class="input" placeholder="全部班級" @keyup.enter="fetchAnalysis" />
          </label>
          <label class="field">
            <span>組別</span>
            <select v-model="groupType" class="input" @change="onGroupChange">
              <option value="">全部組別</option>
              <option value="control">控制組</option>
              <option value="experimental">實驗組</option>
              <option value="test_data">測試資料</option>
            </select>
          </label>
          <label class="field">
            <span>學生選擇</span>
            <select v-model="selectedStudentId" class="input" @change="fetchAnalysis">
              <option value="">選擇學生</option>
              <option v-for="row in studentOptions" :key="row.student_id" :value="row.student_id">
                {{ row.student_id }}{{ row.has_attempts ? " / 作答" : "" }}{{ row.has_logs ? " / 操作" : "" }}
              </option>
            </select>
          </label>
        </div>

        <p v-if="errorMsg" class="message error">{{ errorMsg }}</p>
        <p v-if="csvMessage" class="message ok">{{ csvMessage }}</p>
        <div v-if="invalidRows.length" class="invalidRows">
          <div v-for="row in invalidRows" :key="`${row.row}-${row.student_id}-${row.reason}`">
            row {{ row.row }} / {{ row.student_id || "-" }} / {{ row.reason }}
          </div>
        </div>
        <p class="note" :class="{ testModeNotice: groupType === 'test_data' }">
          {{ groupHint }}
        </p>

        <div class="viewTabs" role="tablist" aria-label="分析呈現方式">
          <button
            class="viewTab"
            :class="{ active: analysisView === 'table' }"
            type="button"
            role="tab"
            :aria-selected="analysisView === 'table'"
            @click="analysisView = 'table'"
          >
            資料表分析
          </button>
          <button
            class="viewTab"
            :class="{ active: analysisView === 'visual' }"
            type="button"
            role="tab"
            :aria-selected="analysisView === 'visual'"
            @click="analysisView = 'visual'"
          >
            視覺化分析
          </button>
          <button
            class="viewTab"
            :class="{ active: analysisView === 'video' }"
            type="button"
            role="tab"
            :aria-selected="analysisView === 'video'"
            @click="analysisView = 'video'"
          >
            影片觀看紀錄
          </button>
        </div>
      </section>

      <section class="overviewGrid">
        <article class="overviewPanel">
          <div class="sectionHeader compactHeader">
            <h2>班級組別總覽</h2>
            <span>來源：users</span>
          </div>
          <div class="overviewCards">
            <div class="overviewCard">
              <div class="overviewLabel">實驗組人數</div>
              <div class="overviewValue">{{ userGroupOverview.experimental_count ?? 0 }}</div>
            </div>
            <div class="overviewCard">
              <div class="overviewLabel">控制組人數</div>
              <div class="overviewValue">{{ userGroupOverview.control_count ?? 0 }}</div>
            </div>
            <div class="overviewCard">
              <div class="overviewLabel">測試帳號總人數</div>
              <div class="overviewValue">{{ userGroupOverview.test_account_count ?? 0 }}</div>
            </div>
            <div class="overviewCard subtle">
              <div class="overviewLabel">總人數</div>
              <div class="overviewValue">{{ userGroupOverview.total_student_count ?? 0 }}</div>
            </div>
          </div>
        </article>

        <article v-if="isTestMode" class="overviewPanel">
          <div class="sectionHeader compactHeader">
            <h2>{{ selectedModeConfig.label }}完成總覽</h2>
            <span>來源：parsons_attempts_v2 / parsons_test_attempts</span>
          </div>
          <div class="overviewCards compactCards">
            <div class="overviewCard">
              <div class="overviewLabel">已完成</div>
              <div class="overviewValue">{{ selectedTestCompletion.completed_students ?? 0 }}/{{ selectedTestCompletion.total_students ?? 0 }}</div>
            </div>
            <div class="overviewCard">
              <div class="overviewLabel">完成率</div>
              <div class="overviewValue">{{ formatPercent(selectedTestCompletion.completion_rate) }}</div>
            </div>
            <div class="overviewCard subtle">
              <div class="overviewLabel">測驗題數</div>
              <div class="overviewValue">{{ selectedTestCompletion.expected_task_count ?? 0 }}</div>
            </div>
          </div>
          <div v-if="false" class="overviewCards">
            <div class="overviewCard">
              <div class="overviewLabel">應完成學生</div>
              <div class="overviewValue">{{ testCompletionOverview.total_students ?? 0 }}</div>
            </div>
            <div class="overviewCard">
              <div class="overviewLabel">已完成</div>
              <div class="overviewValue">{{ testCompletionOverview.completed_students ?? 0 }}</div>
            </div>
            <div class="overviewCard">
              <div class="overviewLabel">未完成</div>
              <div class="overviewValue">{{ testCompletionOverview.pending_students ?? 0 }}</div>
            </div>
            <div class="overviewCard">
              <div class="overviewLabel">完成率</div>
              <div class="overviewValue">{{ formatPercent(testCompletionOverview.completion_rate) }}</div>
            </div>
            <div class="overviewCard subtle">
              <div class="overviewLabel">測驗題數</div>
              <div class="overviewValue">{{ testCompletionOverview.expected_task_count ?? 0 }}</div>
            </div>
          </div>
        </article>
      </section>

      <section class="panel">
        <div class="sectionHeader">
          <h2>{{ progressTableTitle }}</h2>
          <span>{{ progressCountLabel }}</span>
        </div>
        <div v-if="isTestMode" class="tableWrap analytics-table-wrapper">
          <table class="dataTable analytics-table progress-table">
            <colgroup>
              <col style="width: 15%">
              <col style="width: 18%">
              <col style="width: 14%">
              <col style="width: 12%">
              <col style="width: 18%">
              <col style="width: 23%">
            </colgroup>
            <thead>
              <tr>
                <th>學號</th>
                <th>姓名</th>
                <th>班級</th>
                <th>組別</th>
                <th class="center">{{ selectedModeConfig.label }}</th>
                <th>最後操作時間</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in pagedStudentProgressRows" :key="row.student_id">
                <td class="mono">{{ row.student_id }}</td>
                <td>{{ row.name || "-" }}</td>
                <td>{{ row.class_name || "-" }}</td>
                <td>{{ formatGroupType(row) }}</td>
                <td class="center">
                  <span class="status-badge" :class="statusClass(selectedTestProgressStatus(row))">
                    {{ formatProgressStatus(selectedTestProgressStatus(row)) }}
                  </span>
                  <div class="progress-sub">{{ selectedTestCompletedTasks(row) }}/{{ selectedTestTotalTasks(row) }} 題</div>
                </td>
                <td>{{ formatDateTime(selectedProgressLastActivity(row)) }}</td>
              </tr>
              <tr v-if="!loading && studentProgressRows.length === 0">
                <td class="empty" colspan="6">目前沒有符合條件的學生進度資料</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="tableWrap analytics-table-wrapper">
          <table class="dataTable analytics-table practice-matrix-table">
            <thead>
              <tr>
                <th>學號</th>
                <th>姓名</th>
                <th v-for="unit in practiceUnitColumns" :key="unit.unit_key" class="center">
                  {{ unit.unit_label }}
                </th>
                <th class="center">整體完成</th>
                <th>最後操作</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="row in pagedPracticeUnitRows" :key="row.student_id">
                <tr>
                  <td class="mono">{{ row.student_id }}</td>
                  <td>{{ row.name || "-" }}</td>
                  <td v-for="unit in practiceUnitColumns" :key="`${row.student_id}-${unit.unit_key}`" class="center">
                    <button
                      class="matrixCellBtn"
                      :class="statusClass(practiceUnitCell(row, unit).status)"
                      type="button"
                      @click="togglePracticeUnit(row, unit)"
                    >
                      <span>{{ practiceUnitCell(row, unit).completed_tasks || 0 }}/{{ practiceUnitCell(row, unit).total_tasks || unit.task_total || 0 }}</span>
                      <span>{{ formatProgressStatus(practiceUnitCell(row, unit).status) }}</span>
                    </button>
                  </td>
                  <td class="center">
                    <span class="status-badge" :class="statusClass(row.overall_status)">
                      {{ row.overall_completed_tasks || 0 }}/{{ row.overall_total_tasks || 0 }}
                    </span>
                  </td>
                  <td>{{ formatDateTime(row.latest_practice_at) }}</td>
                </tr>
                <tr v-if="isPracticeUnitExpanded(row)" class="matrixExpandRow">
                  <td :colspan="practiceMatrixColspan">
                    <div class="matrixExpandPanel">
                      <div class="matrixExpandTitle">
                        {{ row.student_id }} / {{ row.name || "-" }} / {{ expandedPracticeUnit.unitLabel }}
                      </div>
                      <table class="dataTable analytics-table compact-inner-table">
                        <thead>
                          <tr>
                            <th>題目</th>
                            <th class="center">來源</th>
                            <th class="center">狀態</th>
                            <th class="center">提交次數</th>
                            <th>最後作答</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="task in expandedPracticeTasks(row)" :key="`${row.student_id}-${task.task_id}`">
                            <td>
                              <div class="cell-task-title">{{ task.task_title || task.task_id || "-" }}</div>
                              <div class="progress-sub mono">{{ task.task_id || "-" }}</div>
                            </td>
                            <td class="center">
                              <span class="source-badge" :class="taskSourceClass(task)">
                                {{ formatTaskSource(task) }}
                              </span>
                              <div v-if="task.task_code" class="progress-sub mono">{{ task.task_code }}</div>
                            </td>
                            <td class="center">
                              <span class="status-badge" :class="statusClass(task.status)">
                                {{ formatProgressStatus(task.status) }}
                              </span>
                            </td>
                            <td class="center compact">{{ task.submission_count || 0 }}</td>
                            <td>{{ formatDateTime(task.last_submitted_at) }}</td>
                          </tr>
                          <tr v-if="expandedPracticeTasks(row).length === 0">
                            <td class="empty" colspan="5">此單元目前沒有題目資料</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </td>
                </tr>
              </template>
              <tr v-if="!loading && practiceUnitRows.length === 0">
                <td class="empty" :colspan="practiceMatrixColspan">目前沒有符合條件的平時練習進度資料</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="isTestMode && hasPagination(studentProgressRows)" class="paginationControls">
          <button
            class="pageBtn"
            type="button"
            :disabled="currentPage('studentProgress', studentProgressRows) <= 1"
            @click="setPage('studentProgress', currentPage('studentProgress', studentProgressRows) - 1, studentProgressRows)"
          >
            上一頁
          </button>
          <span>{{ paginationText("studentProgress", studentProgressRows) }}</span>
          <button
            class="pageBtn"
            type="button"
            :disabled="currentPage('studentProgress', studentProgressRows) >= pageCount(studentProgressRows)"
            @click="setPage('studentProgress', currentPage('studentProgress', studentProgressRows) + 1, studentProgressRows)"
          >
            下一頁
          </button>
        </div>
        <div v-if="!isTestMode && hasPagination(practiceUnitRows)" class="paginationControls">
          <button
            class="pageBtn"
            type="button"
            :disabled="currentPage('practiceUnitMatrix', practiceUnitRows) <= 1"
            @click="setPage('practiceUnitMatrix', currentPage('practiceUnitMatrix', practiceUnitRows) - 1, practiceUnitRows)"
          >
            上一頁
          </button>
          <span>{{ paginationText("practiceUnitMatrix", practiceUnitRows) }}</span>
          <button
            class="pageBtn"
            type="button"
            :disabled="currentPage('practiceUnitMatrix', practiceUnitRows) >= pageCount(practiceUnitRows)"
            @click="setPage('practiceUnitMatrix', currentPage('practiceUnitMatrix', practiceUnitRows) + 1, practiceUnitRows)"
          >
            下一頁
          </button>
        </div>
      </section>

      <section v-if="!isTestMode" class="panel">
        <div class="sectionHeader">
          <h2>學生 × 題目最新一輪</h2>
          <span>{{ practiceTaskLatestRows.length }} 列</span>
        </div>
        <div class="tableWrap analytics-table-wrapper">
          <table class="dataTable analytics-table practice-latest-table">
            <thead>
              <tr>
                <th>學號</th>
                <th>姓名</th>
                <th>單元</th>
                <th>題目</th>
                <th class="center">來源</th>
                <th class="center">作答輪次</th>
                <th class="center">輪內次數</th>
                <th class="center">結果</th>
                <th>最後作答時間</th>
                <th class="center">提示總次數</th>
                <th>提示摘要</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="row in pagedPracticeTaskLatestRows" :key="row.row_key">
                <tr>
                  <td class="mono">{{ row.student_id || "-" }}</td>
                  <td>{{ row.student_name || "-" }}</td>
                  <td>{{ row.unit_label || "-" }}</td>
                  <td>
                    <div class="cell-task-title">{{ row.task_title || row.task_id || "-" }}</div>
                    <div class="progress-sub mono">{{ row.task_id || "-" }}</div>
                  </td>
                  <td class="center">
                    <span class="source-badge" :class="taskSourceClass(row)">
                      {{ formatTaskSource(row) }}
                    </span>
                    <div v-if="row.task_code" class="progress-sub mono">{{ row.task_code }}</div>
                  </td>
                  <td class="center compact">{{ row.round_no || "-" }}</td>
                  <td class="center compact">{{ row.round_attempt_count || 0 }}</td>
                  <td class="center compact">
                    <span class="status-badge" :class="statusClass(row.status)">
                      {{ formatPracticeResult(row.result) }}
                    </span>
                  </td>
                  <td>{{ formatDateTime(row.last_submitted_at) }}</td>
                  <td class="center compact">{{ row.hint_total_count || 0 }}</td>
                  <td>
                    <button class="inlineActionBtn" type="button" @click="toggleHintJson(row)">
                      {{ isHintJsonExpanded(row) ? "收合 JSON" : "查看 JSON" }}
                    </button>
                    <div class="progress-sub">{{ row.hint_summary_text || "-" }}</div>
                  </td>
                </tr>
                <tr v-if="isHintJsonExpanded(row)" class="jsonExpandRow">
                  <td colspan="11">
                    <pre class="jsonPreview">{{ formatJson(row.hint_summary || {}) }}</pre>
                  </td>
                </tr>
              </template>
              <tr v-if="!loading && practiceTaskLatestRows.length === 0">
                <td class="empty" colspan="11">目前沒有符合條件的學生題目進度資料</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="hasPagination(practiceTaskLatestRows)" class="paginationControls">
          <button
            class="pageBtn"
            type="button"
            :disabled="currentPage('practiceLatestTasks', practiceTaskLatestRows) <= 1"
            @click="setPage('practiceLatestTasks', currentPage('practiceLatestTasks', practiceTaskLatestRows) - 1, practiceTaskLatestRows)"
          >
            上一頁
          </button>
          <span>{{ paginationText("practiceLatestTasks", practiceTaskLatestRows) }}</span>
          <button
            class="pageBtn"
            type="button"
            :disabled="currentPage('practiceLatestTasks', practiceTaskLatestRows) >= pageCount(practiceTaskLatestRows)"
            @click="setPage('practiceLatestTasks', currentPage('practiceLatestTasks', practiceTaskLatestRows) + 1, practiceTaskLatestRows)"
          >
            下一頁
          </button>
        </div>
      </section>

      <template v-if="analysisView === 'table'">
      <section class="kpiGrid">
        <article v-for="item in kpiItems" :key="item.key" class="kpiCard">
          <div class="kpiLabel">{{ item.label }}</div>
          <div class="kpiValue">{{ item.value }}</div>
        </article>
      </section>

      <section class="panel">
        <div class="sectionHeader">
          <h2>學生作答總覽</h2>
          <span>{{ studentRows.length }} 位學生</span>
        </div>
        <div class="tableWrap">
          <table class="dataTable">
            <thead>
              <tr>
                <th>學生編號</th>
                <th>班級</th>
                <th>組別</th>
                <th>作答題數</th>
                <th>提交數</th>
                <th>答對題數</th>
                <th>平均嘗試次數</th>
                <th>平均有效作答時間</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in pagedStudentRows"
                :key="row.student_id"
                :class="{ selected: selectedStudentId === row.student_id }"
                @click="selectStudent(row.student_id)"
              >
                <td>{{ row.student_id }}</td>
                <td>{{ row.class_name || "-" }}</td>
                <td>{{ formatGroupType(row) }}</td>
                <td>{{ row.task_count }}</td>
                <td>{{ row.total_attempts }}</td>
                <td>{{ row.correct_task_count }}</td>
                <td>{{ formatNumber(row.avg_attempts_per_task) }}</td>
                <td>{{ formatSeconds(row.avg_duration_sec) }}</td>
              </tr>
              <tr v-if="!loading && studentRows.length === 0">
                <td class="empty" colspan="8">目前沒有符合條件的作答資料</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="hasPagination(studentRows)" class="paginationControls">
          <button
            class="pageBtn"
            type="button"
            :disabled="currentPage('studentSummary', studentRows) <= 1"
            @click="setPage('studentSummary', currentPage('studentSummary', studentRows) - 1, studentRows)"
          >
            上一頁
          </button>
          <span>{{ paginationText("studentSummary", studentRows) }}</span>
          <button
            class="pageBtn"
            type="button"
            :disabled="currentPage('studentSummary', studentRows) >= pageCount(studentRows)"
            @click="setPage('studentSummary', currentPage('studentSummary', studentRows) + 1, studentRows)"
          >
            下一頁
          </button>
        </div>
      </section>

      <section class="analysisGrid">
        <article class="panel">
          <div class="sectionHeader">
            <h2>題目錯誤分析</h2>
            <span>{{ taskRows.length }} 題</span>
          </div>
          <div class="tableWrap analytics-table-wrapper">
            <table class="dataTable analytics-table task-error-table">
              <colgroup>
                <col style="width: 12%">
                <col style="width: 30%">
                <col style="width: 12%">
                <col style="width: 9%">
                <col style="width: 9%">
                <col style="width: 8%">
                <col style="width: 20%">
              </colgroup>
              <thead>
                <tr>
                  <th>題目ID</th>
                  <th>題目</th>
                  <th>概念</th>
                  <th class="center">提交數</th>
                  <th class="center">錯誤數</th>
                  <th class="center">答對率</th>
                  <th>常見錯誤</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in pagedTaskRows" :key="row.task_id">
                  <td class="mono task-id-cell">{{ row.task_id }}</td>
                  <td>
                    <div class="cell-task-title" :title="row.task_title || '-'">
                      {{ row.task_title || "-" }}
                    </div>
                  </td>
                  <td><span class="concept-badge">{{ row.target_concept || "unknown" }}</span></td>
                  <td class="center compact">{{ row.total_attempts }}</td>
                  <td class="center compact">{{ row.wrong_attempts }}</td>
                  <td class="center compact">{{ formatPercent(row.correct_rate) }}</td>
                  <td>
                    <span
                      v-for="item in row.common_error_types || []"
                      :key="`${row.task_id}-${item.type}`"
                      class="error-badge"
                    >
                      {{ formatCommonErrorBadge(item) }}
                    </span>
                    <span v-if="!row.common_error_types || row.common_error_types.length === 0">-</span>
                  </td>
                </tr>
                <tr v-if="!loading && taskRows.length === 0">
                  <td class="empty" colspan="7">目前沒有題目錯誤資料</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="hasPagination(taskRows)" class="paginationControls compactPagination">
            <button
              class="pageBtn"
              type="button"
              :disabled="currentPage('taskErrors', taskRows) <= 1"
              @click="setPage('taskErrors', currentPage('taskErrors', taskRows) - 1, taskRows)"
            >
              上一頁
            </button>
            <span>{{ paginationText("taskErrors", taskRows) }}</span>
            <button
              class="pageBtn"
              type="button"
              :disabled="currentPage('taskErrors', taskRows) >= pageCount(taskRows)"
              @click="setPage('taskErrors', currentPage('taskErrors', taskRows) + 1, taskRows)"
            >
              下一頁
            </button>
          </div>
        </article>

        <article class="panel">
          <div class="sectionHeader">
            <h2>概念錯誤分析</h2>
            <span>{{ conceptRows.length }} 個概念</span>
          </div>
          <div class="tableWrap">
            <table class="dataTable">
              <thead>
                <tr>
                  <th>概念</th>
                  <th>提交數</th>
                  <th>錯誤數</th>
                  <th>答對率</th>
                  <th>重複錯誤數</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in pagedConceptRows" :key="row.target_concept">
                  <td>{{ row.target_concept || "unknown" }}</td>
                  <td>{{ row.total_attempts }}</td>
                  <td>{{ row.wrong_attempts }}</td>
                  <td>{{ formatPercent(row.correct_rate) }}</td>
                  <td>{{ row.repeated_error_count }}</td>
                </tr>
                <tr v-if="!loading && conceptRows.length === 0">
                  <td class="empty" colspan="5">目前沒有概念錯誤資料</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="hasPagination(conceptRows)" class="paginationControls compactPagination">
            <button
              class="pageBtn"
              type="button"
              :disabled="currentPage('conceptErrors', conceptRows) <= 1"
              @click="setPage('conceptErrors', currentPage('conceptErrors', conceptRows) - 1, conceptRows)"
            >
              上一頁
            </button>
            <span>{{ paginationText("conceptErrors", conceptRows) }}</span>
            <button
              class="pageBtn"
              type="button"
              :disabled="currentPage('conceptErrors', conceptRows) >= pageCount(conceptRows)"
              @click="setPage('conceptErrors', currentPage('conceptErrors', conceptRows) + 1, conceptRows)"
            >
              下一頁
            </button>
          </div>
        </article>
      </section>

      <section class="panel">
        <div class="sectionHeader">
          <h2>單一學生學習資料</h2>
          <span>{{ selectedStudentId || "尚未選擇學生" }}</span>
        </div>

        <div class="studentDetailGrid">
          <article class="detailBlock">
            <h3>作答紀錄</h3>
            <div class="tableWrap analytics-table-wrapper">
              <table class="dataTable analytics-table student-attempt-table">
                <colgroup>
                  <col style="width: 11%">
                  <col style="width: 10%">
                  <col style="width: 26%">
                  <col style="width: 10%">
                  <col style="width: 7%">
                  <col style="width: 7%">
                  <col style="width: 6%">
                  <col style="width: 11%">
                  <col style="width: 7%">
                  <col style="width: 5%">
                </colgroup>
                <thead>
                  <tr>
                    <th>提交時間</th>
                    <th>題目ID</th>
                    <th>題目</th>
                    <th>概念</th>
                    <th class="center">次數</th>
                    <th class="center">是否答對</th>
                    <th class="center">分數</th>
                    <th>錯誤類型</th>
                    <th>錯誤位置</th>
                    <th class="center">作答時間</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, index) in pagedStudentAttempts" :key="`${row.task_id}-${row.attempt_no}-${index}`">
                    <td class="compact">{{ formatTaipeiDateTime(row.submitted_at) }}</td>
                    <td class="mono">{{ row.task_id || "-" }}</td>
                    <td>
                      <div class="cell-task-title" :title="row.task_title || '-'">
                        {{ row.task_title || "-" }}
                      </div>
                    </td>
                    <td><span class="concept-badge">{{ row.target_concept || "unknown" }}</span></td>
                    <td class="center compact">{{ row.attempt_no ?? "-" }}</td>
                    <td class="center compact">{{ formatCorrectness(row.is_correct) }}</td>
                    <td class="center compact">{{ formatNumber(row.score) }}</td>
                    <td>
                      <span
                        v-for="errorType in row.error_types || []"
                        :key="`${row.task_id}-${row.attempt_no}-${errorType}`"
                        class="error-badge"
                      >
                        {{ errorType }}
                      </span>
                      <span v-if="!row.error_types || row.error_types.length === 0">-</span>
                    </td>
                    <td class="slot-list">{{ formatArray(attemptIncorrectSlots(row)) }}</td>
                    <td class="center compact">{{ formatSeconds(row.duration_sec) }}</td>
                  </tr>
                  <tr v-if="!loading && selectedStudentId && studentAttempts.length === 0">
                    <td class="empty" colspan="10">此學生目前沒有符合模式的作答紀錄</td>
                  </tr>
                  <tr v-if="!selectedStudentId">
                    <td class="empty" colspan="10">請先選擇學生</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="hasPagination(studentAttempts)" class="paginationControls compactPagination">
              <button
                class="pageBtn"
                type="button"
                :disabled="currentPage('studentAttempts', studentAttempts) <= 1"
                @click="setPage('studentAttempts', currentPage('studentAttempts', studentAttempts) - 1, studentAttempts)"
              >
                上一頁
              </button>
              <span>{{ paginationText("studentAttempts", studentAttempts) }}</span>
              <button
                class="pageBtn"
                type="button"
                :disabled="currentPage('studentAttempts', studentAttempts) >= pageCount(studentAttempts)"
                @click="setPage('studentAttempts', currentPage('studentAttempts', studentAttempts) + 1, studentAttempts)"
              >
                下一頁
              </button>
            </div>
          </article>

          <article class="detailBlock">
            <h3>操作歷程</h3>
            <div class="tableWrap analytics-table-wrapper">
              <table class="dataTable analytics-table logTable student-log-table">
                <colgroup>
                  <col style="width: 13%">
                  <col style="width: 11%">
                  <col style="width: 8%">
                  <col style="width: 13%">
                  <col style="width: 13%">
                  <col style="width: 7%">
                  <col style="width: 11%">
                  <col style="width: 24%">
                </colgroup>
                <thead>
                  <tr>
                    <th>事件時間</th>
                    <th>事件類型</th>
                    <th>頁面</th>
                    <th>題目ID</th>
                    <th>作答紀錄ID</th>
                    <th class="center">次數</th>
                    <th>概念</th>
                    <th>事件摘要</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, index) in pagedStudentLogs" :key="`${row.event_at}-${index}`">
                    <td class="compact">{{ formatTaipeiDateTime(row.event_at) }}</td>
                    <td>
                      <span class="event-badge" :title="row.event_type || ''">
                        {{ formatEventType(row.event_type, row) }}
                      </span>
                    </td>
                    <td class="compact">{{ row.page || "-" }}</td>
                    <td class="mono">{{ row.task_id || "-" }}</td>
                    <td class="mono">{{ row.attempt_id || "-" }}</td>
                    <td class="center compact">{{ row.attempt_no ?? "-" }}</td>
                    <td><span class="concept-badge">{{ row.target_concept || "-" }}</span></td>
                    <td>
                      <div class="metadata-summary">{{ formatMetadataSummary(row) }}</div>
                      <details v-if="hasMetadata(row.metadata)" class="metadata-details">
                        <summary>查看詳細</summary>
                        <pre>{{ formatMetadata(row.metadata) }}</pre>
                      </details>
                    </td>
                  </tr>
                  <tr v-if="!loading && selectedStudentId && studentLogs.length === 0">
                    <td class="empty" colspan="8">此學生目前沒有符合模式的操作歷程</td>
                  </tr>
                  <tr v-if="!selectedStudentId">
                    <td class="empty" colspan="8">請先選擇學生</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="hasPagination(studentLogs)" class="paginationControls compactPagination">
              <button
                class="pageBtn"
                type="button"
                :disabled="currentPage('studentLogs', studentLogs) <= 1"
                @click="setPage('studentLogs', currentPage('studentLogs', studentLogs) - 1, studentLogs)"
              >
                上一頁
              </button>
              <span>{{ paginationText("studentLogs", studentLogs) }}</span>
              <button
                class="pageBtn"
                type="button"
                :disabled="currentPage('studentLogs', studentLogs) >= pageCount(studentLogs)"
                @click="setPage('studentLogs', currentPage('studentLogs', studentLogs) + 1, studentLogs)"
              >
                下一頁
              </button>
            </div>
          </article>
        </div>
      </section>
      </template>

      <template v-else-if="analysisView === 'visual'">
        <div v-if="groupType === 'test_data'" class="visualTestNotice" role="status">
          目前為測試資料模式，圖表僅供系統測試，不納入正式研究分析。
        </div>

        <section class="panel chartExportPanel">
          <div>
            <h2>視覺化圖表</h2>
            <p>
              {{ showAllCharts ? "目前顯示完整圖表資料。" : `目前每張圖先顯示前 ${chartPreviewLimit} 筆重點資料。` }}
            </p>
          </div>
          <div class="chartExportActions">
            <button class="btn" type="button" @click="showAllCharts = !showAllCharts">
              {{ showAllCharts ? "收合圖形" : "查看全部圖形" }}
            </button>
            <button class="btn primary" type="button" :disabled="pngBusy" @click="downloadVisualizationPng">
              {{ pngBusy ? "產生中..." : "下載全部圖形 ZIP" }}
            </button>
          </div>
        </section>

        <section class="visualizationGrid">
          <article class="panel chartPanel">
            <div class="sectionHeader">
              <h2>概念錯誤分布</h2>
              <span>{{ displayedConceptChartItems.length }} / {{ conceptChartItems.length }} 個概念</span>
            </div>
            <AnalyticsBarChart
              :items="displayedConceptChartItems"
              color="#c2413b"
              aria-label="各概念錯誤次數長條圖"
            />
          </article>

          <article class="panel chartPanel">
            <div class="sectionHeader">
              <h2>題目答對率</h2>
              <span>{{ displayedTaskCorrectRateChartItems.length }} / {{ taskCorrectRateChartItems.length }} 題</span>
            </div>
            <AnalyticsBarChart
              :items="displayedTaskCorrectRateChartItems"
              :max-value="100"
              value-suffix="%"
              color="#2563a6"
              aria-label="各題目答對率長條圖"
            />
          </article>

          <article class="panel chartPanel">
            <div class="sectionHeader">
              <h2>錯誤類型分布</h2>
              <span>{{ displayedErrorTypeChartItems.length }} / {{ errorTypeChartItems.length }} 種錯誤</span>
            </div>
            <AnalyticsBarChart
              :items="displayedErrorTypeChartItems"
              color="#b45309"
              aria-label="錯誤類型出現次數長條圖"
            />
          </article>

          <article class="panel chartPanel">
            <div class="sectionHeader">
              <h2>學生平均嘗試次數</h2>
              <span>{{ displayedStudentAttemptChartItems.length }} / {{ studentAttemptChartItems.length }} 位學生</span>
            </div>
            <AnalyticsBarChart
              :items="displayedStudentAttemptChartItems"
              color="#146c64"
              aria-label="各學生平均每題嘗試次數長條圖"
            />
          </article>
        </section>

        <section v-if="selectedStudentId" class="panel timelinePanel">
          <div class="sectionHeader">
            <h2>單一學生操作歷程</h2>
            <span>{{ selectedStudentId }}，{{ timelineEvents.length }} 筆事件</span>
          </div>

          <ol v-if="timelineEvents.length" class="timelineList">
            <li
              v-for="(event, index) in pagedTimelineEvents"
              :key="`${event.event_at}-${event.event_type}-${index}`"
              class="timelineItem"
            >
              <div class="timelineMarker" aria-hidden="true"></div>
              <div class="timelineContent">
                <div class="timelineHeader">
                  <span class="event-badge" :title="event.event_type || ''">
                    {{ formatEventType(event.event_type, event) }}
                  </span>
                  <time>{{ formatTaipeiDateTime(event.event_at) }}</time>
                </div>
                <div class="timelineContext">
                  <span v-if="event.task_id">題目ID：{{ event.task_id }}</span>
                  <span v-if="event.attempt_no != null">第 {{ event.attempt_no }} 次提交</span>
                </div>
                <div v-if="hasMetadata(event.metadata)" class="timelineSummary">
                  {{ formatMetadataSummary(event) }}
                </div>
              </div>
            </li>
          </ol>
          <div v-if="hasPagination(timelineEvents)" class="paginationControls compactPagination">
            <button
              class="pageBtn"
              type="button"
              :disabled="currentPage('studentTimeline', timelineEvents) <= 1"
              @click="setPage('studentTimeline', currentPage('studentTimeline', timelineEvents) - 1, timelineEvents)"
            >
              上一頁
            </button>
            <span>{{ paginationText("studentTimeline", timelineEvents) }}</span>
            <button
              class="pageBtn"
              type="button"
              :disabled="currentPage('studentTimeline', timelineEvents) >= pageCount(timelineEvents)"
              @click="setPage('studentTimeline', currentPage('studentTimeline', timelineEvents) + 1, timelineEvents)"
            >
              下一頁
            </button>
          </div>
          <div v-if="!timelineEvents.length" class="visualizationEmpty">目前沒有符合條件的資料可視覺化。</div>
        </section>
      </template>

      <template v-else>
        <section class="panel">
          <div class="sectionHeader">
            <h2>影片觀看紀錄</h2>
            <span>資料來源：video_rewatch_logs</span>
          </div>
          <p class="note">
            此分頁僅呈現學生影片觀看歷程，作為輔助資料；不納入 Parsons 主分析指標。
          </p>
          <p v-if="videoRewatchError" class="message error">{{ videoRewatchError }}</p>
          <section class="kpiGrid">
            <article class="kpiCard">
              <div class="kpiLabel">有觀看紀錄學生數</div>
              <div class="kpiValue">{{ videoRewatchTotals.student_count || 0 }}</div>
            </article>
            <article class="kpiCard">
              <div class="kpiLabel">觀看事件筆數</div>
              <div class="kpiValue">{{ videoRewatchTotals.record_count || 0 }}</div>
            </article>
            <article class="kpiCard">
              <div class="kpiLabel">總觀看時長</div>
              <div class="kpiValue">{{ formatSeconds(videoRewatchTotals.total_watch_seconds) }}</div>
            </article>
          </section>
        </section>

        <section class="panel">
          <div class="sectionHeader">
            <h2>每位學生觀看摘要</h2>
            <span>{{ videoRewatchSummary.length }} 位學生</span>
          </div>
          <div class="tableWrap analytics-table-wrapper">
            <table class="dataTable analytics-table">
              <thead>
                <tr>
                  <th>學生 ID</th>
                  <th>姓名</th>
                  <th>班級</th>
                  <th>組別</th>
                  <th class="center">觀看事件</th>
                  <th class="center">影片數</th>
                  <th class="center">總觀看時長</th>
                  <th class="center">平均每筆</th>
                  <th class="center">看完次數</th>
                  <th>最後觀看時間</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in pagedVideoRewatchSummary" :key="row.student_id">
                  <td class="mono">{{ row.student_id }}</td>
                  <td>{{ row.student_name || "-" }}</td>
                  <td>{{ row.class_name || "-" }}</td>
                  <td>{{ formatGroupType(row) }}</td>
                  <td class="center compact">{{ row.record_count || 0 }}</td>
                  <td class="center compact">{{ row.video_count || 0 }}</td>
                  <td class="center compact">{{ formatSeconds(row.total_watch_seconds) }}</td>
                  <td class="center compact">{{ formatSeconds(row.avg_watch_seconds) }}</td>
                  <td class="center compact">{{ row.completed_count || 0 }}</td>
                  <td class="compact">{{ formatTaipeiDateTime(row.latest_event_at) }}</td>
                </tr>
                <tr v-if="!loading && videoRewatchSummary.length === 0">
                  <td class="empty" colspan="10">目前沒有符合條件的影片觀看紀錄。</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="hasPagination(videoRewatchSummary)" class="paginationControls compactPagination">
            <button
              class="pageBtn"
              type="button"
              :disabled="currentPage('videoSummary', videoRewatchSummary) <= 1"
              @click="setPage('videoSummary', currentPage('videoSummary', videoRewatchSummary) - 1, videoRewatchSummary)"
            >
              上一頁
            </button>
            <span>{{ paginationText("videoSummary", videoRewatchSummary) }}</span>
            <button
              class="pageBtn"
              type="button"
              :disabled="currentPage('videoSummary', videoRewatchSummary) >= pageCount(videoRewatchSummary)"
              @click="setPage('videoSummary', currentPage('videoSummary', videoRewatchSummary) + 1, videoRewatchSummary)"
            >
              下一頁
            </button>
          </div>
        </section>

        <section class="panel">
          <div class="sectionHeader">
            <h2>觀看事件明細</h2>
            <span>{{ videoRewatchRecords.length }} 筆紀錄</span>
          </div>
          <div class="tableWrap analytics-table-wrapper">
            <table class="dataTable analytics-table video-rewatch-table">
              <thead>
                <tr>
                  <th>學生 ID</th>
                  <th>姓名</th>
                  <th class="center">跳轉方向</th>
                  <th class="center">跳轉起點</th>
                  <th class="center">跳轉終點</th>
                  <th class="center">跳轉秒數</th>
                  <th class="center">倒退回看</th>
                  <th class="center">播放倍速</th>
                  <th>觀看時間</th>
                  <th>影片</th>
                  <th>事件</th>
                  <th class="center">觀看秒數</th>
                  <th class="center">本次增量</th>
                  <th class="center">目前進度</th>
                  <th class="center">影片長度</th>
                  <th class="center">是否看完</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in pagedVideoRewatchRecords" :key="row.log_id">
                  <td class="mono">{{ row.student_id || "-" }}</td>
                  <td>{{ row.student_name || "-" }}</td>
                  <td class="center compact">{{ formatSeekDirection(row.seek_direction) }}</td>
                  <td class="center compact">{{ formatSeconds(row.seek_from_sec) }}</td>
                  <td class="center compact">{{ formatSeconds(row.seek_to_sec) }}</td>
                  <td class="center compact">{{ formatSignedSeconds(row.seek_delta_sec) }}</td>
                  <td class="center compact">{{ row.is_backward_seek ? "是" : "-" }}</td>
                  <td class="center compact">{{ formatPlaybackRate(row) }}</td>
                  <td class="video-event-time-cell">
                    <span>{{ formatVideoEventTimeParts(row.event_at).date }}</span>
                    <span>{{ formatVideoEventTimeParts(row.event_at).time }}</span>
                  </td>
                  <td>
                    <div class="cell-task-title" :title="row.video_title || row.video_id || '-'">
                      {{ row.video_title || row.video_id || "-" }}
                    </div>
                    <div class="progress-sub">{{ row.unit_id || "-" }}</div>
                  </td>
                  <td>
                    <span class="event-badge">{{ formatEventType(row.event_type, row) }}</span>
                  </td>
                  <td class="center compact">{{ formatSeconds(row.watch_seconds) }}</td>
                  <td class="center compact">{{ formatSeconds(row.watch_delta_sec) }}</td>
                  <td class="center compact">{{ formatSeconds(row.current_time_sec) }}</td>
                  <td class="center compact">{{ formatSeconds(row.video_duration_sec) }}</td>
                  <td class="center compact">{{ row.reached_end || row.completed_fully ? "是" : "否" }}</td>
                </tr>
                <tr v-if="!loading && videoRewatchRecords.length === 0">
                  <td class="empty" colspan="16">目前沒有符合條件的 video_rewatch_logs 紀錄。</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="hasPagination(videoRewatchRecords)" class="paginationControls compactPagination">
            <button
              class="pageBtn"
              type="button"
              :disabled="currentPage('videoRecords', videoRewatchRecords) <= 1"
              @click="setPage('videoRecords', currentPage('videoRecords', videoRewatchRecords) - 1, videoRewatchRecords)"
            >
              上一頁
            </button>
            <span>{{ paginationText("videoRecords", videoRewatchRecords) }}</span>
            <button
              class="pageBtn"
              type="button"
              :disabled="currentPage('videoRecords', videoRewatchRecords) >= pageCount(videoRewatchRecords)"
              @click="setPage('videoRecords', currentPage('videoRecords', videoRewatchRecords) + 1, videoRewatchRecords)"
            >
              下一頁
            </button>
          </div>
        </section>
      </template>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import AnalyticsBarChart from "../components/AnalyticsBarChart.vue";
import TeacherSidebar from "../components/TeacherSidebar.vue";
import { formatTaipeiDateTime } from "../utils/dateTime.js";

const API_BASE = (import.meta?.env?.VITE_API_BASE || "").replace(/\/$/, "");

const modeOptions = [
  { value: "practice", label: "平時練習", activityType: "practice", testRole: null },
  { value: "pretest", label: "前測", activityType: "test", testRole: "pretest" },
  { value: "posttest", label: "後測", activityType: "test", testRole: "posttest" },
];

const selectedMode = ref("practice");
const analysisView = ref("table");
const className = ref("");
const groupType = ref("");
const selectedStudentId = ref("");
const loading = ref(false);
const csvBusy = ref(false);
const pngBusy = ref(false);
const errorMsg = ref("");
const csvMessage = ref("");
const invalidRows = ref([]);
const studentCsvInput = ref(null);
const analysis = ref(emptyAnalysis());
const videoRewatch = ref(emptyVideoRewatch());
const videoRewatchError = ref("");
const studentOptions = ref([]);
const expandedPracticeUnit = reactive({
  studentId: "",
  unitKey: "",
  unitLabel: "",
});
const expandedHintJsonKey = ref("");
// 分頁功能
const pageSize = 5;
const chartPreviewLimit = 10;
const showAllCharts = ref(false);
const pagination = reactive({
  practiceUnitMatrix: 1,
  practiceLatestTasks: 1,
  studentProgress: 1,
  studentSummary: 1,
  taskErrors: 1,
  conceptErrors: 1,
  studentAttempts: 1,
  studentLogs: 1,
  studentTimeline: 1,
  videoSummary: 1,
  videoRecords: 1,
});

const selectedModeConfig = computed(() => (
  modeOptions.find((option) => option.value === selectedMode.value) || modeOptions[0]
));
const kpis = computed(() => analysis.value.kpis || {});
const userGroupOverview = computed(() => analysis.value.user_group_overview || {});
const testCompletionOverview = computed(() => analysis.value.test_completion_overview || {});
const testCompletionOverviews = computed(() => analysis.value.test_completion_overviews || {});
const pretestCompletion = computed(() => testCompletionOverviews.value.pretest || {});
const posttestCompletion = computed(() => testCompletionOverviews.value.posttest || {});
const studentProgressRows = computed(() => analysis.value.student_progress_rows || []);
const practiceUnitColumns = computed(() => analysis.value.practice_unit_columns || []);
const practiceUnitRows = computed(() => analysis.value.practice_unit_progress_rows || []);
const practiceTaskLatestRows = computed(() => analysis.value.practice_task_latest_rows || []);
const studentRows = computed(() => analysis.value.student_overview || []);
const taskRows = computed(() => analysis.value.task_error_analysis || []);
const conceptRows = computed(() => analysis.value.concept_error_analysis || []);
const studentAttempts = computed(() => analysis.value.student_attempts || []);
const studentLogs = computed(() => analysis.value.student_logs || []);
const videoRewatchTotals = computed(() => videoRewatch.value.summary || {});
const videoRewatchSummary = computed(() => videoRewatch.value.student_summary || []);
const videoRewatchRecords = computed(() => videoRewatch.value.records || []);
const pagedStudentProgressRows = computed(() => pageItems(studentProgressRows.value, "studentProgress"));
const pagedPracticeUnitRows = computed(() => pageItems(practiceUnitRows.value, "practiceUnitMatrix"));
const pagedPracticeTaskLatestRows = computed(() => pageItems(practiceTaskLatestRows.value, "practiceLatestTasks"));
const pagedStudentRows = computed(() => pageItems(studentRows.value, "studentSummary"));
const pagedTaskRows = computed(() => pageItems(taskRows.value, "taskErrors"));
const pagedConceptRows = computed(() => pageItems(conceptRows.value, "conceptErrors"));
const pagedStudentAttempts = computed(() => pageItems(studentAttempts.value, "studentAttempts"));
const pagedStudentLogs = computed(() => pageItems(studentLogs.value, "studentLogs"));
const pagedVideoRewatchSummary = computed(() => pageItems(videoRewatchSummary.value, "videoSummary"));
const pagedVideoRewatchRecords = computed(() => pageItems(videoRewatchRecords.value, "videoRecords"));
const isTestMode = computed(() => selectedMode.value === "pretest" || selectedMode.value === "posttest");
const selectedTestCompletion = computed(() => (
  selectedMode.value === "posttest" ? posttestCompletion.value : pretestCompletion.value
));
const progressTableTitle = computed(() => (
  isTestMode.value ? `${selectedModeConfig.value.label}學生進度列表` : "平時練習進度列表"
));
const progressCountLabel = computed(() => (
  isTestMode.value ? `${studentProgressRows.value.length} 位學生` : `${practiceUnitRows.value.length} 位學生`
));
const practiceMatrixColspan = computed(() => Math.max(4 + practiceUnitColumns.value.length, 4));
const conceptChartItems = computed(() => (
  [...conceptRows.value]
    .sort((a, b) => Number(b.wrong_attempts || 0) - Number(a.wrong_attempts || 0))
    .map((row, index) => {
      const concept = row.target_concept || "unknown";
      const wrongAttempts = Number(row.wrong_attempts) || 0;
      return {
        key: `${concept}-${index}`,
        label: concept,
        value: wrongAttempts,
        displayValue: String(wrongAttempts),
        tooltip: [
          `概念：${concept}`,
          `錯誤次數：${wrongAttempts}`,
          `提交數：${Number(row.total_attempts) || 0}`,
          `答對率：${formatPercent(row.correct_rate)}`,
        ].join(" | "),
      };
    })
));
const taskCorrectRateChartItems = computed(() => (
  [...taskRows.value]
    .sort((a, b) => Number(a.correct_rate || 0) - Number(b.correct_rate || 0))
    .map((row, index) => {
      const taskId = String(row.task_id || "-");
      const taskTitle = String(row.task_title || taskId);
      const ratePercent = Math.max(0, Math.min(100, (Number(row.correct_rate) || 0) * 100));
      return {
        key: `${taskId}-${index}`,
        label: shortChartLabel(taskTitle, taskId),
        value: ratePercent,
        displayValue: `${Math.round(ratePercent * 10) / 10}%`,
        tooltip: [
          `題目：${taskTitle}`,
          `題目ID：${taskId}`,
          `提交數：${Number(row.total_attempts) || 0}`,
          `答對率：${Math.round(ratePercent * 10) / 10}%`,
        ].join(" | "),
      };
    })
));
const errorTypeChartItems = computed(() => {
  const counts = new Map();
  for (const task of taskRows.value) {
    for (const item of task.common_error_types || []) {
      const type = String(item?.type || "").trim();
      if (!type) continue;
      counts.set(type, (counts.get(type) || 0) + (Number(item.count) || 0));
    }
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([type, count]) => ({
      key: type,
      label: type,
      value: count,
      displayValue: String(count),
      tooltip: `錯誤類型：${type} | 出現次數：${count}`,
    }));
});
const studentAttemptChartItems = computed(() => (
  [...studentRows.value]
    .sort((a, b) => Number(b.avg_attempts_per_task || 0) - Number(a.avg_attempts_per_task || 0))
    .map((row) => {
      const studentId = String(row.student_id || "-");
      const average = Number(row.avg_attempts_per_task) || 0;
      return {
        key: studentId,
        label: studentId,
        value: average,
        displayValue: formatNumber(average),
        tooltip: [
          `學生：${studentId}`,
          `作答題數：${Number(row.task_count) || 0}`,
          `總提交次數：${Number(row.total_attempts) || 0}`,
          `平均每題提交：${formatNumber(average)}`,
        ].join(" | "),
      };
    })
));

function chartDisplayItems(items) {
  const rows = Array.isArray(items) ? items : [];
  return showAllCharts.value ? rows : rows.slice(0, chartPreviewLimit);
}

const displayedConceptChartItems = computed(() => chartDisplayItems(conceptChartItems.value));
const displayedTaskCorrectRateChartItems = computed(() => chartDisplayItems(taskCorrectRateChartItems.value));
const displayedErrorTypeChartItems = computed(() => chartDisplayItems(errorTypeChartItems.value));
const displayedStudentAttemptChartItems = computed(() => chartDisplayItems(studentAttemptChartItems.value));
const timelineEvents = computed(() => {
  const allowed = new Set([
    "task_open",
    "task_start",
    "video_click",
    "video_play",
    "video_pause",
    "video_progress",
    "video_ended",
    "video_leave",
    "click_next_to_practice",
    "enter_parsons_task",
    "answer_submit",
    "view_hint",
    "hide_hint",
    "review_open",
    "review_close",
    "first_error_hint_shown",
    "ai_hint_modal_open",
    "review_code_from_hint",
    "ai_hint_modal_close",
    "return_to_fix_from_hint",
    "ai_hint_reopen",
    "ai_hint_view",
    "submit_after_hint",
    "ai_hint_second_request",
    "second_hint_reminder_shown",
    "second_hint_reminder_clicked",
    "second_hint_reminder_ignored",
    "return_to_task",
  ]);
  return studentLogs.value
    .filter((event) => allowed.has(event.event_type))
    .slice()
    .sort((a, b) => new Date(a.event_at || 0) - new Date(b.event_at || 0));
});
const pagedTimelineEvents = computed(() => pageItems(timelineEvents.value, "studentTimeline"));
const groupHint = computed(() => {
  if (groupType.value === "control") return "目前顯示控制組正式資料，已排除測試資料。";
  if (groupType.value === "experimental") return "目前顯示實驗組正式資料，已排除測試資料。";
  if (groupType.value === "test_data") {
    return "目前為測試資料模式，僅供系統測試，不納入正式研究分析。";
  }
  return "目前顯示全部組別資料，包含控制組、實驗組與測試資料。";
});
const kpiItems = computed(() => [
  { key: "active_students", label: "參與學生數", value: kpis.value.active_students ?? 0 },
  { key: "total_attempts", label: "總提交次數", value: kpis.value.total_attempts ?? 0 },
  {
    key: "first_try_correct_rate",
    label: "首次答對率",
    value: formatPercent(kpis.value.first_try_correct_rate),
  },
  {
    key: "final_correct_rate",
    label: "最終答對率",
    value: formatPercent(kpis.value.final_correct_rate),
  },
  {
    key: "avg_attempts_per_task",
    label: "平均嘗試次數",
    value: formatNumber(kpis.value.avg_attempts_per_task),
  },
  {
    key: "avg_duration_sec",
    label: "平均有效作答時間",
    value: formatSeconds(kpis.value.avg_duration_sec),
  },
]);

function emptyAnalysis() {
  return {
    kpis: {
      active_students: 0,
      total_attempts: 0,
      first_try_correct_rate: 0,
      final_correct_rate: 0,
      avg_attempts_per_task: 0,
      avg_duration_sec: null,
    },
    user_group_overview: {
      experimental_count: 0,
      control_count: 0,
      test_account_count: 0,
      formal_student_count: 0,
      total_student_count: 0,
    },
    test_completion_overview: null,
    test_completion_overviews: {
      pretest: null,
      posttest: null,
    },
    data_sources: [],
    wrong_slot_distribution: [],
    wrong_type_distribution: [],
    intervention_metrics: {
      wrong_attempt_count: 0,
      hint_intervention_count: 0,
      hint_intervention_rate: 0,
      corrected_after_hint_count: 0,
      corrected_after_hint_rate: 0,
      no_hint_wrong_count: 0,
      corrected_without_hint_count: 0,
      corrected_without_hint_rate: 0,
    },
    pre_post_score_gain: {
      matched_student_count: 0,
      avg_score_gain: null,
      rows: [],
      data_sources: [],
    },
    student_progress_rows: [],
    practice_unit_columns: [],
    practice_unit_progress_rows: [],
    practice_task_latest_rows: [],
    student_overview: [],
    task_error_analysis: [],
    concept_error_analysis: [],
    student_attempts: [],
    student_logs: [],
  };
}

function emptyVideoRewatch() {
  return {
    ok: true,
    data_source: "video_rewatch_logs",
    summary: {
      student_count: 0,
      record_count: 0,
      total_watch_seconds: 0,
    },
    student_summary: [],
    records: [],
  };
}

function pageCount(items) {
  const total = Array.isArray(items) ? items.length : 0;
  return Math.max(1, Math.ceil(total / pageSize));
}

function currentPage(key, items) {
  const totalPages = pageCount(items);
  return Math.min(Math.max(Number(pagination[key]) || 1, 1), totalPages);
}

function pageItems(items, key) {
  const rows = Array.isArray(items) ? items : [];
  const page = currentPage(key, rows);
  const start = (page - 1) * pageSize;
  return rows.slice(start, start + pageSize);
}

function hasPagination(items) {
  return Array.isArray(items) && items.length > pageSize;
}

function setPage(key, page, items) {
  pagination[key] = Math.min(Math.max(Number(page) || 1, 1), pageCount(items));
}

function paginationText(key, items) {
  const rows = Array.isArray(items) ? items : [];
  const page = currentPage(key, rows);
  const start = rows.length ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(page * pageSize, rows.length);
  return `${start}-${end} / ${rows.length}`;
}

function resetPagination() {
  for (const key of Object.keys(pagination)) {
    pagination[key] = 1;
  }
}

function resetPracticeExpansions() {
  expandedPracticeUnit.studentId = "";
  expandedPracticeUnit.unitKey = "";
  expandedPracticeUnit.unitLabel = "";
  expandedHintJsonKey.value = "";
}

function clampPagination() {
  const sources = {
    practiceUnitMatrix: practiceUnitRows.value,
    practiceLatestTasks: practiceTaskLatestRows.value,
    studentProgress: studentProgressRows.value,
    studentSummary: studentRows.value,
    taskErrors: taskRows.value,
    conceptErrors: conceptRows.value,
    studentAttempts: studentAttempts.value,
    studentLogs: studentLogs.value,
    studentTimeline: timelineEvents.value,
    videoSummary: videoRewatchSummary.value,
    videoRecords: videoRewatchRecords.value,
  };
  for (const [key, rows] of Object.entries(sources)) {
    pagination[key] = currentPage(key, rows);
  }
}

function shortChartLabel(value, fallback = "-") {
  const text = String(value || fallback).trim() || fallback;
  return text.length > 14 ? `${text.slice(0, 13)}…` : text;
}

function formatPercent(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0%";
  return `${Math.round(n * 1000) / 10}%`;
}

function formatNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return n.toFixed(2).replace(/\.00$/, "");
}

function formatSeconds(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `${Math.round(n * 10) / 10}s`;
}

function formatVideoEventTimeParts(value) {
  const text = formatTaipeiDateTime(value, "");
  if (!text) return { date: "-", time: "" };
  const [date, time = ""] = text.split(" ");
  return { date, time };
}

function formatSignedSeconds(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  const rounded = Math.round(n * 10) / 10;
  return `${rounded > 0 ? "+" : ""}${rounded}s`;
}

const seekDirectionLabels = {
  forward: "向前跳轉",
  backward: "倒退回看",
  minor_adjustment: "微調",
};

function formatSeekDirection(value) {
  return seekDirectionLabels[String(value || "")] || "-";
}

function formatRate(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "-";
  return `${Math.round(n * 100) / 100}x`;
}

function formatPlaybackRate(row) {
  const from = row?.playback_rate_from;
  const to = row?.playback_rate_to;
  if (row?.event_type === "video_rate_change" && Number.isFinite(Number(from)) && Number.isFinite(Number(to))) {
    return `${formatRate(from)} -> ${formatRate(to)}`;
  }
  return formatRate(row?.playback_rate_current ?? row?.playback_rate);
}

function formatErrorTypes(value) {
  if (!Array.isArray(value) || value.length === 0) return "-";
  return value.map((item) => `${item.type}(${item.count})`).join(", ");
}

function formatCommonErrorBadge(item) {
  if (!item || typeof item !== "object") return "-";
  return item.count ? `${item.type} (${item.count})` : item.type;
}

function formatArray(value) {
  return Array.isArray(value) && value.length ? value.join(", ") : "-";
}

function attemptIncorrectSlots(row) {
  if (Array.isArray(row?.incorrect_slots)) return row.incorrect_slots;
  if (Array.isArray(row?.wrong_slots)) return row.wrong_slots;
  return [];
}

const eventTypeLabels = {
  session_start: "工作階段開始",
  session_end: "工作階段結束",
  page_view: "進入頁面",
  video_click: "點擊影片",
  video_play: "播放影片",
  video_pause: "暫停影片",
  video_progress: "觀看影片",
  video_ended: "影片結束",
  video_leave: "離開影片",
  click_next_to_practice: "點擊下一步",
  enter_parsons_task: "進入 Parsons 題目",
  task_open: "開啟題目",
  task_start: "開始作答",
  answer_submit: "提交 Parsons",
  submit_parsons: "提交 Parsons",
  view_hint: "查看提示",
  hide_hint: "收起提示",
  review_open: "開啟提示",
  review_close: "關閉提示",
  return_to_task: "返回題目",
  idle_detected: "偵測閒置",
  heartbeat: "活躍確認",
};

Object.assign(eventTypeLabels, {
  first_error_hint_shown: "首次系統提示",
  ai_hint_modal_open: "開啟 AI 提示",
  review_code_from_hint: "回看第一次提示",
  ai_hint_modal_close: "關閉 AI 提示",
  return_to_fix_from_hint: "返回題目修正",
  ai_hint_reopen: "重開 AI 提示",
  ai_hint_view: "查看 AI 提示",
  submit_after_hint: "提示後提交",
  ai_hint_second_request: "查看第二次 AI 提示",
  second_hint_reminder_shown: "第二次提示提醒顯示",
  second_hint_reminder_clicked: "點擊第二次提示提醒",
  second_hint_reminder_ignored: "未查看第二次提示",
});

Object.assign(eventTypeLabels, {
  video_seek: "影片跳轉",
  video_rate_change: "倍速變更",
});

const metadataValueLabels = {
  ai_hint: "AI 提示",
  click_ai_hint: "點擊 AI 提示",
  click_blank_area: "點擊空白處",
  click_ai_hint_again: "再次點擊 AI 提示",
  click_hint_retry: "點擊再次提示",
  click_close_button: "點擊關閉按鈕",
};

function formatCorrectness(value) {
  if (value === true) return "是";
  if (value === false) return "否";
  return "-";
}

function formatGroupType(row) {
  if (row?.is_test_data === true) return "測試資料";
  if (row?.group_type === "control") return "控制組";
  if (row?.group_type === "experimental") return "實驗組";
  return row?.group_type || "-";
}

function formatProgressStatus(value) {
  if (value === "completed") return "完成";
  if (value === "in_progress") return "進行中";
  if (value === "not_started") return "未開始";
  return "-";
}

function statusClass(value) {
  if (value === "completed") return "done";
  if (value === "in_progress") return "doing";
  return "idle";
}

function formatEventType(value, row = null) {
  const metadata = hasMetadata(row?.metadata) ? row.metadata : {};
  const triggerMethod = metadata.trigger_method || row?.trigger_method;
  const buttonName = metadata.button_name || row?.button_name;
  if ((value === "view_hint" || value === "review_open")
    && (triggerMethod === "click_hint_retry" || buttonName === "再次提示")) {
    return "再次提示";
  }
  return eventTypeLabels[value] || value || "-";
}

function formatMetadataValue(value) {
  return metadataValueLabels[value] || value;
}

function compactMetadataText(value, maxLength = 90) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function hasMetadata(value) {
  return Boolean(value && typeof value === "object" && Object.keys(value).length);
}

function formatMetadataSummary(row) {
  const metadata = hasMetadata(row?.metadata) ? row.metadata : {};
  if (!hasMetadata(metadata) && !row?.hint_content && !row?.hint_click_no) return "-";

  const parts = [];
  if (["video_click", "video_play", "video_pause", "video_progress", "video_ended", "video_leave"].includes(row.event_type)) {
    const title = row.from_video_title || metadata.video_title;
    const videoId = row.from_video_id || metadata.video_id;
    const watchSeconds = metadata.watch_seconds;
    const deltaSeconds = metadata.watch_delta_sec;
    if (title) parts.push(`影片：${title}`);
    else if (videoId) parts.push(`影片ID：${videoId}`);
    if (watchSeconds !== null && watchSeconds !== undefined) {
      parts.push(`累積觀看：${formatSeconds(watchSeconds)}`);
    }
    if (deltaSeconds !== null && deltaSeconds !== undefined) {
      parts.push(`本次增加：${formatSeconds(deltaSeconds)}`);
    }
    if (metadata.current_time_sec !== null && metadata.current_time_sec !== undefined) {
      parts.push(`目前時間：${formatSeconds(metadata.current_time_sec)}`);
    }
    if (metadata.reached_end === true) parts.push("已看至片尾");
  } else if (row.event_type === "click_next_to_practice") {
    const title = row.from_video_title || metadata.from_video_title;
    const toTaskId = row.to_task_id || metadata.to_task_id;
    if (title) parts.push(`來源影片：${title}`);
    if (row.watch_session_id || metadata.watch_session_id) {
      parts.push(`觀看序列：${row.watch_session_id || metadata.watch_session_id}`);
    }
    parts.push(`前往：${toTaskId || "尚未指定題目"} / Parsons`);
  } else if (row.event_type === "enter_parsons_task") {
    const taskId = row.task_id || metadata.task_id;
    const title = row.from_video_title || metadata.from_video_title;
    if (title) parts.push(`來源影片：${title}`);
    if (row.watch_session_id || metadata.watch_session_id) {
      parts.push(`觀看序列：${row.watch_session_id || metadata.watch_session_id}`);
    }
    parts.push(`進入題目：${taskId || "-"}`);
  } else if (row.event_type === "answer_submit") {
    if (typeof metadata.is_correct === "boolean") {
      parts.push(`是否答對：${formatCorrectness(metadata.is_correct)}`);
    }
    if (metadata.score !== null && metadata.score !== undefined) {
      parts.push(`分數：${formatNumber(metadata.score)}`);
    }
    if (Array.isArray(metadata.error_types)) {
      parts.push(`錯誤類型：${metadata.error_types.length ? metadata.error_types.join("、") : "無"}`);
    }
  } else if ([
    "first_error_hint_shown",
    "ai_hint_modal_open",
    "review_code_from_hint",
    "ai_hint_modal_close",
    "return_to_fix_from_hint",
    "ai_hint_reopen",
    "ai_hint_view",
    "submit_after_hint",
    "ai_hint_second_request",
    "second_hint_reminder_shown",
    "second_hint_reminder_clicked",
    "second_hint_reminder_ignored",
  ].includes(row.event_type)) {
    const hintId = metadata.hint_id || row.hint_id;
    const hintNo = metadata.requested_hint_no ?? metadata.hint_no ?? row.requested_hint_no ?? row.hint_no;
    if (hintId) parts.push(`hint_id：${hintId}`);
    if (hintNo) parts.push(`第 ${hintNo} 則提示`);
    const generationCount = metadata.hint_generation_count ?? metadata.ai_hint_generation_count;
    const viewCount = metadata.hint_view_count ?? metadata.ai_hint_view_count;
    if (generationCount !== null && generationCount !== undefined) {
      parts.push(`AI生成：${generationCount} 次`);
    }
    if (viewCount !== null && viewCount !== undefined) {
      parts.push(`查看：${viewCount} 次`);
    }
    if (Array.isArray(metadata.error_types) && metadata.error_types.length) {
      parts.push(`錯誤類型：${metadata.error_types.join("、")}`);
    }
    const incorrectSlots = Array.isArray(metadata.incorrect_slots)
      ? metadata.incorrect_slots
      : metadata.wrong_slots;
    if (Array.isArray(incorrectSlots) && incorrectSlots.length) {
      parts.push(`錯誤位置：${incorrectSlots.join(", ")}`);
    }
    const hintText = compactMetadataText(
      metadata.hint_content
      || metadata.hint_text
      || metadata.ai_hint_2_text
      || metadata.ai_hint_1_text
      || metadata.first_system_hint_text
    );
    if (hintText) parts.push(`提示：${hintText}`);
  } else if (row.event_type === "view_hint" || row.event_type === "review_open") {
    const hintNo = metadata.hint_click_no ?? metadata.hint_no ?? row.hint_click_no;
    if (hintNo !== null && hintNo !== undefined) {
      parts.push(`第 ${hintNo} 次提示`);
    }
    const reviewType = metadata.review_type || row.review_type;
    if (reviewType) {
      parts.push(`提示類型：${formatMetadataValue(reviewType)}`);
    }
    const triggerMethod = metadata.trigger_method || row.trigger_method;
    if (triggerMethod) {
      parts.push(`動作：${formatMetadataValue(triggerMethod)}`);
    }
    const buttonName = metadata.button_name || row.button_name;
    if (buttonName) {
      parts.push(`按鈕：${buttonName}`);
    }
    const hintText = compactMetadataText(row.hint_content || row.hint_text || metadata.hint_content || metadata.hint_text || metadata.first_hint || metadata.concept_hint);
    if (hintText) {
      parts.push(`提示內容：${hintText}`);
    }
    if (metadata.hint_source) {
      parts.push(`來源：${formatMetadataValue(metadata.hint_source)}`);
    }
    if (metadata.hint_limit_reached === true) parts.push("已達提示上限");
  } else if (row.event_type === "hide_hint" || row.event_type === "review_close") {
    if (metadata.close_method) {
      parts.push(`關閉方式：${formatMetadataValue(metadata.close_method)}`);
    }
    if (metadata.next_hint_no) {
      parts.push(`接著查看第 ${metadata.next_hint_no} 次提示`);
    }
    const hintText = compactMetadataText(row.hint_content || metadata.hint_content || metadata.hint_text || metadata.first_hint || metadata.concept_hint);
    if (hintText) {
      parts.push(`提示內容：${hintText}`);
    }
  } else if (row.event_type === "return_to_task") {
    const returnMethod = metadata.return_source
      ?? metadata.return_method
      ?? metadata.source
      ?? metadata.from_page
      ?? metadata.from;
    if (returnMethod) parts.push(`返回來源／方式：${formatMetadataValue(returnMethod)}`);
    const hintText = compactMetadataText(row.hint_content || metadata.hint_content || metadata.hint_text || metadata.first_hint || metadata.concept_hint);
    if (hintText) {
      parts.push(`提示內容：${hintText}`);
    }
  }

  return parts.length ? parts.join("；") : "有事件詳細資料";
}

function formatMetadata(value) {
  if (!value || typeof value !== "object") return "-";
  return JSON.stringify(value, null, 2);
}

function formatDateTime(value) {
  return value ? formatTaipeiDateTime(value) : "-";
}

function practiceUnitCell(row, unit) {
  const unitKey = typeof unit === "string" ? unit : unit?.unit_key;
  return row?.units?.[unitKey] || {
    completed_tasks: 0,
    attempted_tasks: 0,
    total_tasks: typeof unit === "object" ? Number(unit?.task_total || 0) : 0,
    status: "not_started",
    tasks: [],
  };
}

function togglePracticeUnit(row, unit) {
  const studentId = String(row?.student_id || "");
  const unitKey = String(unit?.unit_key || "");
  if (!studentId || !unitKey) return;
  if (expandedPracticeUnit.studentId === studentId && expandedPracticeUnit.unitKey === unitKey) {
    expandedPracticeUnit.studentId = "";
    expandedPracticeUnit.unitKey = "";
    expandedPracticeUnit.unitLabel = "";
    return;
  }
  expandedPracticeUnit.studentId = studentId;
  expandedPracticeUnit.unitKey = unitKey;
  expandedPracticeUnit.unitLabel = String(unit?.unit_label || unitKey);
}

function isPracticeUnitExpanded(row) {
  return expandedPracticeUnit.studentId === String(row?.student_id || "")
    && Boolean(expandedPracticeUnit.unitKey);
}

function expandedPracticeTasks(row) {
  if (!isPracticeUnitExpanded(row)) return [];
  return practiceUnitCell(row, expandedPracticeUnit.unitKey).tasks || [];
}

function formatPracticeResult(value) {
  if (value === "correct") return "正確";
  if (value === "incorrect") return "錯誤";
  if (value === "not_started") return "未開始";
  return "-";
}

function normalizedTaskSource(row) {
  return String(row?.source_type || row?.gen_source || "").trim().toLowerCase();
}

function formatTaskSource(row) {
  const source = normalizedTaskSource(row);
  if (source === "fixed" || source === "fixed_task") return "fixed";
  if (source === "openai" || source === "ai" || source === "fallback") return "ai";
  return "-";
}

function taskSourceClass(row) {
  const source = normalizedTaskSource(row);
  if (source === "fixed" || source === "fixed_task") return "fixed";
  if (source === "openai" || source === "ai" || source === "fallback") return "ai";
  return "unknown";
}

function toggleHintJson(row) {
  const key = String(row?.row_key || "");
  if (!key) return;
  expandedHintJsonKey.value = expandedHintJsonKey.value === key ? "" : key;
}

function isHintJsonExpanded(row) {
  return expandedHintJsonKey.value === String(row?.row_key || "");
}

function formatJson(value) {
  try {
    return JSON.stringify(value || {}, null, 2);
  } catch (error) {
    return String(value || "");
  }
}

function selectedTestProgressStatus(row) {
  return selectedMode.value === "posttest" ? row?.posttest_status : row?.pretest_status;
}

function selectedTestCompletedTasks(row) {
  return selectedMode.value === "posttest"
    ? Number(row?.posttest_completed_tasks || 0)
    : Number(row?.pretest_completed_tasks || 0);
}

function selectedTestTotalTasks(row) {
  return selectedMode.value === "posttest"
    ? Number(row?.posttest_total_tasks || 0)
    : Number(row?.pretest_total_tasks || 0);
}

function selectedProgressLastActivity(row) {
  if (selectedMode.value === "posttest") return row?.latest_posttest_at || null;
  if (selectedMode.value === "pretest") return row?.latest_pretest_at || null;
  return row?.latest_practice_at || null;
}

function selectedGroupFilter() {
  if (groupType.value === "control") return "control";
  if (groupType.value === "experimental") return "experimental";
  if (groupType.value === "test_data") return "test";
  return "all";
}

function buildStudentOptionsQuery() {
  const params = new URLSearchParams();
  if (className.value) params.set("class_name", className.value);
  params.set("group_filter", selectedGroupFilter());
  return params;
}

function buildFilterQuery() {
  const params = new URLSearchParams();
  const config = selectedModeConfig.value;
  params.set("activity_type", config.activityType);
  if (config.testRole) params.set("test_role", config.testRole);
  if (className.value) params.set("class_name", className.value);
  if (groupType.value) params.set("group_type", groupType.value);
  if (selectedStudentId.value) params.set("student_id", selectedStudentId.value);
  params.set("exclude_test_data", groupType.value === "control" || groupType.value === "experimental" ? "true" : "false");
  return params;
}

function buildGroupExportQuery() {
  const params = buildFilterQuery();
  params.delete("student_id");
  params.set("_ts", String(Date.now()));
  return params;
}

function buildQuery() {
  const params = buildFilterQuery();
  params.set("logs_limit", "120");
  params.set("_ts", String(Date.now()));
  return params;
}

function buildVideoRewatchQuery() {
  const params = new URLSearchParams();
  if (className.value) params.set("class_name", className.value);
  if (groupType.value) params.set("group_type", groupType.value);
  if (selectedStudentId.value) params.set("student_id", selectedStudentId.value);
  params.set("limit", "1000");
  params.set("_ts", String(Date.now()));
  return params;
}

function todayStamp() {
  const now = new Date();
  const year = String(now.getFullYear());
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}${month}${day}`;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

let crc32Table = null;

function getCrc32Table() {
  if (crc32Table) return crc32Table;
  crc32Table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let c = i;
    for (let j = 0; j < 8; j += 1) {
      c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    }
    crc32Table[i] = c >>> 0;
  }
  return crc32Table;
}

function crc32(bytes) {
  const table = getCrc32Table();
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    crc = table[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function dosDateTime(date = new Date()) {
  const year = Math.max(1980, date.getFullYear());
  return {
    date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
    time: (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2),
  };
}

async function createZipBlob(files) {
  const encoder = new TextEncoder();
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  const stamp = dosDateTime();

  for (const file of files) {
    const filenameBytes = encoder.encode(file.name);
    const dataBytes = new Uint8Array(await file.blob.arrayBuffer());
    const checksum = crc32(dataBytes);

    const localHeader = new Uint8Array(30 + filenameBytes.length);
    const localView = new DataView(localHeader.buffer);
    localView.setUint32(0, 0x04034b50, true);
    localView.setUint16(4, 20, true);
    localView.setUint16(6, 0x0800, true);
    localView.setUint16(8, 0, true);
    localView.setUint16(10, stamp.time, true);
    localView.setUint16(12, stamp.date, true);
    localView.setUint32(14, checksum, true);
    localView.setUint32(18, dataBytes.length, true);
    localView.setUint32(22, dataBytes.length, true);
    localView.setUint16(26, filenameBytes.length, true);
    localView.setUint16(28, 0, true);
    localHeader.set(filenameBytes, 30);
    localParts.push(localHeader, dataBytes);

    const centralHeader = new Uint8Array(46 + filenameBytes.length);
    const centralView = new DataView(centralHeader.buffer);
    centralView.setUint32(0, 0x02014b50, true);
    centralView.setUint16(4, 20, true);
    centralView.setUint16(6, 20, true);
    centralView.setUint16(8, 0x0800, true);
    centralView.setUint16(10, 0, true);
    centralView.setUint16(12, stamp.time, true);
    centralView.setUint16(14, stamp.date, true);
    centralView.setUint32(16, checksum, true);
    centralView.setUint32(20, dataBytes.length, true);
    centralView.setUint32(24, dataBytes.length, true);
    centralView.setUint16(28, filenameBytes.length, true);
    centralView.setUint16(30, 0, true);
    centralView.setUint16(32, 0, true);
    centralView.setUint16(34, 0, true);
    centralView.setUint16(36, 0, true);
    centralView.setUint32(38, 0, true);
    centralView.setUint32(42, offset, true);
    centralHeader.set(filenameBytes, 46);
    centralParts.push(centralHeader);

    offset += localHeader.length + dataBytes.length;
  }

  const centralOffset = offset;
  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const endRecord = new Uint8Array(22);
  const endView = new DataView(endRecord.buffer);
  endView.setUint32(0, 0x06054b50, true);
  endView.setUint16(4, 0, true);
  endView.setUint16(6, 0, true);
  endView.setUint16(8, files.length, true);
  endView.setUint16(10, files.length, true);
  endView.setUint32(12, centralSize, true);
  endView.setUint32(16, centralOffset, true);
  endView.setUint16(20, 0, true);

  return new Blob([...localParts, ...centralParts, endRecord], { type: "application/zip" });
}

function currentModeLabel() {
  return selectedModeConfig.value?.label || "平時練習";
}

function currentGroupLabel() {
  if (groupType.value === "control") return "控制組";
  if (groupType.value === "experimental") return "實驗組";
  if (groupType.value === "test_data") return "測試資料";
  return "全部組別";
}

function roundedRect(ctx, x, y, width, height, radius = 8) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

// 視覺化分布類型
function chartPngDefinitions() {
  return [
    {
      title: "概念錯誤分布",
      subtitle: "依 target_concept 統計錯誤提交次數",
      filenameKey: "concept_error",
      items: conceptChartItems.value,
      color: "#c2413b",
    },
    {
      title: "題目答對率",
      subtitle: "依題目統計答對率，數值越低代表越需要關注",
      filenameKey: "task_correct_rate",
      items: taskCorrectRateChartItems.value,
      color: "#2563a6",
      maxValue: 100,
    },
    {
      title: "錯誤類型分布",
      subtitle: "統計 sequence_error、indentation_error 等錯誤類型",
      filenameKey: "error_type",
      items: errorTypeChartItems.value,
      color: "#b45309",
    },
    {
      title: "學生平均嘗試次數",
      subtitle: "依學生統計平均每題提交次數",
      filenameKey: "student_avg_attempts",
      items: studentAttemptChartItems.value,
      color: "#146c64",
    },
  ];
}

function verticalChartWidth(chart) {
  const itemCount = Math.max(1, chart.items.length);
  return Math.max(1100, 150 + itemCount * 96);
}

function drawWrappedLabel(ctx, text, centerX, y, maxWidth) {
  const raw = String(text || "-");
  const words = raw.length > 12 ? raw.match(/.{1,12}/g) || [raw] : [raw];
  const lines = [];
  for (const word of words) {
    if (lines.length >= 2) break;
    let output = word;
    while (output.length > 1 && ctx.measureText(output).width > maxWidth) {
      output = output.slice(0, -1);
    }
    lines.push(output);
  }
  if (words.length > lines.length && lines.length) {
    lines[lines.length - 1] = `${lines[lines.length - 1].replace(/…$/, "")}…`;
  }
  lines.forEach((line, index) => {
    ctx.fillText(line, centerX, y + index * 15);
  });
}

function drawVerticalPngChart(ctx, chart, width, height) {
  const margin = 50;
  const cardInset = 22;
  const titleY = 58;
  const infoY = 112;
  const plotTop = 188;
  const plotBottom = height - 116;
  const plotLeft = 84;
  const plotRight = width - 44;
  const plotWidth = plotRight - plotLeft;
  const plotHeight = plotBottom - plotTop;
  const items = chart.items || [];
  const maxValue = Number.isFinite(chart.maxValue) && chart.maxValue > 0
    ? chart.maxValue
    : Math.max(1, ...items.map((item) => Number(item.value) || 0));

  ctx.fillStyle = "#f8fafc";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  roundedRect(ctx, cardInset, cardInset, width - cardInset * 2, height - cardInset * 2, 14);
  ctx.fill();
  ctx.strokeStyle = "#d8dee9";
  ctx.stroke();

  ctx.fillStyle = "#111827";
  ctx.font = "800 28px Arial, 'Microsoft JhengHei', sans-serif";
  ctx.fillText(chart.title, margin, titleY);
  ctx.fillStyle = "#64748b";
  ctx.font = "14px Arial, 'Microsoft JhengHei', sans-serif";
  ctx.fillText(`${chart.subtitle}，共 ${items.length} 筆`, margin, titleY + 26);
  const filterText = [
    `資料模式：${currentModeLabel()}`,
    `班級：${className.value || "全部班級"}`,
    `組別：${currentGroupLabel()}`,
    selectedStudentId.value ? `學生：${selectedStudentId.value}` : "學生：全部",
  ].join("　");
  ctx.fillText(filterText, margin, infoY);
  ctx.fillText(`產生時間：${formatTaipeiDateTime(new Date().toISOString())}`, margin, infoY + 22);

  if (!items.length) {
    ctx.fillStyle = "#64748b";
    ctx.font = "15px Arial, 'Microsoft JhengHei', sans-serif";
    ctx.fillText("目前沒有符合條件的資料可視覺化。", margin, plotTop + 36);
    return;
  }

  ctx.strokeStyle = "#dbe2ea";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#64748b";
  ctx.font = "12px Arial, 'Microsoft JhengHei', sans-serif";
  ctx.textAlign = "right";
  for (let tick = 0; tick <= 2; tick += 1) {
    const ratio = tick / 2;
    const value = maxValue * (1 - ratio);
    const y = plotTop + plotHeight * ratio;
    ctx.beginPath();
    ctx.moveTo(plotLeft, y);
    ctx.lineTo(plotRight, y);
    ctx.stroke();
    ctx.fillText(Number.isInteger(value) ? String(value) : value.toFixed(1), plotLeft - 12, y + 4);
  }

  ctx.strokeStyle = "#94a3b8";
  ctx.beginPath();
  ctx.moveTo(plotLeft, plotTop);
  ctx.lineTo(plotLeft, plotBottom);
  ctx.lineTo(plotRight, plotBottom);
  ctx.stroke();

  const slotWidth = plotWidth / items.length;
  const barWidth = Math.min(54, Math.max(24, slotWidth * 0.55));
  ctx.textAlign = "center";
  items.forEach((item, index) => {
    const value = Math.max(0, Number(item.value) || 0);
    const valueText = item.displayValue ?? String(value);
    const x = plotLeft + slotWidth * index + slotWidth / 2;
    const barHeight = value ? Math.max(4, (value / maxValue) * plotHeight) : 0;
    const y = plotBottom - barHeight;

    ctx.fillStyle = item.color || chart.color;
    roundedRect(ctx, x - barWidth / 2, y, barWidth, barHeight, 5);
    ctx.fill();

    ctx.fillStyle = "#111827";
    ctx.font = "700 12px Arial, 'Microsoft JhengHei', sans-serif";
    ctx.fillText(valueText, x, y - 10);

    ctx.fillStyle = "#475569";
    ctx.font = "12px Arial, 'Microsoft JhengHei', sans-serif";
    drawWrappedLabel(ctx, item.label, x, plotBottom + 20, Math.min(82, slotWidth - 8));
  });
}

async function downloadVisualizationPng() {
  try {
    pngBusy.value = true;
    errorMsg.value = "";
    csvMessage.value = "";

    const charts = chartPngDefinitions();
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const files = [];
    for (const chart of charts) {
      const width = verticalChartWidth(chart);
      const height = 780;
      const canvas = document.createElement("canvas");
      canvas.width = width * pixelRatio;
      canvas.height = height * pixelRatio;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("無法建立 PNG 圖表。");
      ctx.scale(pixelRatio, pixelRatio);
      drawVerticalPngChart(ctx, chart, width, height);

      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("PNG 產生失敗。");
      files.push({
        name: `parsons_${chart.filenameKey}_${todayStamp()}.png`,
        blob,
      });
    }
    const zipBlob = await createZipBlob(files);
    downloadBlob(zipBlob, `parsons_visualization_png_${todayStamp()}.zip`);
    csvMessage.value = "已下載目前篩選條件下的完整長條圖 PNG 壓縮檔。";
  } catch (error) {
    errorMsg.value = error?.message || String(error);
  } finally {
    pngBusy.value = false;
  }
}

function openStudentCsvPicker() {
  errorMsg.value = "";
  csvMessage.value = "";
  invalidRows.value = [];
  studentCsvInput.value?.click();
}

async function uploadStudentCsv(event) {
  const file = event?.target?.files?.[0];
  if (!file) return;
  try {
    csvBusy.value = true;
    errorMsg.value = "";
    csvMessage.value = "";
    invalidRows.value = [];

    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API_BASE}/api/teacher/import/users-csv`, {
      method: "POST",
      body: form,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data?.ok === false) {
      throw new Error(data?.message || `users csv import failed: ${response.status}`);
    }
    const invalidCount = Array.isArray(data.invalid_rows) ? data.invalid_rows.length : 0;
    csvMessage.value =
      `學生 CSV 匯入完成：新增 ${data.inserted_count || 0} 筆，更新 ${data.updated_count || 0} 筆，` +
      `跳過 ${data.skipped_count || 0} 筆，錯誤 ${invalidCount} 筆`;
    invalidRows.value = (data.invalid_rows || []).slice(0, 5);
    await fetchAnalysis();
  } catch (error) {
    errorMsg.value = error?.message || String(error);
  } finally {
    csvBusy.value = false;
    if (event?.target) event.target.value = "";
  }
}

async function exportGroupLearningData() {
  try {
    csvBusy.value = true;
    errorMsg.value = "";
    csvMessage.value = "";
    invalidRows.value = [];
    const response = await fetch(
      `${API_BASE}/api/teacher/export/group-learning-data.zip?${buildGroupExportQuery().toString()}`,
    );
    if (!response.ok) throw new Error(`group learning data export failed: ${response.status}`);
    const blob = await response.blob();
    downloadBlob(blob, `parsons_group_learning_data_${todayStamp()}.zip`);
    csvMessage.value = "已匯出目前班級與組別下全部學生的作答紀錄及學習歷程。";
  } catch (error) {
    errorMsg.value = error?.message || String(error);
  } finally {
    csvBusy.value = false;
  }
}

async function fetchStudentOptions() {
  const response = await fetch(`${API_BASE}/api/teacher/analytics/student-options?${buildStudentOptionsQuery().toString()}`);
  const data = await response.json().catch(() => []);
  if (!response.ok) throw new Error(data?.message || `student options failed: ${response.status}`);
  studentOptions.value = Array.isArray(data) ? data : [];
  if (
    selectedStudentId.value &&
    !studentOptions.value.some((row) => row.student_id === selectedStudentId.value)
  ) {
    selectedStudentId.value = "";
  }
}

async function fetchAnalysis() {
  try {
    loading.value = true;
    errorMsg.value = "";
    videoRewatchError.value = "";
    const [analysisResponse, videoRewatchResponse] = await Promise.all([
      fetch(`${API_BASE}/api/teacher/analysis/parsons?${buildQuery().toString()}`),
      fetch(`${API_BASE}/api/teacher/analysis/video-rewatch?${buildVideoRewatchQuery().toString()}`),
      fetchStudentOptions(),
    ]);
    const data = await analysisResponse.json().catch(() => ({}));
    if (!analysisResponse.ok || data?.ok === false) {
      throw new Error(data?.message || `analysis failed: ${analysisResponse.status}`);
    }
    const videoData = await videoRewatchResponse.json().catch(() => ({}));
    analysis.value = {
      ...emptyAnalysis(),
      ...data,
    };
    if (!videoRewatchResponse.ok || videoData?.ok === false) {
      videoRewatch.value = emptyVideoRewatch();
      videoRewatchError.value = videoData?.message || `video rewatch analysis failed: ${videoRewatchResponse.status}`;
    } else {
      videoRewatch.value = {
        ...emptyVideoRewatch(),
        ...videoData,
      };
    }
    resetPracticeExpansions();
    resetPagination();
  } catch (error) {
    errorMsg.value = error?.message || String(error);
    analysis.value = emptyAnalysis();
    videoRewatch.value = emptyVideoRewatch();
    videoRewatchError.value = "";
    studentOptions.value = [];
    resetPracticeExpansions();
    resetPagination();
  } finally {
    loading.value = false;
  }
}

function setMode(mode) {
  selectedMode.value = mode;
  selectedStudentId.value = "";
  resetPracticeExpansions();
  fetchAnalysis();
}

function onGroupChange() {
  selectedStudentId.value = "";
  resetPracticeExpansions();
  fetchAnalysis();
}

function selectStudent(studentId) {
  if (!studentId) return;
  selectedStudentId.value = studentId;
  resetPracticeExpansions();
  fetchAnalysis();
}

watch(
  [
    studentProgressRows,
    practiceUnitRows,
    practiceTaskLatestRows,
    studentRows,
    taskRows,
    conceptRows,
    studentAttempts,
    studentLogs,
    timelineEvents,
    videoRewatchSummary,
    videoRewatchRecords,
  ],
  () => clampPagination(),
);

onMounted(() => {
  fetchAnalysis();
});
</script>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 100vh;
  background: #f4f6f8;
}

.content {
  padding: 22px;
  min-width: 0;
}

.pageHeader {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.pageHeader h1 {
  margin: 0;
  color: #172033;
  font-size: 28px;
  font-weight: 900;
}

.pageHeader p {
  margin: 6px 0 0;
  color: #657085;
  font-size: 14px;
}

.headerActions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.hiddenFile {
  display: none;
}

.panel,
.kpiCard {
  background: #fff;
  border: 1px solid #d8dee9;
  border-radius: 8px;
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.04);
}

.panel {
  padding: 16px;
  margin-bottom: 14px;
}

.modeTabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.modeBtn,
.btn {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
  color: #1f2937;
  font-weight: 800;
  cursor: pointer;
}

.modeBtn {
  padding: 9px 14px;
}

.modeBtn.active,
.btn.primary {
  background: #146c64;
  border-color: #146c64;
  color: #fff;
}

.btn {
  padding: 10px 14px;
}

.btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.filters {
  display: grid;
  grid-template-columns: repeat(3, minmax(180px, 1fr));
  gap: 12px;
}

.field {
  display: grid;
  gap: 6px;
  color: #334155;
  font-size: 13px;
  font-weight: 800;
}

.input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 10px 11px;
  color: #172033;
  background: #fff;
  outline: none;
}

.message {
  margin: 12px 0 0;
  padding: 10px 12px;
  border-radius: 8px;
  font-weight: 800;
}

.message.error {
  color: #991b1b;
  background: #fee2e2;
  border: 1px solid #fecaca;
}

.message.ok {
  color: #166534;
  background: #dcfce7;
  border: 1px solid #bbf7d0;
}

.invalidRows {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  background: #fff7ed;
  color: #9a3412;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.note {
  margin: 12px 0 0;
  color: #657085;
  font-size: 13px;
}

.note.testModeNotice {
  padding: 11px 13px;
  border: 1px solid #fdba74;
  border-radius: 6px;
  color: #9a3412;
  background: #fff7ed;
  font-size: 14px;
  font-weight: 800;
}

.viewTabs {
  display: inline-flex;
  margin-top: 14px;
  padding: 3px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f1f5f9;
}

.viewTab {
  min-width: 112px;
  border: 0;
  border-radius: 6px;
  padding: 8px 13px;
  color: #475569;
  background: transparent;
  cursor: pointer;
  font-weight: 800;
}

.viewTab.active {
  color: #fff;
  background: #146c64;
}

.visualTestNotice {
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid #fdba74;
  border-radius: 6px;
  color: #9a3412;
  background: #fff7ed;
  font-size: 14px;
  font-weight: 800;
}

.chartExportPanel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 14px;
}

.chartExportPanel h2 {
  margin: 0;
  color: #172033;
  font-size: 18px;
  font-weight: 900;
}

.chartExportPanel p {
  margin: 5px 0 0;
  color: #64748b;
  font-size: 13px;
}

.chartExportActions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.overviewGrid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 14px;
  margin-bottom: 14px;
}

.overviewPanel {
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.04);
}

.overviewCards {
  display: grid;
  grid-template-columns: repeat(3, minmax(130px, 1fr));
  gap: 10px;
}

.testOverviewPair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.testOverviewCard {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  background: #ffffff;
}

.testOverviewTitle {
  margin-bottom: 10px;
  color: #1e3a8a;
  font-size: 14px;
  font-weight: 900;
}

.compactCards {
  grid-template-columns: repeat(3, minmax(90px, 1fr));
}

.overviewCard {
  min-width: 0;
  padding: 13px 14px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
}

.overviewCard.subtle {
  background: #f5fbf8;
}

.overviewLabel {
  color: #64748b;
  font-size: 12px;
  font-weight: 900;
}

.overviewValue {
  margin-top: 8px;
  color: #111827;
  font-size: 24px;
  font-weight: 900;
}

.compactHeader {
  margin-bottom: 10px;
}

.progress-table {
  min-width: 980px;
}

.practice-matrix-table,
.practice-latest-table {
  min-width: 1120px;
}

.matrixCellBtn {
  display: inline-grid;
  min-width: 86px;
  gap: 2px;
  justify-items: center;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 7px 9px;
  color: #334155;
  background: #f8fafc;
  cursor: pointer;
  font-weight: 900;
}

.matrixCellBtn.done {
  color: #166534;
  border-color: #86efac;
  background: #dcfce7;
}

.matrixCellBtn.doing {
  color: #92400e;
  border-color: #fcd34d;
  background: #fef3c7;
}

.matrixCellBtn.idle {
  color: #475569;
  border-color: #cbd5e1;
  background: #f1f5f9;
}

.matrixCellBtn:hover {
  border-color: #146c64;
  box-shadow: 0 0 0 3px rgba(20, 108, 100, 0.1);
}

.matrixExpandRow td,
.jsonExpandRow td {
  background: #f8fafc;
}

.matrixExpandPanel {
  padding: 10px;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  background: #ffffff;
}

.matrixExpandTitle {
  margin-bottom: 8px;
  color: #1e3a8a;
  font-size: 13px;
  font-weight: 900;
}

.compact-inner-table {
  min-width: 720px;
}

.inlineActionBtn {
  border: 1px solid #cbd5e1;
  border-radius: 999px;
  padding: 5px 10px;
  color: #146c64;
  background: #ffffff;
  cursor: pointer;
  font-size: 12px;
  font-weight: 900;
}

.inlineActionBtn:hover {
  border-color: #146c64;
  background: #f0fdfa;
}

.jsonPreview {
  max-height: 320px;
  overflow: auto;
  margin: 0;
  padding: 12px;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  color: #172033;
  background: #ffffff;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 58px;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 900;
  white-space: nowrap;
}

.status-badge.done {
  color: #166534;
  background: #dcfce7;
  border: 1px solid #86efac;
}

.status-badge.doing {
  color: #92400e;
  background: #fef3c7;
  border: 1px solid #fcd34d;
}

.status-badge.idle {
  color: #475569;
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
}

.source-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 48px;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 900;
  border: 1px solid #cbd5e1;
  background: #f8fafc;
  color: #475569;
  white-space: nowrap;
}

.source-badge.fixed {
  color: #1d4ed8;
  border-color: #bfdbfe;
  background: #dbeafe;
}

.source-badge.ai {
  color: #854d0e;
  border-color: #fde68a;
  background: #fef3c7;
}

.progress-sub {
  margin-top: 4px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.35;
}

.paginationControls {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 10px;
  color: #475569;
  font-size: 13px;
  font-weight: 800;
}

.compactPagination {
  justify-content: center;
}

.pageBtn {
  border: 1px solid #cbd5e1;
  border-radius: 999px;
  padding: 6px 12px;
  color: #1f2937;
  background: #fff;
  cursor: pointer;
  font-weight: 800;
}

.pageBtn:hover:not(:disabled) {
  border-color: #146c64;
  color: #146c64;
  background: #f0fdfa;
}

.pageBtn:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.kpiGrid {
  display: grid;
  grid-template-columns: repeat(6, minmax(130px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.kpiCard {
  padding: 14px;
}

.kpiLabel {
  color: #64748b;
  font-size: 12px;
  font-weight: 900;
}

.kpiValue {
  margin-top: 8px;
  color: #111827;
  font-size: 24px;
  font-weight: 900;
}

.analysisGrid,
.studentDetailGrid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 14px;
}

.analysisGrid {
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.8fr);
}

.visualizationGrid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

.visualizationGrid .panel {
  min-width: 0;
  margin-bottom: 0;
}

.chartPanel {
  min-height: 320px;
}

.timelinePanel {
  min-width: 0;
}

.timelineList {
  position: relative;
  display: grid;
  margin: 0;
  padding: 4px 0;
  gap: 0;
  list-style: none;
}

.timelineItem {
  position: relative;
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 10px;
  min-width: 0;
  padding-bottom: 18px;
}

.timelineItem:not(:last-child)::before {
  position: absolute;
  top: 15px;
  bottom: 0;
  left: 6px;
  width: 2px;
  background: #cbd5e1;
  content: "";
}

.timelineMarker {
  position: relative;
  z-index: 1;
  width: 12px;
  height: 12px;
  margin-top: 5px;
  border: 3px solid #d1fae5;
  border-radius: 50%;
  box-sizing: border-box;
  background: #146c64;
}

.timelineContent {
  min-width: 0;
  padding-bottom: 2px;
}

.timelineHeader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.timelineHeader time {
  color: #64748b;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.timelineContext {
  display: flex;
  margin-top: 4px;
  gap: 6px 14px;
  flex-wrap: wrap;
  color: #475569;
  font-size: 12px;
}

.timelineSummary {
  margin-top: 5px;
  color: #334155;
  font-size: 13px;
  line-height: 1.5;
}

.visualizationEmpty {
  display: grid;
  min-height: 160px;
  place-items: center;
  color: #64748b;
  font-size: 14px;
}

.detailBlock {
  min-width: 0;
}

.detailBlock h3 {
  margin: 4px 0 10px;
  color: #172033;
  font-size: 15px;
  font-weight: 900;
}

.sectionHeader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.sectionHeader h2 {
  margin: 0;
  color: #172033;
  font-size: 17px;
  font-weight: 900;
}

.sectionHeader span {
  color: #64748b;
  font-size: 13px;
  font-weight: 800;
}

.tableWrap {
  overflow-x: auto;
}

.analytics-table-wrapper {
  width: 100%;
  overflow-x: auto;
}

.dataTable {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.analytics-table {
  width: 100%;
  min-width: 980px;
  table-layout: fixed;
  border-collapse: collapse;
}

.student-attempt-table,
.student-log-table {
  min-width: 1280px;
}

.dataTable th,
.dataTable td {
  border-bottom: 1px solid #e5e7eb;
  padding: 9px 10px;
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}

.analytics-table th,
.analytics-table td {
  padding: 10px 12px;
  vertical-align: top;
  text-align: left;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
  line-height: 1.5;
  font-size: 14px;
}

.dataTable th {
  color: #475569;
  background: #f8fafc;
  font-size: 12px;
  font-weight: 900;
}

.analytics-table th {
  font-weight: 600;
}

.analytics-table td.compact,
.analytics-table th.compact {
  white-space: nowrap;
}

.analytics-table td.center,
.analytics-table th.center {
  text-align: center;
}

.dataTable tbody tr {
  cursor: default;
}

.dataTable tbody tr.selected,
.dataTable tbody tr:hover {
  background: #ecfdf5;
}

.titleCell {
  max-width: 280px;
  white-space: normal;
}

.cell-task-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.5;
  max-height: 3em;
}

.error-badge,
.concept-badge,
.event-badge {
  display: inline-block;
  margin: 2px 4px 2px 0;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 12px;
  background: #eef2f7;
  white-space: nowrap;
}

.concept-badge {
  background: #ecfdf5;
  color: #166534;
}

.event-badge {
  background: #eff6ff;
  color: #1d4ed8;
}

.video-event-time-cell {
  min-width: 118px;
  white-space: nowrap;
}

.video-event-time-cell span {
  display: block;
  line-height: 1.35;
}

.slot-list {
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.metadata-summary {
  color: #334155;
  line-height: 1.5;
}

.metadata-details {
  margin-top: 6px;
}

.metadata-details summary {
  width: fit-content;
  color: #166534;
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
}

.metadata-details[open] summary {
  margin-bottom: 6px;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  white-space: normal;
  word-break: break-all;
}

.task-id-cell {
  white-space: normal;
  word-break: break-all;
  overflow-wrap: anywhere;
}

.empty {
  color: #64748b;
  text-align: center;
}

.logTable pre {
  margin: 0;
  max-width: 360px;
  max-height: 160px;
  overflow: auto;
  color: #1f2937;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  white-space: pre-wrap;
}

@media (max-width: 1280px) {
  .overviewCards {
    grid-template-columns: repeat(2, minmax(160px, 1fr));
  }

  .testOverviewPair {
    grid-template-columns: 1fr;
  }

  .kpiGrid {
    grid-template-columns: repeat(3, minmax(160px, 1fr));
  }

  .analysisGrid {
    grid-template-columns: 1fr;
  }

  .visualizationGrid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .content {
    padding: 14px;
  }

  .pageHeader,
  .sectionHeader {
    align-items: stretch;
    flex-direction: column;
  }

  .headerActions {
    justify-content: flex-start;
  }

  .filters,
  .overviewCards,
  .testOverviewPair,
  .kpiGrid {
    grid-template-columns: 1fr;
  }
}
</style>
