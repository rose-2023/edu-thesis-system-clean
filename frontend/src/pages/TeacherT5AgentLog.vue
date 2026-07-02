<template>
  <div class="t5-layout">
    <!-- ===== Sidebar ===== -->
    <TeacherSidebar active="agentlog" />

    <!-- ===== Main ===== -->
    <main class="main">
      <h1 class="title">AI 代理 Parsons 題目生成紀錄</h1>

      <!-- [新增] 後測開放控制（僅新增按鈕，不影響既有功能） -->
      <div class="posttest-bar">
        <div class="posttest-left">
          <span class="posttest-label">後測狀態：</span>
          <span class="posttest-status" :class="{ open: postOpen === true, closed: postOpen === false }">
            {{ postOpen === null ? "未讀取" : (postOpen ? "已開放" : "未開放") }}
          </span>
          <span class="posttest-hint">（依單元控制：{{ testCycleId }}）</span>
        </div>

        <div class="posttest-actions">
          <button class="btn primary" :disabled="!testCycleId || postOpenLoading" @click="setPostOpen(true)">
            後測發布
          </button>
          <button class="btn warn" :disabled="!testCycleId || postOpenLoading" @click="setPostOpen(false)">
            後測取消發布
          </button>
        </div>
      </div>
      <section class="grid">
        <!-- A -->
        <div class="card">
          <h2 class="card-title">A. 影片與字幕資訊</h2>

          <div class="row2">
            <div class="field">
              <label>單元</label>
              <select v-model="selectedUnit" :disabled="loading.units">
                <option value="">請選擇</option>
                <option v-for="u in units" :key="u.id" :value="u.id">{{ u.name }}</option>
              </select>
            </div>

            <div class="field">
              <label>影片標題</label>
              <select v-model="selectedVideo" :disabled="!selectedUnit || loading.videos">
                <option value="">請選擇</option>
                <option v-for="v in videos" :key="v.id" :value="v.id">{{ v.title }}</option>
              </select>
            </div>
          </div>

          <div class="row2 info">
            <div class="kv">
              <span class="k">影片狀態：</span>
              <span class="chip" :class="videoInfo.enabled ? 'chip-ok' : 'chip-off'">
                {{ videoInfo.enabled ? "啟用" : "停用" }}
              </span>
            </div>

            <div class="kv">
              <span class="k">字幕：</span>
              <span class="chip chip-ok" v-if="videoInfo.subtitle_uploaded">已上傳</span>
              <span class="chip chip-off" v-else>未上傳</span>

              <span class="chip chip-ok" v-if="videoInfo.subtitle_verified">已校正</span>
              <span class="chip chip-off" v-else>未校正</span>
            </div>
          </div>

          <div class="row2 info">
            <div class="kv"><span class="k">來源：</span>{{ videoInfo.source || "—" }}</div>
            <div class="kv">
              <span class="k">字幕版本：</span>
              <select v-model="selectedSubtitleVersion" :disabled="!videoInfo.subtitle_versions?.length">
                <option v-for="sv in videoInfo.subtitle_versions" :key="sv" :value="sv">{{ sv }}</option>
              </select>
            </div>
          </div>

          <div class="row2 info">
            <div class="kv"><span class="k">影片時間：</span>{{ videoInfo.duration || "—" }}</div>
            <div class="actions">
              <button class="btn warn" @click="deleteSelectedVideo" :disabled="!selectedVideo">刪除此影片</button>
              <button class="btn" @click="goVideoManage" :disabled="!selectedVideo">前往影片管理</button>
              <button class="btn" @click="goSubtitleCheck" :disabled="!selectedVideo">前往字幕校正</button>
            </div>
          </div>

          <p class="hint err" v-if="err.a">{{ err.a }}</p>
        </div>

        <!-- B -->
        <div class="card">
          <h2 class="card-title">B. AI 代理任務流程</h2>
          <div class="flow">
            <div class="step">
              <div class="num">1</div>
              <div class="box">
                <div class="icon">📄</div>
                <div class="txt">Text/Code</div>
              </div>
            </div>
            <div class="arrow">→</div>
            <div class="step">
              <div class="num">2</div>
              <div class="box">
                <div class="icon">💡</div>
                <div class="txt">Key Concepts</div>
              </div>
            </div>
            <div class="arrow">→</div>
            <div class="step">
              <div class="num">3</div>
              <div class="box">
                <div class="icon">🧩</div>
                <div class="txt">Parsons Puzzle</div>
              </div>
            </div>
          </div>

          <div class="meta">
            <div>使用模型：OpenAI</div>
            <div>執行方式：AI Agent 自動生成</div>
          </div>
        </div>

        <!-- C -->
        <div class="card">
          <h2 class="card-title">C. 題目設定</h2>
          <div class="kvcol">
            <div class="kv"><span class="k">類型：</span>Parsons 程式除錯題</div>
            <div class="kv"><span class="k">數量：</span>1 題 / 影片</div>
            <div class="kv"><span class="k">語言：</span>Python</div>
            <div class="kv"><span class="k">依據：</span>影片字幕檔生成</div>
          </div>
        </div>

        <!-- D -->
        <div class="card">
          <h2 class="card-title">D. 題目內容預覽與審核</h2>

          <!-- filters -->
          <div class="d-toolbar">
            <div class="d-filter">
              <label>狀態：</label>
              <select v-model="filterStatus" :disabled="!selectedVideo || loading.questions">
                <option value="all">全部</option>
                <option value="pending">待審核</option>
                <option value="published">已上架</option>
                <option value="rejected">已退回</option>
              </select>
            </div>

            <div class="d-filter">
              <label>排序：</label>
              <select v-model="sortOrder" :disabled="!selectedVideo || loading.questions">
                <option value="newest">生成時間（新→舊）</option>
                <option value="oldest">生成時間（舊→新）</option>
              </select>
            </div>

            <!-- 穩定模式開關 -->
            <div class="stable-toggle" :class="{ 'stable-on': stableMode }" @click="stableMode = !stableMode" title="穩定模式：降低隨機性，讓 AI 生題更穩定（適合課前備課）">
              <div class="stable-knob"></div>
              <span class="stable-label">
                {{ stableMode ? '⚙️ 穩定模式 ON' : '🎲 創意模式 ON' }}
              </span>
            </div>
          </div>

          <!-- states -->
          <div class="d-state" v-if="!selectedVideo">
            請先於 A 區塊選擇影片後，再查看題目版本。
          </div>

          <div class="d-state" v-else-if="loading.questions">
            ⏳ 載入題目中，請稍候…
          </div>

          <div class="d-state err" v-else-if="err.d">
            ⚠️ 題目載入失敗<br />
            原因：{{ err.d }}<br />
            <button class="btn mini" @click="fetchQuestions()">重新嘗試</button>
          </div>

          <div class="d-state" v-else-if="questions.length === 0">
            📭 尚無題目版本<br />
            請點擊「建立題目」以建立 AI 題目。<br />
            <button class="btn mini" @click="regenerate()">建立題目</button>
          </div>

          <!-- table -->
          <div v-else class="table-wrap">
            <table class="t">
              <thead>
                <tr>
                  <th style="width: 90px;">題型</th>
                  <th style="width: 110px;">題目代號</th>
                  <th style="width: 80px;">來源</th>
                  <th style="width: 170px;">建立時間</th>
                  <th style="width: 110px;">狀態</th>
                  <th style="width: 90px;">學生可見</th>
                  <th style="width: 250px;">操作</th>
                </tr>
              </thead>
              <tbody>
                  <tr v-for="q in questions" :key="q.task_id" :class="{ 'fixed-row': getSourceType(q) === 'fixed' }">
                    <!-- 題型 -->
                    <td>
                      <span v-if="getSourceType(q) === 'fixed'" class="pin-badge">固定題</span>
                      <span v-else class="ai-badge">AI題</span>
                    </td>

                    <!-- 題目代號 -->
                    <td class="mono">
                      {{ getTaskCode(q) }}
                      <div class="sub" v-if="q.parent_version">（由 {{ q.parent_version }} 重新生成）</div>
                    </td>

                    <!-- 來源 -->
                    <td class="mono">
                      {{ getSourceType(q) }}
                    </td>

                    <!-- 建立時間 -->
                    <td class="mono">
                      {{ fmtTime(q.created_at) }}
                    </td>

                    <!-- 狀態 -->
                    <td>
                      <span class="dot" :class="dotClass(getStatus(q))"></span>
                      {{ getStatusZh(q) }}
                    </td>

                    <!-- 學生可見 -->
                    <td>
                      <span v-if="isStudentVisible(q)" class="visible-yes">是</span>
                      <span v-else class="visible-no">否</span>
                    </td>

                    <!-- 操作 -->
                    <td>
                      <div class="btns">
                        <button class="pillBtn preview" @click="openPreview(q)">預覽</button>

                        <button
                          v-if="!isStudentVisible(q)"
                          class="pillBtn publish"
                          @click="publish(q)"
                        >
                          發布
                        </button>

                        <button
                          v-else
                          class="pillBtn unpub"
                          @click="unpublish(q)"
                        >
                          取消發布
                        </button>

                        <button
                          v-if="getSourceType(q) === 'fixed'"
                          class="pillBtn edit"
                          @click="openPreview(q)"
                        >
                          編輯
                        </button>

                        <button
                          v-if="getSourceType(q) === 'fixed'"
                          class="pillBtn"
                          @click="alignFixedTask(q)"
                          title="用 AI 將固定題每個 slot 對齊到影片字幕時間"
                        >
                          對齊字幕
                        </button>

                        <button
                          class="pillBtn"
                          @click="alignConceptTask(q)"
                          title="由系統判斷章節（保留舊格式，供老師確認）"
                        >
                          系統判斷
                        </button>

                        <button
                          v-if="getSourceType(q) !== 'fixed'"
                          class="pillBtn regen"
                          @click="regenerate(q)"
                        >
                          重新生成
                        </button>
                      </div>
                    </td>
                  </tr>
                </tbody>
            </table>
          </div>

          <div class="hint" v-if="conceptDraftModal.loading" style="margin-top:8px; color:#0d6efd;">
            ⏳ 系統正在判斷章節，請稍候...
          </div>

          <p class="hint err" v-if="err.e">{{ err.e }}</p>
        </div>

        <div class="card card-wide">
          <h2 class="card-title">E. 老師手動秒數校正</h2>

          <div class="row2">
            <div class="field">
              <label>題目版本（固定題 / AI 題）</label>
              <select v-model="selectedFixedTaskId" :disabled="!selectedVideo || !editableQuestions.length">
                <option value="">請選擇題目版本</option>
                <option v-for="q in editableQuestions" :key="q.task_id" :value="q.task_id">
                  {{ getSourceType(q) === "fixed" ? "固定題" : "AI題" }}｜{{ getTaskCode(q) }}｜{{ fmtTime(q.created_at) }}
                </option>
              </select>
            </div>
            <div class="actions">
              <button class="btn" :disabled="!selectedFixedTaskId || pageSegmentEditorLoading" @click="loadPageSegmentEditor">載入秒數校正資料</button>
            </div>
          </div>

          <div class="d-state" v-if="!selectedVideo">
            請先選擇影片。
          </div>
          <div class="d-state" v-else-if="!editableQuestions.length">
            這支影片目前沒有可調整題目，請先建立題目版本（固定題或 AI 題）。
          </div>
          <div class="d-state" v-else-if="pageSegmentEditorLoading">
            ⏳ 載入題目秒數校正資料中…
          </div>

          <div v-else-if="segmentEditorRows.length" class="pvBox">
            <div class="pvMetaRow">
              <div class="pill">題目：{{ segmentEditorTaskId || "—" }}</div>
              <div class="pill">字幕句數：{{ subtitleSegments.length }}</div>
              <div class="pill">優先序：teacher_segment_map > ai_segment_map > subtitle_ir</div>
            </div>

            <div class="segDebug" v-if="segmentEditorDebug">
              <div class="segDebugTitle">對齊 Debug（最近一次學生提交）</div>
              <div class="pvMetaRow">
                <div class="pill">segment_source：{{ segmentEditorDebug.segment_source || "—" }}</div>
                <div class="pill">wrong_slot：{{ segmentEditorDebug.wrong_index ?? "—" }}</div>
                <div class="pill">回看片段：{{ fmtSecRange(segmentEditorDebug.jump_start, segmentEditorDebug.jump_end) }}</div>
                <div class="pill">同概念相鄰合併：{{ segmentEditorDebug.merged_adjacent_same_concept ? "yes" : "no" }}</div>
              </div>
              <div class="hint" v-if="segmentEditorDebug.merged_adjacent_same_concept">
                合併概念：{{ segmentEditorDebug?.merged_meta?.concept || "—" }}；
                合併 slots：{{ formatMergedSlots(segmentEditorDebug?.merged_meta?.group) }}
              </div>
            </div>

            <div class="segTableWrap">
              <table class="segTable">
                <thead>
                  <tr>
                    <th>概念標籤</th>
                    <th>程式碼區段與對應 AI 秒數</th>
                    <th>老師 start</th>
                    <th>老師 end</th>
                    <th>目前播放窗字幕參考</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(ch, idx) in previewData?.teacher_concept_chapters" :key="`page-seg-${idx}`">
                    <td><span class="segTag">{{ ch.concept || "unknown" }}</span></td>
                    <td class="mono segCode">
                      <div v-for="b in _getBlocksForConcept(ch.concept)" :key="b.id" style="margin-bottom: 6px; padding: 4px; background: #f8f9fa; border-radius: 4px; border: 1px solid #e9ecef;">
                        <div style="font-weight: 500; color: #2c3e50; line-height: 1.4;">
                          <span style="color: #6c757d; font-size: 0.85em; margin-right: 4px;">[{{ b.index + 1 }}]</span>
                          {{ b.code }}
                          <span v-if="b.meaning" style="color: #198754; font-size: 0.9em; margin-left: 6px;">// {{ b.meaning }}</span>
                        </div>
                        <div style="font-size: 0.85em; color: #6c757d; margin-top: 2px;" v-if="b.aiStart !== undefined">
                          └ AI 秒數：{{ fmtSecRange(b.aiStart, b.aiEnd) }}
                        </div>
                      </div>
                      <div v-if="!_getBlocksForConcept(ch.concept)?.length" class="hint">（無對應程式碼）</div>
                    </td>
                    <td><input class="segInput" type="text" :value="_secToMMSS(ch.start)" @input="e => { const sec = _MMSSToSec(e.target.value); if (sec !== null) ch.start = sec; }" placeholder="mm:ss" /></td>
                    <td><input class="segInput" type="text" :value="_secToMMSS(ch.end)" @input="e => { const sec = _MMSSToSec(e.target.value); if (sec !== null) ch.end = sec; }" placeholder="mm:ss" /></td>
                    <td class="segCtx">
                      <div class="segSourceHint">
                        對齊範圍：{{ fmtSecRange(ch.start, ch.end) }}
                      </div>
                      <div class="segWarn" v-if="!ch.start || !ch.end || ch.start >= ch.end">
                        ⚠ 老師區間無效（請確認 end > start，且皆為有效數字）
                      </div>
                      <div class="segSubList">
                        <div class="segSubItem" v-for="(line, i) in _collectSubtitleOverlapSegments(ch.start, ch.end)" :key="`sub-${idx}-${i}`">
                          [#{{ line.id ?? i + 1 }} | {{ _secToTC(line.start) }} - {{ _secToTC(line.end) }}] {{ line.text }}
                        </div>
                      </div>
                      <div class="segCtxLine hint" v-if="!_collectSubtitleOverlapSegments(ch.start, ch.end).length">（此格目前區間內無字幕句）</div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div class="segActions">
              <button class="btn primary" @click="saveConceptOverride" :disabled="segmentEditorSaving || !segmentEditorTaskId">儲存老師概念校正秒數</button>
            </div>
          </div>

          <div class="hint" v-else>
            請先選擇題目版本並載入秒數校正資料。
          </div>
        </div>
      </section>

    <!-- ===== Concept Draft Modal ===== -->
    <div v-if="conceptDraftModal.open" class="modal-mask" @click.self="conceptDraftModal.open = false">
      <div class="modal">
        <div class="modal-head">
          <div class="modal-title">【概念章節草稿 + AI 補充建議】</div>
          <button class="x" @click="conceptDraftModal.open = false">✕</button>
        </div>
        <div class="modal-body concept-modal-body" style="background:#fff;">
          <p style="margin-bottom: 10px; color:#555; line-height:1.5;">
            左側是系統草稿章節，只能由老師調整名稱、時間、刪除或合併。右側只保留 AI 的名稱修正建議，仍然只能透過「套用」按鈕帶回左側草稿，不能直接覆蓋草稿內容。
          </p>

          <div class="concept-meta-bar">
            <div class="concept-meta-block">
              <div class="concept-meta-title">設定教學大區間</div>
              <div class="concept-meta-row">
                <input
                  type="text"
                  v-model="conceptDraftModal.teachingRangeStart"
                  placeholder="開始時間 (分:秒)，例如 01:23"
                  class="concept-start-input"
                />
                <input
                  type="text"
                  v-model="conceptDraftModal.teachingRangeEnd"
                  placeholder="結束時間 (分:秒)，例如 02:48"
                  class="concept-start-input"
                />
                <button class="btn secondary" @click="applyTeachingRange">[設定教學大區間]</button>
              </div>
            </div>
            <div class="concept-meta-note">
              全片字幕範圍：{{ _formatRangeBounds(_getSubtitleRangeBounds()) }}<br />
              教學大區間：{{ _formatRangeBounds(_getTeachingRangeBounds()) }}<br />
              教學大區間建議：{{ _formatRangeBounds(conceptDraftModal.teachingRangeRecommendedRange || _getTeachingRangeBounds()) }}<br />
              目前設定：{{ conceptDraftModal.teachingRangeStart && conceptDraftModal.teachingRangeEnd ? `${conceptDraftModal.teachingRangeStart} – ${conceptDraftModal.teachingRangeEnd}` : "自動判定" }}<br />
              草稿只會在教學大區間內生成；若未設定，系統才會回退到自動判定。
            </div>
          </div>

          <div v-if="conceptDraftModal.loading" class="hint" style="margin:10px 0; color:#0d6efd; font-weight:600;">
            ⏳ 系統正在判斷章節，請稍候...
          </div>

          <div v-if="conceptDraftModal.statusText" class="hint" style="margin:6px 0 12px; color:#495057; white-space:pre-wrap;">
            {{ conceptDraftModal.statusText }}
          </div>

          <div v-if="conceptDraftModal.teachingRangeMode" class="hint" style="margin:0 0 12px; color:#0f5132; background:#ecfdf3; border:1px solid #bbf7d0; padding:8px 10px; border-radius:10px; white-space:pre-wrap;">
            {{ conceptDraftModal.teachingRangeMode === 'teacher_range_only' ? '本次重算模式：teacher_range_only，僅依老師設定的教學區間生成章節。' : `本次重算模式：${conceptDraftModal.teachingRangeMode}` }}
          </div>

          <div v-if="conceptDraftModal.teachingRangeWarning" class="hint" style="margin:6px 0 12px; color:#92400e; background:#fffbeb; border:1px solid #f59e0b; padding:8px 10px; border-radius:10px; white-space:pre-wrap;">
            {{ _warningText(conceptDraftModal.teachingRangeWarning) || '本次教學區間無效，下方仍顯示上一版章節草稿' }}
          </div>

          <div v-if="conceptDraftModal.debugText" class="hint" style="margin:0 0 12px; color:#6c757d; white-space:pre-wrap; font-size:12px;">
            {{ conceptDraftModal.debugText }}
          </div>

          <div v-if="conceptDraftModal.error" class="hint err" style="margin:10px 0; white-space:pre-wrap;">
            {{ conceptDraftModal.error }}
          </div>

          <div v-if="conceptDraftModal.aiSuggestions.rename_suggestions.length > 0" class="hint" style="margin:8px 0 12px; color:#0f5132; background:#ecfdf3; border:1px solid #bbf7d0; padding:8px 10px; border-radius:10px;">
            仍有 AI 建議可參考，請以左側草稿為主，逐筆按鈕套用。
          </div>

          <div v-if="conceptDraftModal.loading && !conceptDraftModal.chapters.length" style="padding:14px 0; color:#6c757d;">
            目前尚未載入草稿章節。
          </div>

          <div class="concept-shell">
            <section class="concept-left">
              <details v-if="Array.isArray(_getConceptAlignedSummarySegments()) && _getConceptAlignedSummarySegments().length" class="subtitle-fold">
                <summary>字幕摘要（依概念章節對齊）</summary>
                <div class="subtitle-fold-body">
                  <div v-for="(seg, sIdx) in _getConceptAlignedSummarySegments()" :key="`subtitle-${sIdx}`" class="subtitle-row" :class="{ 'subtitle-row-active': isSubtitleSegmentHighlighted(seg) }">
                    <span class="subtitle-time">[{{ _secToTC(seg.start) }} - {{ _secToTC(seg.end) }}]</span>
                    <span class="subtitle-text">{{ seg.text }}</span>
                  </div>
                </div>
              </details>

              <div v-if="selectedConceptDraftChapter" class="draft-focus-bar">
                <div class="draft-focus-title">目前選取章節</div>
                <div class="draft-focus-text">
                  {{ selectedConceptDraftChapter.cell_id || '—' }}｜{{ selectedConceptDraftChapter.concept_label || '—' }}
                  <span v-if="selectedConceptDraftChapter.concept_tag" class="draft-focus-tag">{{ selectedConceptDraftChapter.concept_tag }}</span>
                    <span class="draft-focus-range">{{ _formatChapterTimeRelativeRange(selectedConceptDraftChapter) }}</span>
                </div>
                <div class="draft-focus-preview" v-if="_selectedDraftChapterSubtitleText()">
                  {{ _selectedDraftChapterSubtitleText() }}
                </div>
              </div>

              <details v-if="selectedConceptDraftChapter && _selectedChapterCodeRows().length" class="subtitle-fold" style="margin-top:10px;">
                <summary>目前章節對應程式碼（{{ _selectedChapterCodeRows().length }} 段）</summary>
                <div class="subtitle-fold-body">
                  <div
                    v-for="(row, ridx) in _selectedChapterCodeRows()"
                    :key="`chapter-code-${ridx}`"
                    class="subtitle-row"
                    style="display:block; padding:8px 10px;"
                  >
                    <div class="mono" style="font-size:12px; color:#6c757d; margin-bottom:4px;">
                      slot {{ row.slot_index ?? row.slot ?? ridx }}
                      <span v-if="row.chapter_start !== undefined && row.chapter_end !== undefined">
                        ｜章節時間 {{ _secToTC(row.chapter_start) }} - {{ _secToTC(row.chapter_end) }}
                      </span>
                    </div>
                    <div class="mono" style="white-space:pre-wrap; line-height:1.45;">{{ row.code || "（無程式碼）" }}</div>
                    <div v-if="row.semantic_zh" style="margin-top:4px; color:#198754; font-size:12px;">語意：{{ row.semantic_zh }}</div>
                  </div>
                </div>
              </details>

              <table v-if="conceptDraftModal.chapters.length" class="pv-table draft-table" style="width:100%; table-layout:fixed;">
                <thead>
                  <tr>
                    <th style="width:56px;">Cell</th>
                    <th>概念名稱</th>
                    <th style="width:150px;">時間</th>
                    <th style="width:150px;">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(ch, idx) in conceptDraftModal.chapters"
                    :key="idx"
                    class="draft-row"
                    :class="{ 'draft-row-selected': selectedChapterIndex === idx }"
                    @click="selectConceptDraftChapter(idx)"
                  >
                    <td class="mono">{{ ch.cell_id || (idx + 1) }}</td>
                    <td>
                      <input type="text" v-model="ch.concept_label" class="concept-text-input" />
                      <div class="concept-row-meta">
                        <div v-if="ch.concept_tag"><strong>tag：</strong>{{ ch.concept_tag }}</div>
                        <div v-if="ch.source || ch.chapter_source">
                          <strong>來源：</strong>
                          <!-- [新增] AI 自動補入用醒目標籤，避免老師誤以為是後端正式章節 -->
                          <span
                            v-if="(ch.source || ch.chapter_source || '').includes('ai_auto_fill')"
                            style="display:inline-block; font-size:11px; font-weight:700; padding:2px 7px; border-radius:999px; background:#fef9c3; color:#854d0e; border:1px solid #fde047;"
                          >🤖 AI 自動補入（請確認時間）</span>
                          <span v-else>{{ _chapterSourceLabel(ch.source || ch.chapter_source) }}</span>
                        </div>
                      </div>
                    </td>
                    <td>
                      <div style="display:flex; gap:6px; flex-direction:column;">
                        <input
                          type="text"
                          :value="_chapterTimeInputDisplay(idx, 'start', ch.start)"
                          placeholder="mm:ss"
                          class="concept-time-input"
                          @click.stop
                          @focus.stop="_beginChapterTimeEdit(idx, 'start', ch.start)"
                          @input.stop="_updateChapterTimeEdit(idx, 'start', $event.target.value)"
                          @blur.stop="_commitChapterTimeEdit(idx, 'start')"
                          @keydown.enter.stop.prevent="_commitChapterTimeEdit(idx, 'start')"
                        />
                        <input
                          type="text"
                          :value="_chapterTimeInputDisplay(idx, 'end', ch.end)"
                          placeholder="mm:ss"
                          class="concept-time-input"
                          @click.stop
                          @focus.stop="_beginChapterTimeEdit(idx, 'end', ch.end)"
                          @input.stop="_updateChapterTimeEdit(idx, 'end', $event.target.value)"
                          @blur.stop="_commitChapterTimeEdit(idx, 'end')"
                          @keydown.enter.stop.prevent="_commitChapterTimeEdit(idx, 'end')"
                        />
                      </div>
                    </td>
                    <td>
                      <div class="concept-actions">
                        <button class="btn secondary" style="padding:4px 8px; font-size:12px;" :disabled="idx === 0" @click.stop="moveConceptDraftRow(idx, -1)">上移</button>
                        <button class="btn secondary" style="padding:4px 8px; font-size:12px;" :disabled="idx === conceptDraftModal.chapters.length - 1" @click.stop="moveConceptDraftRow(idx, 1)">下移</button>
                        <button class="btn secondary" style="padding:4px 8px; font-size:12px;" :disabled="idx === conceptDraftModal.chapters.length - 1" @click.stop="mergeConceptDraftRow(idx, 1)">合併下一個</button>
                        <button class="btn danger" style="padding:4px 8px; font-size:12px;" @click.stop="removeConceptDraftRow(idx)">刪除</button>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>

              <div v-else-if="!conceptDraftModal.loading && !conceptDraftModal.error" style="padding:14px 0; color:#6c757d;">
                目前沒有可顯示的草稿章節。這通常表示後端回傳為 0 筆，或資料尚未整理成可編輯的語意單元。
              </div>

              <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
                <button class="btn secondary" @click="insertConceptDraftRow()">+ 新增章節</button>
              </div>
            </section>

            <aside class="concept-right">
              <div class="suggest-panel">
                <div class="suggest-panel-title">AI 補充建議</div>

                <div class="suggest-section">
                  <div class="suggest-section-title">rename_suggestions</div>
                  <div v-if="conceptDraftModal.aiSuggestions.rename_suggestions.length" class="suggest-list">
                    <div v-for="(item, idx) in conceptDraftModal.aiSuggestions.rename_suggestions" :key="`rename-${idx}`" class="suggest-card">
                      <div class="suggest-main">
                        <div class="suggest-original">{{ item.original }}</div>
                        <div class="suggest-arrow">→</div>
                        <div class="suggest-target">{{ item.suggested }}</div>
                      </div>
                      <div class="suggest-meta" v-if="item.cell_id !== undefined && item.cell_id !== null">cell_id：{{ item.cell_id }}</div>
                      <div class="suggest-actions-row">
                        <button class="btn secondary" @click="applyRenameSuggestion(item)">套用</button>
                      </div>
                    </div>
                  </div>
                  <div v-else class="suggest-empty">目前沒有需要優化的名稱。</div>
                </div>

                <div class="suggest-section">
                  <div class="suggest-section-title">missing_concepts</div>
                  <div v-if="conceptDraftModal.aiSuggestions.missing_concepts?.length" class="suggest-list">
                    <div v-for="(item, idx) in conceptDraftModal.aiSuggestions.missing_concepts" :key="`missing-${idx}`" class="suggest-card">
                      <div class="suggest-main" style="display:block; gap:0;">
                        <div class="suggest-target">{{ item.label }}</div>
                        <div class="suggest-meta" v-if="item.reason" style="margin-top:4px; white-space:pre-wrap;">{{ item.reason }}</div>
                      </div>
                      <div class="suggest-actions-row" style="margin-top:8px;">
                        <button class="btn secondary" @click="addMissingConceptSuggestion(item)">加入章節</button>
                      </div>
                    </div>
                  </div>
                  <div v-else class="suggest-empty">目前沒有可補上的章節建議。</div>
                </div>

                <div class="suggest-section">
                  <div class="suggest-section-title">chapter_recommendations</div>
                  <div v-if="conceptDraftModal.aiSuggestions.chapter_recommendations.length" class="suggest-list">
                    <div v-for="(item, idx) in conceptDraftModal.aiSuggestions.chapter_recommendations" :key="`chapter-rec-${idx}`" class="suggest-card">
                      <div class="suggest-main" style="display:block; gap:0;">
                        <div class="suggest-original">
                          cell_id：{{ item.cell_id ?? '—' }} ｜ candidate_id：{{ item.best_candidate_id || item.candidate_id || '—' }}
                          <span
                            v-if="item.rerank_source"
                            :style="{
                              marginLeft: '6px',
                              fontSize: '11px',
                              fontWeight: '700',
                              padding: '2px 7px',
                              borderRadius: '999px',
                              background: item.rerank_source === 'ai' ? '#dcfce7' : '#fef9c3',
                              color: item.rerank_source === 'ai' ? '#166534' : '#854d0e',
                              border: item.rerank_source === 'ai' ? '1px solid #86efac' : '1px solid #fde047',
                            }"
                          >{{ item.rerank_source === 'ai' ? '✦ AI 判斷' : '⚙ 規則 fallback' }}</span>
                        </div>
                        <div class="suggest-target" style="margin-top:4px;">
                          {{ item.chapter_title || item.concept_label || '（未命名）' }}
                        </div>
                        <div class="suggest-meta" v-if="item.chapter_note" style="margin-top:4px; white-space:pre-wrap;">
                          {{ item.chapter_note }}
                        </div>
                        <div class="suggest-meta" v-if="item.alternative_candidate_ids?.length" style="margin-top:4px;">
                          其他候選：{{ item.alternative_candidate_ids.join(', ') }}
                        </div>
                      </div>
                      <div class="suggest-actions-row" style="margin-top:8px;">
                        <button
                          class="btn secondary"
                          :disabled="!item.chapter_title && !item.chapter_note"
                          @click="applyChapterRecommendation(item, { applyTitle: true, applyTime: false })"
                          title="套用 AI 建議的章節標題與說明（不會修改時間）"
                        >套用標題</button>
                      </div>
                    </div>
                  </div>
                  <div v-else class="suggest-empty">目前沒有可用的章節候選建議。</div>
                </div>
              </div>
            </aside>
          </div>
        </div>

        <div class="modal-root-footer">
          <button class="btn secondary" @click="conceptDraftModal.open = false">取消</button>
          <button class="btn secondary" @click="recoverAutoConceptStart">回復自動區間</button>
          <button class="btn secondary" @click="rebuildConceptDraft" :disabled="conceptDraftModal.saving || conceptDraftModal.rebuilding">
            {{ conceptDraftModal.rebuilding ? '判斷中...' : '重新判斷' }}
          </button>
          <button class="btn primary" @click="saveConceptDraft" :disabled="conceptDraftModal.saving">
            {{ conceptDraftModal.saving ? '儲存中...' : '確認並儲存正式章節' }}
          </button>
        </div>
      </div>
    </div>


    <!-- ===== Preview Modal（統一用 previewData）===== -->
    <div v-if="modal.open" class="modal-mask" @click.self="closeModal">
      <div class="modal">
        <div class="modal-head">
          <div class="modal-title">
            【AI 題目生成紀錄｜題目預覽（{{ previewData?.meta?.version || "—" }}）】
          </div>
          <button class="x" @click="closeModal">✕</button>
        </div>

        <!-- 三態：loading / error / content -->
        <div class="modal-body" v-if="modal.loading">
          ⏳ 題目載入中…
        </div>

        <div class="modal-body" v-else-if="modal.err">
          ⚠️ 題目載入失敗：{{ modal.err }}
        </div>

        <div class="modal-body" v-else>
          <div class="pv" v-if="previewData?.ok">
            <!-- Header meta -->
            <div class="pvTop">
              <div class="pvMetaRow">
                <div class="pill">教學來源：{{ previewData.meta.unit || "—" }} / {{ previewData.meta.title || "—" }}</div>
                <div class="pill">分析片段：{{ previewData.meta.segment_label || "—" }}</div>
                <div class="pill">字幕版本：{{ previewData.meta.subtitle_version || "—" }}</div>
                <div class="pill">
                  題目狀態：{{ previewData.meta.status || "—" }}
                  （{{ previewData.meta.enabled ? "學生端可見" : "學生端不可見" }}）
                </div>
              </div>
              <hr class="pvHr" />
            </div>

            <!-- 題目說明 -->
            <div class="pvSection">
              <div class="pvH">【題目說明】</div>
              <div class="pvBox">
                {{ previewData.prompt ? previewData.prompt : "（未提供題目敘述）" }}
              </div>
            </div>

            
            <!-- [新增] Traceability：字幕對應證據 -->
            <div class="pvSection">
              <div class="pvH">【字幕對應證據（Traceability）】</div>
              <div class="pvBox">
                <div class="pvMetaRow">
                  <div class="pill">
                    對應字幕時間：{{ fmtTimeRange(previewData) }}
                  </div>
                  <div class="pill">
                    字幕句數：{{ fmtIndexRange(previewData) }}
                  </div>
                </div>

                <details v-if="getSubtitleText(previewData)">
                  <summary>使用字幕內容（點我展開）</summary>
                  <div style="white-space: pre-line;">{{ getSubtitleText(previewData) }}</div>
                </details>
                <div v-else class="hint">（目前沒有保存使用字幕內容）</div>
              </div>
            </div>

            <div class="pvSection">
              <div class="pvH">【字幕切片品質】</div>
              <div class="pvBox">
                <div class="pvMetaRow">
                  <div class="pill">best_score：{{ getSelectorMeta(previewData)?.best_score ?? "—" }}</div>
                  <div class="pill">hit_ratio：{{ fmtRatio(getSelectorMeta(previewData)?.hit_ratio) }}</div>
                  <div class="pill">keyword_coverage：{{ fmtRatio(getSelectorMeta(previewData)?.keyword_coverage_ratio) }}</div>
                  <div class="pill">selected_count：{{ getSelectorMeta(previewData)?.selected_count ?? "—" }}</div>
                  <div class="pill">fallback：{{ getSelectorMeta(previewData)?.fallback || "—" }}</div>
                </div>

                <div class="pvMetaRow">
                  <div class="pill">policy_ok：{{ getPolicyMeta(previewData)?.ok === true ? "yes" : (getPolicyMeta(previewData)?.ok === false ? "no" : "—") }}</div>
                  <div class="pill">policy_min_hits：{{ getPolicyMeta(previewData)?.min_hits ?? "—" }}</div>
                  <div class="pill">policy_hit_count：{{ (getPolicyMeta(previewData)?.anchor_hits || []).length }}</div>
                  <div class="pill">off_topic_ratio：{{ fmtRatio(getPolicyMeta(previewData)?.off_topic_ratio) }}</div>
                </div>
                <div class="hint" v-if="getPolicyMeta(previewData)?.reason">
                  policy reason：{{ getPolicyMeta(previewData)?.reason }}
                </div>

                <details>
                  <summary>關鍵句（key_sentences，點我展開）</summary>
                  <ol class="pvList" v-if="(getKeySentences(previewData) || []).length">
                    <li v-for="(s, i) in getKeySentences(previewData)" :key="`ks-${i}`">{{ s }}</li>
                  </ol>
                  <div v-else class="hint">（目前沒有 key_sentences）</div>
                </details>
              </div>
            </div>

            <div class="pvSection">
              <div class="pvH">【研究分析面板】</div>
              <div class="pvBox">
                <div class="pvMetaRow">
                  <div class="pill">alignment_score：{{ fmtRatio(getAlignmentConfidence(previewData)?.score) }}</div>
                  <div class="pill">mapped_slots：{{ getAlignmentConfidence(previewData)?.mapped_slots ?? "—" }}</div>
                  <div class="pill">hinted_slots：{{ getAlignmentConfidence(previewData)?.hinted_slots ?? "—" }}</div>
                  <div class="pill">source：{{ getAlignmentConfidence(previewData)?.source || "—" }}</div>
                </div>

                <div class="pvMetaRow">
                  <div class="pill">function_param_count：{{ getFunctionProfile(previewData)?.param_count ?? "—" }}</div>
                  <div class="pill">need_input：{{ boolText(getFunctionProfile(previewData)?.need_input) }}</div>
                  <div class="pill">need_print：{{ boolText(getFunctionProfile(previewData)?.need_print) }}</div>
                  <div class="pill">allow_condition：{{ boolText(getFunctionProfile(previewData)?.allow_condition) }}</div>
                </div>

                <div class="pvMetaRow">
                  <div class="pill">typed_definition：{{ getSentenceTypeCount(previewData, "definition") }}</div>
                  <div class="pill">typed_rule：{{ getSentenceTypeCount(previewData, "rule") }}</div>
                  <div class="pill">typed_example：{{ getSentenceTypeCount(previewData, "example") }}</div>
                  <div class="pill">typed_other：{{ getSentenceTypeCount(previewData, "other") }}</div>
                </div>

                <details>
                  <summary>關鍵句型別（key_sentences_typed，點我展開）</summary>
                  <ol class="pvList" v-if="(getTypedKeySentences(previewData) || []).length">
                    <li v-for="(row, i) in getTypedKeySentences(previewData)" :key="`kst-${i}`">
                      <span class="typeTag">{{ row.sentence_type || "other" }}</span>
                      {{ row.text || "" }}
                    </li>
                  </ol>
                  <div v-else class="hint">（目前沒有 key_sentences_typed）</div>
                </details>
              </div>
            </div>

            <!-- [新增] Rule Check：規則驗證 -->
            <div class="pvSection">
              <div class="pvH">【規則驗證】</div>
              <div class="pvBox">
                <div class="pvMetaRow">
                  <div class="pill">單元類型：{{ (previewData?.unit_type || previewData?.meta?.unit_type || "—") }}</div>
                  <div class="pill">限制條件：{{ fmtConstraints(previewData) }}</div>
                </div>

                <div class="pvRules" v-if="previewData?.rule_check">
                  <div class="pvRuleLine">{{ mark(previewData.rule_check.has_for) }} 包含 for</div>
                  <div class="pvRuleLine">{{ mark(!previewData.rule_check.has_while) }} 未使用 while</div>
                  <div class="pvRuleLine">{{ mark(previewData.rule_check.has_range) }} 使用 range()</div>
                  <div class="pvRuleLine">{{ mark(previewData.rule_check.has_accumulate) }} 使用累加 (+= 或 count = count + 1)</div>

                  <div class="pvRuleLine" v-if="previewData.rule_check.has_if !== undefined">
                    {{ mark(previewData.rule_check.has_if) }} 包含 if
                  </div>
                  <div class="pvRuleLine" v-if="previewData.rule_check.has_else !== undefined">
                    {{ mark(previewData.rule_check.has_else) }} 包含 else
                  </div>
                  <div class="pvRuleLine" v-if="previewData.rule_check.has_elif !== undefined">
                    {{ mark(previewData.rule_check.has_elif) }} 包含 elif
                  </div>

                  <div class="pvRuleLine" v-if="previewData.rule_check.exec_ok !== undefined">
                    {{ mark(previewData.rule_check.exec_ok) }} 執行測試
                  </div>
                  <div class="pvRuleLine" v-if="previewData.rule_check.compile_ok !== undefined">
                    {{ mark(previewData.rule_check.compile_ok) }} 編譯檢查
                  </div>

                  <div class="pvRuleSummary">
                    整體結果：
                    <span :class="previewData.rule_check.ok ? 'okText' : 'warnText'">
                      {{ previewData.rule_check.ok ? "符合教學目標" : "部分符合教學目標" }}
                    </span>
                    <span v-if="previewData.rule_check.reason">（{{ previewData.rule_check.reason }}）</span>
                  </div>
                </div>

                <div v-else class="hint">（目前沒有 rule_check，請確認後端是否有寫入 rule_check）</div>
              </div>
            </div>
        <!-- Parsons 區塊 -->
            <div class="pvSection">
              <div class="pvHRow">
                <div class="pvH">Parsons 區塊（AI 生成）</div>
                <label class="dSwitch" title="控制老師端與學生端是否顯示中文語意提示">
                  <input
                    type="checkbox"
                    :checked="!hideSemanticZh"
                    :disabled="semanticToggleSaving"
                    @change="onToggleSemanticVisibility($event.target.checked)"
                  />
                  <span class="dSlider"></span>
                  <span class="dSwitchText">{{ hideSemanticZh ? "中文語意：隱藏" : "中文語意：顯示" }}</span>
                </label>
              </div>
              <div class="pvGrid">
                <div class="pvBlock" v-for="b in (previewData.parsons_blocks || [])" :key="b.id">
                  <div class="code">{{ b.text || "（空）" }}</div>
                  <div class="zh" v-if="!hideSemanticZh">中文語意（AI）：{{ enhanceMeaning(b.text, b.meaning_zh) }}</div>
                </div>
                <div v-if="!(previewData.parsons_blocks || []).length" class="pvEmpty">
                  尚無 Parsons 區塊資料（請確認題目生成時是否有存 blocks）。
                </div>
              </div>
            </div>

          <!-- 干擾區塊 -->
          <div class="pvSection">
            <div class="pvH">B-2 干擾區塊（Distractor Blocks｜AI 生成）</div>

            <div class="pvGrid">
              <div
                class="pvBlock pvBlockD"
                v-for="b in (previewData.distractor_blocks || [])"
                :key="'d-' + (b.id || b._id)"
                :class="{ removed: !isKeepDistractor(b.id || b._id) }"
              >
                <!-- 右上角顯示/隱藏開關（預設開啟） -->
                <div class="dCtrl">
                  <label class="dSwitch" :title="isKeepDistractor(b.id || b._id) ? '目前：顯示（學生端會看到）' : '目前：隱藏（學生端不會看到）'">
                    <input
                      type="checkbox"
                      :checked="isKeepDistractor(b.id || b._id)"
                      @change="setDistractorVisible(b.id || b._id, $event.target.checked)"
                    />
                    <span class="dSlider"></span>
                    <span class="dSwitchText">{{ isKeepDistractor(b.id || b._id) ? "顯示" : "隱藏" }}</span>
                  </label>
                </div>

                <div v-if="!isKeepDistractor(b.id || b._id)" class="dMask" aria-hidden="true"></div>

                <div class="code">{{ b.text || "（空）" }}</div>
                <div class="zh" v-if="!hideSemanticZh">
                  中文語意（AI）：{{ enhanceMeaning(b.text, b.meaning_zh) }}
                  <span v-if="!isKeepDistractor(b.id || b._id)" class="removedTag">（已標記移除）</span>
                </div>
              </div>

              <div v-if="!(previewData.distractor_blocks || []).length" class="pvEmpty">
                尚無干擾區塊資料（請確認生成流程是否有存 distractor_blocks）。
              </div>
            </div>
          </div>

            <!-- 正確答案順序 -->
            <div class="pvSection">
              <div class="pvH">【正確答案順序（僅教師可見）】</div>
              <div class="pvOrder">
                {{ previewData.solution_order_text || "（未提供）" }}
              </div>
              <div class="pvIndentControls" v-if="solutionDetailList.length">
                <label>
                  解答縮排顯示：
                  <select v-model="teacherIndentMode">
                    <option value="raw">原始縮排</option>
                    <option value="2">每層 2 空格</option>
                    <option value="4">每層 4 空格</option>
                  </select>
                </label>
              </div>
              <!-- ✅ 顯示對應程式碼（老師一眼看懂） -->
              <div class="pvOrderList" v-if="solutionDetailList.length">
                <div class="pvOrderItem" v-for="r in solutionDetailList" :key="r.id">
                  <div class="pvOrderIdx">{{ r.idx }}.</div>
                  <div class="pvOrderMain">
                    <div class="pvOrderCode">
                      <span class="pvOrderId">{{ r.id }}</span>
                      <span class="indentGuide" v-if="getIndentLevel(r) > 0">{{ indentGuidePrefix(r) }}</span>{{ formatTeacherCodeLine(r) }}
                    </div>
                    <div class="pvOrderZh" v-if="!hideSemanticZh && r.meaning_zh">中文語意：{{ r.meaning_zh }}</div>
                  </div>
                </div>
              </div>

              <div class="pvSmall">
                版本：{{ previewData.meta.version }}　生成時間：{{ previewData.meta.created_at || "—" }}　建立者：{{ previewData.meta.created_by || "AI Agent" }}
              </div>
            </div>

            <!-- 問題類型 + 老師備註 -->
            <div class="pvSection">
              <div class="pvH">問題類型（可複選）</div>
              <div class="pvChecks">
                <label><input type="checkbox" v-model="reviewForm.tags" value="題幹過長" /> 題幹過長</label>
                <label><input type="checkbox" v-model="reviewForm.tags" value="中文語意提示不清楚" /> 中文語意提示不清楚</label>
                <label><input type="checkbox" v-model="reviewForm.tags" value="干擾選項不清楚" /> 干擾選項不清楚</label>
                <label><input type="checkbox" v-model="reviewForm.tags" value="題目難度過高" /> 題目難度過高</label>
                <label><input type="checkbox" v-model="reviewForm.tags" value="其他" /> 其他</label>
              </div>

              <div class="pvH" style="margin-top:10px;">老師備註（選填）</div>
              <textarea class="pvNote" v-model="reviewForm.note" placeholder="題目敘述偏長 / 干擾片段不夠明確 / 難度需調整 …"></textarea>
            </div>
          </div>

          <div v-else class="d-state err">
            ⚠️ 無法顯示預覽資料（previewData 為空或 ok=false）
          </div>

          <!-- ✅ 新增：固定底部按鈕列 -->
          <div class="modal-foot" v-if="!modal.loading && !modal.err && previewData?.ok">
            <div class="pvActions">
              <button class="btn primary" @click="publishFromPreview" :disabled="previewData.meta.enabled">發布至學生題庫</button>
              <button class="btn" @click="regenerate">重新生成新版本</button>
              <button class="btn warn" @click="returnNotPublish">退回不發布</button>
              <button class="btn" @click="closeModal">關閉</button>
            </div>
          </div>

        </div>
      </div>
    </div>



    </main>
  </div>
</template>

<script setup>
import { ref, reactive, watch, onMounted, computed } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";
import TeacherSidebar from "../components/TeacherSidebar.vue";

// ✅ Teacher T5 API base（有些版本用 /api/teacher/t5，有些用 /api/teacher_t5）
const T5_BASE_PRIMARY = "/api/teacher_t5";
const T5_BASE_FALLBACK = "/api/teacher_t5";

// [新增] 避免 AI 生成/預覽超過 15 秒造成前端 axios timeout（ECONNABORTED）
const T5_TIMEOUT_MS = 120000; // 120s（只影響本頁 teacher_t5 相關請求）
const PARSONS_TIMEOUT_MS = 120000;

function _withT5Timeout(cfg = {}) {
  const c = { ...(cfg || {}) };
  if (c.timeout === undefined || c.timeout === null) c.timeout = T5_TIMEOUT_MS;
  return c;
}

async function t5Get(path, config = {}) {
  try {
    return await api.get(`${T5_BASE_PRIMARY}${path}`, _withT5Timeout(config));
  } catch (e) {
    const status = e?.response?.status;
    if (status === 404) return await api.get(`${T5_BASE_FALLBACK}${path}`, _withT5Timeout(config));
    throw e;
  }
}

async function t5Post(path, data = {}, config = {}) {
  try {
    return await api.post(`${T5_BASE_PRIMARY}${path}`, data, _withT5Timeout(config));
  } catch (e) {
    const status = e?.response?.status;
    if (status === 404) return await api.post(`${T5_BASE_FALLBACK}${path}`, data, _withT5Timeout(config));
    throw e;
  }
}

async function parsonsGet(path, config = {}) {
  return api.get(`/api/parsons${path}`, { ...(config || {}), timeout: PARSONS_TIMEOUT_MS });
}

async function parsonsPost(path, data = {}, config = {}) {
  return api.post(`/api/parsons${path}`, data, { ...(config || {}), timeout: PARSONS_TIMEOUT_MS });
}
/** 干擾區塊：老師保留/移除狀態
 *  true  = 保留（會出現在學生端）
 *  false = 移除（不會出現在學生端）
 */
const distractorKeep = reactive({}); // { [blockId]: true/false }

const router = useRouter();

// ===== state =====
const units = ref([]);
const videos = ref([]);
const selectedUnit = ref("");
const selectedVideo = ref("");
const selectedSubtitleVersion = ref("");

// ===== [新增] D 區表格顯示 helper =====
function getSourceType(q) {
  return q?.source_type || q?.gen_source || "ai";
}

function getTaskCode(q) {
  // 固定題優先顯示 task_code，沒有就顯示 FIXED-01
  if (getSourceType(q) === "fixed") {
    return q?.task_code || "FIXED-01";
  }
  // AI 題優先顯示 version，沒有就 fallback task_code
  return q?.version || q?.task_code || "v1";
}

function getStatus(q) {
  return q?.status || q?.review_status || "pending";
}

function getStatusZh(q) {
  const s = getStatus(q);

  if (s === "approved") return "已審核";
  if (s === "published") return "已發布";
  if (s === "rejected") return "已退回";
  if (s === "draft") return "草稿";
  return "待審核";
}

function isStudentVisible(q) {
  // enabled=true 或 status=published 都視為學生可見
  return !!q?.enabled || getStatus(q) === "published";
}

// [新增] ===== 後測開放控制（依單元） =====
const testCycleId = computed(() => (selectedUnit.value || "default").toString().trim());
const postOpen = ref(null); // null=未讀取, true/false=狀態
const postOpenLoading = ref(false);

async function fetchPostOpen() {
  if (!testCycleId.value) return;
  postOpenLoading.value = true;
  try {
    const { data } = await api.get("/api/parsons/test/cycle/get", { params: { test_cycle_id: testCycleId.value } }); // [新增]
    postOpen.value = !!data?.post_open;
  } catch (e) {
    // 若後端尚未加入此 API，不讓頁面壞掉
    postOpen.value = null;
  } finally {
    postOpenLoading.value = false;
  }
}

// [新增] 切換單元時重新讀取後測開放狀態
async function setPostOpen(open) {
  if (!testCycleId.value) return;

  postOpenLoading.value = true;

  try {
    await api.post("/api/parsons/test/cycle/toggle", {
      test_cycle_id: testCycleId.value,
      post_open: open
    });

    postOpen.value = open;

  } catch (e) {
    console.error("toggle error:", e);
  } finally {
    postOpenLoading.value = false;
  }
}


const loading = reactive({ units: false, videos: false, videoInfo: false, questions: false });
const busy = reactive({ regen: false });
const err = reactive({ a: "", d: "", e: "" });

// 穩定模式：降低 AI 生題隨機性（溫度 0.05），讓同一影片多次生題結果更一致
const stableMode = ref(false);

const videoInfo = reactive({
  enabled: true,
  subtitle_uploaded: false,
  subtitle_verified: false,
  subtitle_versions: [],
  source: "",
  duration: ""
});

// D table
const filterStatus = ref("all");
const sortOrder = ref("newest");
const questions = ref([]);
const previewData = ref(null);
const teacherIndentMode = ref("raw");
const hideSemanticZh = ref(false);
const semanticToggleSaving = ref(false);
const segmentEditorRows = ref([]);
const segmentEditorSaving = ref(false);
const segmentEditorMessage = ref("");
const subtitleSegments = ref([]);
const allSubtitleSegments = ref([]);
const selectedFixedTaskId = ref("");
const segmentEditorTaskId = ref("");
const pageSegmentEditorLoading = ref(false);
const segmentEditorDebug = ref(null);
const predictedWindowMap = ref({});

// Modal
const modal = reactive({
  open: false,
  loading: false,
  err: "",
  data: null
});

// [新增] Concept Draft 確認 Modal
const conceptDraftModal = reactive({
  open: false,
  loading: false,
  error: "",
  statusText: "",
  debugText: "",
  teachingRangeStart: "",
  teachingRangeEnd: "",
  teachingRange: null,
  teachingRangeSegments: [],
  teachingRangeWarning: null,
  teachingRangeRecommendedRange: null,
  teachingRangeEffective: true,
  teachingRangeMode: "",
  codeStartTs: "",
  codeSummaryRange: null,
  codeSummarySegments: [],
  codeStartWarning: null,
  codeStartRecommendedRange: null,
  codeStartEffective: true,
  preservedPreviousDraft: false,
  saving: false,
  rebuilding: false,
  taskId: null,
  selectedChapterIndex: null,
  chapters: [],
  timeInputDrafts: {},
  blockChapterMap: {},
  blockChapterCodeMap: [],
  chapterCodeMap: [],
  aiSuggestions: {
    rename_suggestions: [],
    missing_concepts: [],
    chapter_recommendations: [],
  },
});

function resetConceptDraftModalState() {
  conceptDraftModal.loading = false;
  conceptDraftModal.error = "";
  conceptDraftModal.statusText = "";
  conceptDraftModal.debugText = "";
  conceptDraftModal.teachingRangeStart = "";
  conceptDraftModal.teachingRangeEnd = "";
  conceptDraftModal.teachingRange = null;
  conceptDraftModal.teachingRangeSegments = [];
  conceptDraftModal.teachingRangeWarning = null;
  conceptDraftModal.teachingRangeRecommendedRange = null;
  conceptDraftModal.teachingRangeEffective = true;
  conceptDraftModal.teachingRangeMode = "";
  conceptDraftModal.codeStartTs = "";
  conceptDraftModal.codeSummaryRange = null;
  conceptDraftModal.codeSummarySegments = [];
  conceptDraftModal.codeStartWarning = null;
  conceptDraftModal.codeStartRecommendedRange = null;
  conceptDraftModal.codeStartEffective = true;
  conceptDraftModal.preservedPreviousDraft = false;
  conceptDraftModal.saving = false;
  conceptDraftModal.rebuilding = false;
  conceptDraftModal.taskId = null;
  conceptDraftModal.selectedChapterIndex = null;
  conceptDraftModal.chapters = [];
  conceptDraftModal.timeInputDrafts = {};
  conceptDraftModal.blockChapterMap = {};
  conceptDraftModal.blockChapterCodeMap = [];
  conceptDraftModal.chapterCodeMap = [];
  conceptDraftModal.aiSuggestions = {
    rename_suggestions: [],
    missing_concepts: [],
    chapter_recommendations: [],
  };
}

// [新增] ===== Traceability / RuleCheck helpers（論文證據呈現）=====
// timecode regex (00:02:15 or 02:15)
const _reTC = /^(\d{1,2}:)?\d{2}:\d{2}$/;

function _secToTC(sec) {
  if (sec === null || sec === undefined || sec === "") return "—";
  if (typeof sec === "string") {
    const s = sec.trim();
    // 已是 timecode
    if (_reTC.test(s)) return s;
    const n = Number(s);
    if (!Number.isNaN(n)) return _secToTC(n);
    return s || "—";
  }
  if (typeof sec !== "number" || Number.isNaN(sec)) return "—";
  const total = Math.max(0, Math.floor(sec));
  const hh = Math.floor(total / 3600);
  const mm = Math.floor((total % 3600) / 60);
  const ss = total % 60;
  const pad = (x) => String(x).padStart(2, "0");
  return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
}

function _parseChapterTimeToSec(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;

  const text = String(value).trim();
  if (!text) return null;

  if (/^\d+(?:\.\d+)?$/.test(text)) {
    const n = Number(text);
    return Number.isFinite(n) ? n : null;
  }

  const parts = text.split(":").map((part) => String(part || "").trim());
  if (parts.length === 2) {
    const mm = Number(parts[0]);
    const ss = Number(parts[1]);
    if (!Number.isFinite(mm) || !Number.isFinite(ss)) return null;
    return (mm * 60) + ss;
  }
  if (parts.length === 3) {
    const hh = Number(parts[0]);
    const mm = Number(parts[1]);
    const ss = Number(parts[2]);
    if (!Number.isFinite(hh) || !Number.isFinite(mm) || !Number.isFinite(ss)) return null;
    return (hh * 3600) + (mm * 60) + ss;
  }

  return null;
}

function _parseTeachingRangeTimeToSec(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;

  const text = String(value).trim();
  if (!text) return null;

  if (/^\d+(?:\.\d+)?$/.test(text)) {
    const n = Number(text);
    return Number.isFinite(n) ? n : null;
  }

  return _parseChapterTimeToSec(text);
}

function _chapterSourceLabel(source) {
  const raw = String(source || "").trim().toLowerCase();
  if (!raw) return "未知來源";
  if (raw.includes("formal")) return "正式章節";
  if (raw.includes("teacher")) return "老師章節";
  if (raw.includes("ai_auto_fill")) return "🤖 AI 自動補入";  // [新增]
  if (raw.includes("draft")) return "系統草稿";
  if (raw.includes("subtitle_health") || raw.includes("recommend")) return "健康報表推薦";
  if (raw.includes("ai_segment")) return "AI 對齊片段";
  return source;
}

function _inferConceptTagFromLabel(label) {
  const raw = String(label || "").trim().toLowerCase();
  if (!raw) return "";
  if (raw.includes("輸入") || raw.includes("input") || raw.includes("讀取")) return "input_int_cast";
  if (raw.includes("輸出") || raw.includes("print") || raw.includes("分隔") || raw.includes("空格")) return "print_separator";
  if (raw.includes("條件") || raw.includes("判斷") || raw === "if") return "if_condition_logic";
  if (raw.includes("分支") || raw.includes("elif") || raw.includes("else")) return "if_branch_order";
  if (raw.includes("邊界") || raw.includes("例外") || raw.includes("特殊")) return "edge_case_condition";
  if (raw.includes("巢狀") || raw.includes("雙層") || raw.includes("多層")) return "nested_loop_structure";
  if (raw.includes("反向") || raw.includes("倒著") || raw.includes("遞減")) return "loop_reverse_range";
  if (raw.includes("星號") || raw.includes("2i-1") || raw.includes("奇數")) return "star_formula_2i_minus_1";
  if (raw.includes("空白") || raw.includes("n-i")) return "space_formula_n_minus_i";
  if (raw.includes("迴圈") || raw.includes("for") || raw.includes("while")) return "loop_count_control";
  if (raw.includes("函式") || raw.includes("def") || raw.includes("return")) return "python_syntax";
  return "";
}

function _normalizeAiSuggestions(payload) {
  const rename = Array.isArray(payload?.rename_suggestions) ? payload.rename_suggestions : [];
  const missing = Array.isArray(payload?.missing_concepts) ? payload.missing_concepts : [];
  const recommendations = Array.isArray(payload?.chapter_recommendations) ? payload.chapter_recommendations : [];

  return {
    rename_suggestions: rename
      .map((item) => ({
        cell_id: item?.cell_id ?? null,
        original: String(item?.original || "").trim(),
        suggested: String(item?.suggested || "").trim(),
      }))
      .filter((item) => item.original && item.suggested),
    missing_concepts: missing
      .map((item) => ({
        label: String(item?.label || "").trim(),
        reason: String(item?.reason || "").trim(),
      }))
      .filter((item) => item.label),
    chapter_recommendations: recommendations
      .map((item) => ({
        cell_id: item?.cell_id ?? null,
        concept_tag: String(item?.concept_tag || "").trim(),
        concept_label: String(item?.concept_label || "").trim(),
        candidate_id: String(item?.candidate_id || item?.best_candidate_id || "").trim(),
        best_candidate_id: String(item?.best_candidate_id || item?.candidate_id || "").trim(),
        alternative_candidate_ids: Array.isArray(item?.alternative_candidate_ids)
          ? item.alternative_candidate_ids.map((v) => String(v || "").trim()).filter(Boolean)
          : [],
        chapter_title: String(item?.chapter_title || "").trim(),
        chapter_title_candidates: Array.isArray(item?.chapter_title_candidates)
          ? item.chapter_title_candidates.map((v) => String(v || "").trim()).filter(Boolean)
          : [],
        chapter_note: String(item?.chapter_note || "").trim(),
        confidence: Number.isFinite(Number(item?.confidence)) ? Number(item.confidence) : null,
        // [新增] 保留 rerank_source，讓 UI 能顯示「AI 判斷」或「規則 fallback」
        rerank_source: String(item?.rerank_source || "").trim() || null,
        candidates: Array.isArray(item?.candidates) ? item.candidates : [],
      }))
      .filter((item) => item.best_candidate_id || item.chapter_title || item.chapter_note),
  };
}

function _formatTimeInputValue(value) {
  const sec = _parseTeachingRangeTimeToSec(value);
  if (sec === null) return String(value ?? "").trim();
  return _secToTC(sec);
}

function _secToMMSS(sec) {
  if (sec === null || sec === undefined) return "";
  if (typeof sec === "string") {
    const trimmed = String(sec).trim();
    if (/^(\d{1,2}:)?\d{1,2}:\d{2}$/.test(trimmed)) return trimmed;
    const parsed = _parseChapterTimeToSec(trimmed);
    return parsed !== null ? _secToMMSS(parsed) : "";
  }
  if (typeof sec !== "number") return "";
  const total = Math.max(0, Math.floor(Number(sec)));
  const hh = Math.floor(total / 3600);
  const mm = Math.floor((total % 3600) / 60);
  const ss = total % 60;
  const pad = (x) => String(x).padStart(2, "0");
  return hh > 0 ? `${hh}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
}

function _MMSSToSec(mmss) {
  if (mmss === null || mmss === undefined || mmss === "") return null;
  const text = String(mmss).trim();
  if (!text) return null;
  const parts = text.split(":").map(p => String(p || "").trim()).filter(p => p.length > 0);
  if (parts.length === 2) {
    const mm = Number(parts[0]);
    const ss = Number(parts[1]);
    if (!Number.isFinite(mm) || !Number.isFinite(ss)) return null;
    return (mm * 60) + ss;
  }
  if (parts.length === 3) {
    const hh = Number(parts[0]);
    const mm = Number(parts[1]);
    const ss = Number(parts[2]);
    if (!Number.isFinite(hh) || !Number.isFinite(mm) || !Number.isFinite(ss)) return null;
    return (hh * 3600) + (mm * 60) + ss;
  }
  return null;
}

function _warningText(value) {
  if (!value) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "object") {
    return String(value.message || value.detail || value.error || "").trim();
  }
  return String(value).trim();
}

// 驗證教學大區間輸入，並檢查是否超出字幕範圍（如果有的話）
function _validateTeachingRangeWithinSubtitleBounds(startValue, endValue) {
  const start = _parseTeachingRangeTimeToSec(startValue);
  const end = _parseTeachingRangeTimeToSec(endValue);
  if (!Number.isFinite(start) || start < 0 || !Number.isFinite(end) || end < 0) {
    return { ok: false, message: "教學大區間請輸入 mm:ss、hh:mm:ss 或秒數。" };
  }
  if (end <= start) {
    return { ok: false, message: "教學大區間的結束時間必須大於開始時間。" };
  }

  const subtitleBounds = _getSubtitleRangeBounds();
  if (!subtitleBounds) {
    return { ok: true, start, end, subtitleBounds: null };
  }

  const subtitleStart = Number(subtitleBounds.rawStart ?? subtitleBounds.start);
  const subtitleEnd = Number(subtitleBounds.rawEnd ?? subtitleBounds.end);
  if (Number.isFinite(subtitleStart) && Number.isFinite(subtitleEnd)) {
    if (start < subtitleStart || end > subtitleEnd) {
      return {
        ok: false,
        message: "教學區間超出全片字幕範圍",
        subtitleBounds,
        start,
        end,
      };
    }
  }

  return { ok: true, start, end, subtitleBounds };
}

function _getTeachingRangeStartSec() {
  const bounds = _getTeachingRangeBounds();
  if (bounds && Number.isFinite(Number(bounds.rawStart))) {
    return Number(bounds.rawStart);
  }
  const start = _parseTeachingRangeTimeToSec(conceptDraftModal.teachingRangeStart);
  return Number.isFinite(start) ? start : null;
}

function _formatChapterTimeRelative(value) {
  const sec = _parseChapterTimeToSec(value);
  if (sec === null) return String(value ?? "").trim();
  const base = _getTeachingRangeStartSec();
  if (!Number.isFinite(base)) return _secToTC(sec);
  return _secToTC(Math.max(0, sec - base));
}

function _chapterTimeDraftKey(index, field) {
  return `${Number(index)}:${String(field || "").trim()}`;
}

function _chapterTimeInputDisplay(index, field, rawValue) {
  const key = _chapterTimeDraftKey(index, field);
  if (Object.prototype.hasOwnProperty.call(conceptDraftModal.timeInputDrafts || {}, key)) {
    return String(conceptDraftModal.timeInputDrafts[key] ?? "");
  }
  return _formatChapterTimeRelative(rawValue);
}

function _beginChapterTimeEdit(index, field, rawValue) {
  const key = _chapterTimeDraftKey(index, field);
  conceptDraftModal.timeInputDrafts[key] = _formatChapterTimeRelative(rawValue);
}

function _updateChapterTimeEdit(index, field, value) {
  const key = _chapterTimeDraftKey(index, field);
  conceptDraftModal.timeInputDrafts[key] = String(value ?? "");
}

function _commitChapterTimeEdit(index, field) {
  const key = _chapterTimeDraftKey(index, field);
  const text = String(conceptDraftModal.timeInputDrafts?.[key] ?? "").trim();
  const rows = conceptDraftModal.chapters || [];
  const row = rows[Number(index)];
  if (!row) {
    delete conceptDraftModal.timeInputDrafts[key];
    return;
  }

  if (!text) {
    row[field] = "";
    delete conceptDraftModal.timeInputDrafts[key];
    return;
  }

  const relativeSec = _parseChapterTimeToSec(text);
  if (relativeSec === null) {
    row[field] = text;
    delete conceptDraftModal.timeInputDrafts[key];
    return;
  }

  const base = _getTeachingRangeStartSec();
  const absoluteSec = Number.isFinite(base) ? Math.max(0, base + relativeSec) : relativeSec;
  row[field] = absoluteSec;
  delete conceptDraftModal.timeInputDrafts[key];
}

function _applyChapterTimeRelativeInput(index, field, value) {
  // Legacy wrapper: preserve old call sites if any remain.
  _beginChapterTimeEdit(index, field, conceptDraftModal.chapters?.[Number(index)]?.[field]);
  _updateChapterTimeEdit(index, field, value);
  _commitChapterTimeEdit(index, field);
}

function _formatChapterTimeRelativeRange(chapter) {
  const start = _formatChapterTimeRelative(chapter?.start);
  const end = _formatChapterTimeRelative(chapter?.end);
  if (start === "—" && end === "—") return "—";
  return `${start} - ${end}`;
}

function getSubtitleRange(pd) {
  // 支援多種欄位命名：subtitle_range / source_subtitle / subtitle_range 包在 source_subtitle 內
  return (
    pd?.subtitle_range ||
    pd?.source_subtitle?.subtitle_range ||
    pd?.source_subtitle ||
    null
  );
}

function getSubtitleText(pd) {
  return (
    pd?.subtitle_text_used ||
    pd?.source_subtitle?.subtitle_text_used ||
    pd?.source_subtitle?.text_used ||
    pd?.source_subtitle?.text ||
    pd?.text_used ||
    ""
  );
}

function fmtTimeRange(pd) {
  const r = getSubtitleRange(pd);
  if (!r) return "—";
  const a = r.start_ts ?? r.start_time ?? r.start ?? r.start_sec;
  const b = r.end_ts ?? r.end_time ?? r.end ?? r.end_sec;
  const sa = _secToTC(a);
  const sb = _secToTC(b);
  if (sa === "—" && sb === "—") return "—";
  return `${sa} – ${sb}`;
}

function fmtIndexRange(pd) {
  const r = getSubtitleRange(pd);
  if (!r) return "—";
  const a = r.start_index ?? r.start_idx ?? r.start_i;
  const b = r.end_index ?? r.end_idx ?? r.end_i;
  if (a === undefined && b === undefined) return "—";
  if (a !== undefined && b !== undefined) return `第 ${a} 句 – 第 ${b} 句`;
  return a !== undefined ? `第 ${a} 句` : `第 ${b} 句`;
}

function fmtConstraints(pd) {
  const c = pd?.constraints || {};
  // loop_style / condition requirements
  if (c.loop_style) return c.loop_style;
  if (c.require_else) return "ifelse";
  if (c.require_elif) return "elif";
  if (c.require_if) return "if";
  return "—";
}

function getKeySentences(pd) {
  const arr = pd?.key_sentences;
  if (!Array.isArray(arr)) return [];
  return arr.map((x) => String(x || "").trim()).filter(Boolean);
}

function getSelectorMeta(pd) {
  const m = pd?.selector_meta;
  return (m && typeof m === "object") ? m : {};
}

function getPolicyMeta(pd) {
  const m = pd?.unified_policy_meta;
  return (m && typeof m === "object") ? m : {};
}

function getFunctionProfile(pd) {
  const m = pd?.function_profile;
  return (m && typeof m === "object") ? m : {};
}

function getAlignmentConfidence(pd) {
  const m = pd?.alignment_confidence;
  return (m && typeof m === "object") ? m : {};
}

function getTypedKeySentences(pd) {
  const arr = pd?.key_sentences_typed;
  if (!Array.isArray(arr)) return [];
  return arr
    .map((x) => ({
      text: String(x?.text || "").trim(),
      sentence_type: String(x?.sentence_type || "other").trim().toLowerCase() || "other",
    }))
    .filter((x) => x.text);
}

function getSentenceTypeCount(pd, t) {
  const rows = getTypedKeySentences(pd);
  const target = String(t || "other").trim().toLowerCase();
  return rows.filter((r) => r.sentence_type === target).length;
}

function selectConceptDraftChapter(index) {
  const idx = Number(index);
  if (!Number.isFinite(idx)) return;
  conceptDraftModal.selectedChapterIndex = idx;
}

const selectedConceptDraftChapter = computed(() => {
  const idx = Number(conceptDraftModal.selectedChapterIndex);
  if (!Number.isFinite(idx)) return null;
  return conceptDraftModal.chapters?.[idx] || null;
});

function _selectedChapterCodeRows() {
  const chapter = selectedConceptDraftChapter.value;
  if (!chapter) return [];

  const chapterIdx = Number(conceptDraftModal.selectedChapterIndex);
  const directByIndex = (Array.isArray(conceptDraftModal.chapterCodeMap)
    ? conceptDraftModal.chapterCodeMap
    : []).find((item) => Number(item?.chapter_index) === chapterIdx);

  if (directByIndex && Array.isArray(directByIndex.blocks) && directByIndex.blocks.length) {
    return directByIndex.blocks;
  }

  const tag = String(chapter?.concept_tag || "").trim().toLowerCase();
  const label = String(chapter?.concept_label || "").trim();

  const rows = (Array.isArray(conceptDraftModal.blockChapterCodeMap)
    ? conceptDraftModal.blockChapterCodeMap
    : []).filter((item) => {
    const itemTag = String(item?.chapter_concept_tag || "").trim().toLowerCase();
    const itemLabel = String(item?.chapter_concept_label || "").trim();
    if (tag && itemTag && tag === itemTag) return true;
    if (label && itemLabel && label === itemLabel) return true;
    return false;
  });

  return rows;
}

function _draftChapterRange(chapter) {
  const start = _parseChapterTimeToSec(chapter?.start);
  const end = _parseChapterTimeToSec(chapter?.end);
  if (start === null || end === null || end <= start) return { start: null, end: null, valid: false };
  return { start, end, valid: true };
}

function isSubtitleSegmentHighlighted(seg) {
  const chapter = selectedConceptDraftChapter.value;
  if (!chapter) return false;
  const range = _draftChapterRange(chapter);
  if (!range.valid) return false;
  const start = _toNumOrNull(seg?.start);
  const end = _toNumOrNull(seg?.end);
  if (start === null || end === null || end <= start) return false;
  return end > range.start && start < range.end;
}

function insertConceptDraftRow(afterIndex = null, row = null) {
  const rows = conceptDraftModal.chapters || [];
  const insertRow = row || {
    cell_id: rows.length + 1,
    concept_tag: "",
    concept_label: "新概念章節",
    concept: "新概念章節",
    chapter_label: "新概念章節",
    chapter_source: "manual",
    source: "manual",
    start: 0,
    end: 0,
  };

  let index = rows.length;
  const idx = Number(afterIndex);
  if (Number.isFinite(idx) && idx >= 0 && idx < rows.length) {
    index = idx + 1;
  }
  rows.splice(index, 0, insertRow);
  rows.forEach((item, rowIdx) => {
    item.cell_id = rowIdx + 1;
  });
  conceptDraftModal.selectedChapterIndex = index;
}

function removeConceptDraftRow(index) {
  const rows = conceptDraftModal.chapters || [];
  const idx = Number(index);
  if (!Number.isFinite(idx) || idx < 0 || idx >= rows.length) return;
  rows.splice(idx, 1);
  rows.forEach((item, rowIdx) => {
    item.cell_id = rowIdx + 1;
  });
  if (!rows.length) {
    conceptDraftModal.selectedChapterIndex = null;
    return;
  }
  conceptDraftModal.selectedChapterIndex = Math.min(idx, rows.length - 1);
}

function moveConceptDraftRow(index, direction = 1) {
  const rows = conceptDraftModal.chapters || [];
  const from = Number(index);
  const to = from + Number(direction);
  if (!Array.isArray(rows) || from < 0 || from >= rows.length || to < 0 || to >= rows.length) {
    return;
  }

  const [item] = rows.splice(from, 1);
  rows.splice(to, 0, item);
  rows.forEach((row, rowIdx) => {
    row.cell_id = rowIdx + 1;
  });
  conceptDraftModal.selectedChapterIndex = to;
}

function _toNumOrNull(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function getEffectiveRange(row) {
  const ts = _toNumOrNull(row?.teacherStart);
  const te = _toNumOrNull(row?.teacherEnd);

  // Teacher range is the authoritative source for subtitle preview once present.
  if (ts !== null || te !== null) {
    return {
      start: ts,
      end: te,
      source: "teacher",
      valid: ts !== null && te !== null && te > ts,
    };
  }

  const as = _toNumOrNull(row?.aiStart);
  const ae = _toNumOrNull(row?.aiEnd);
  if (as !== null && ae !== null && ae > as) {
    return { start: as, end: ae, source: "ai", valid: true };
  }

  return { start: null, end: null, source: "none", valid: false };
}

function formatMergedSlots(group) {
  if (!Array.isArray(group) || !group.length) return "—";
  return group
    .map((v) => Number.isFinite(Number(v)) ? `slot ${Number(v)}` : null)
    .filter(Boolean)
    .join(", ");
}

function getPredictedRange(row) {
  const map = (predictedWindowMap.value && typeof predictedWindowMap.value === "object") ? predictedWindowMap.value : {};
  const p = map[String(row?.slot)] || null;
  if (!p || typeof p !== "object") {
    return { start: null, end: null, source: "無資料" };
  }
  const src = String(p.segment_source || "").trim();
  if (!src || src === "hardcoded_default") {
    return { start: null, end: null, source: "無資料" };
  }
  const ps = _toNumOrNull(p.start);
  const pe = _toNumOrNull(p.end);
  if (ps === null || pe === null || pe <= ps) {
    return { start: null, end: null, source: "無資料" };
  }
  return {
    start: ps,
    end: pe,
    source: src,
  };
}

function formatRangeDelta(row) {
  const eff = getEffectiveRange(row);
  const pred = getPredictedRange(row);
  const es = _toNumOrNull(eff.start);
  const ee = _toNumOrNull(eff.end);
  const ps = _toNumOrNull(pred.start);
  const pe = _toNumOrNull(pred.end);
  if (es === null || ee === null || ps === null || pe === null) return "—";
  const ds = (es - ps).toFixed(1);
  const de = (ee - pe).toFixed(1);
  return `Δs ${ds}s / Δe ${de}s`;
}

function _readSlotSeg(segMap, idx) {
  const m = segMap && typeof segMap === "object" ? segMap : {};
  const keys = [String(idx), `s${idx + 1}`, `第${idx + 1}格`];
  for (const k of keys) {
    const seg = m[k];
    if (!seg || typeof seg !== "object") continue;
    const s = _toNumOrNull(seg.start ?? seg.start_ts);
    const e = _toNumOrNull(seg.end ?? seg.end_ts);
    if (s !== null && e !== null && e > s) {
      return { start: s, end: e };
    }
  }
  return { start: null, end: null };
}

function fmtSec(v) {
  const n = _toNumOrNull(v);
  if (n === null) return "—";
  return n.toFixed(1);
}

function fmtSecRange(a, b) {
  const sa = fmtSec(a);
  const sb = fmtSec(b);
  if (sa === "—" && sb === "—") return "—";
  return `${sa} - ${sb}`;
}

function fmtTcRange(a, b) {
  const sa = _secToTC(a);
  const sb = _secToTC(b);
  if (sa === "—" && sb === "—") return "—";
  return `${sa} - ${sb}`;
}

function _segmentRoleByText(text) {
  const t = String(text || "").toLowerCase();
  if (!t) return "any";
  if (/(判斷|如果|if\b|elif\b|條件|是不是|是否)/.test(t)) return "condition";
  if (/(回傳|return|print|輸出|結果|除以|乘以|加|減)/.test(t)) return "compute";
  if (/(輸入|讀取|input)/.test(t)) return "input";
  return "any";
}

function _conceptFromCodeAndMap(row) {
  const byMap = String(row?.concept || "").trim().toLowerCase();
  if (byMap && byMap !== "unknown") return byMap;
  const t = String(row?.code || "").toLowerCase();
  if (/^\s*elif\b/.test(t)) return "elif";
  if (/^\s*if\b/.test(t)) return "if";
  if (/^\s*else\s*:/.test(t)) return "else";
  if (/^\s*for\b/.test(t)) return "for";
  if (/^\s*while\b/.test(t)) return "while";
  if (/(\/|除號|除以|division)/.test(t)) return "division";
  if (/(\*|乘號|乘以|multiplication)/.test(t)) return "multiplication";
  if (/(\+|加號|加上|addition)/.test(t)) return "addition";
  if (/(\-|減號|減去|subtraction)/.test(t)) return "subtraction";
  if (/(輸入|讀取|input)/.test(t)) return "input";
  if (/(輸出|印出|print)/.test(t)) return "output";
  return "";
}

function _normalizeConceptKey(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) return "";
  const aliases = {
    function: "function",
    def: "function",
    函式: "function",
    函數: "function",
    函式定義: "function",
    讀取輸入: "input",
    輸入: "input",
    input: "input",
    輸出結果: "output",
    輸出: "output",
    output: "output",
    print: "output",
    回傳: "return",
    return: "return",
    條件判斷: "condition",
    if: "condition",
    elif: "condition",
    else: "condition",
    迴圈: "loop",
    for: "loop",
    while: "loop",
    資料處理: "compute",
  };
  return aliases[raw] || raw;
}

function _conceptMatches(a, b) {
  return _normalizeConceptKey(a) === _normalizeConceptKey(b);
}

function _conceptMatchText(text, concept) {
  const t = String(text || "").toLowerCase();
  const c = String(concept || "").toLowerCase();
  if (!c) return true;
  const conceptLexicon = {
    division: ["除", "除號", "除以", "/", "division"],
    multiplication: ["乘", "乘號", "乘以", "*", "multiplication"],
    addition: ["加", "加號", "加上", "+", "addition"],
    subtraction: ["減", "減號", "減去", "-", "subtraction"],
    input: ["輸入", "讀取", "input"],
    output: ["輸出", "印出", "print"],
  };
  const terms = conceptLexicon[c] || [];
  if (!terms.length) return true;
  return terms.some((term) => t.includes(String(term).toLowerCase()));
}

function _isTransitionSentence(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  return /(接著|再來|最後|下一個條件|我們要來讀取|下一步|然後)/.test(t);
}

function _findSeedIndex(row) {
  const segs = subtitleSegments.value || [];
  if (!segs.length) return -1;
  const s = _toNumOrNull(row.teacherStart ?? row.aiStart);
  const e = _toNumOrNull(row.teacherEnd ?? row.aiEnd);
  if (s === null || e === null || e <= s) return -1;

  let bestIdx = -1;
  let bestOverlap = -1;
  for (let i = 0; i < segs.length; i++) {
    const seg = segs[i] || {};
    const ss = _toNumOrNull(seg.start);
    const ee = _toNumOrNull(seg.end);
    if (ss === null || ee === null || ee <= ss) continue;
    const overlap = Math.max(0, Math.min(e, ee) - Math.max(s, ss));
    if (overlap > bestOverlap) {
      bestOverlap = overlap;
      bestIdx = i;
    }
  }
  return bestIdx;
}

function _collectSubtitlePreview(start, end) {
  const segs = _getClauseSegments();
  const s = _toNumOrNull(start);
  const e = _toNumOrNull(end);

  if (s === null || e === null || e <= s || !segs.length) return "";

  // 找所有有重疊的字幕
  const hits = segs.filter((seg) => {
    const ss = _toNumOrNull(seg.start);
    const ee = _toNumOrNull(seg.end);
    if (ss === null || ee === null || ee <= ss) return false;
    return ee > s && ss < e;
  });

  if (!hits.length) return "";

  // ⭐ 核心：選「重疊最多」的那一句
  let best = null;
  let bestOverlap = -1;

  for (const seg of hits) {
    const ss = Number(seg.start);
    const ee = Number(seg.end);

    const overlap = Math.min(e, ee) - Math.max(s, ss);

    if (overlap > bestOverlap) {
      bestOverlap = overlap;
      best = seg;
    }
  }

  return best ? String(best.text || "").trim() : "";
}

function _getBlocksForConcept(conceptName) {
  if (!previewData.value || !previewData.value.parsons_blocks) return [];
  const map = previewData.value.slot_concept_map || {};
  const aiMap = previewData.value.ai_segment_map || {};
  const conceptMap = previewData.value.concept_segment_map || {};
  const blocks = previewData.value.parsons_blocks;

  const out = [];
  const target = String(conceptName || "").trim();
  for (const [idx, b] of blocks.entries()) {
    const slotConcept = map[String(idx)] || b?.concept || b?.semantic || b?.meaning_zh || "";
    const aiConcept = aiMap[String(idx)]?.concept || aiMap[String(idx)]?.evidence || "";
    const derivedConcept = _conceptFromCodeAndMap({ concept: slotConcept, code: b?.text || "" });
    const codeText = String(b?.text || "");
    const hasConceptMatch = _conceptMatches(target, slotConcept) || _conceptMatches(target, aiConcept) || _conceptMatches(target, derivedConcept);
    const hasTextMatch = _conceptMatches(target, codeText) || _conceptMatches(target, b?.meaning || "") || _conceptMatches(target, b?.meaning_zh || "");
    if (!hasConceptMatch && !hasTextMatch) continue;

    out.push({
      index: idx,
      id: b.id,
      code: codeText,
      meaning: b.meaning_zh,
      aiStart: aiMap[String(idx)]?.start ?? aiMap[String(idx)]?.start_ts,
      aiEnd: aiMap[String(idx)]?.end ?? aiMap[String(idx)]?.end_ts,
      conceptRange: conceptMap[target] || null,
      slotConcept,
    });
  }
  return out;
}

function _collectSubtitleOverlapSegments(start, end) {
  const segs = subtitleSegments.value || [];

  const s = _toNumOrNull(start);
  const e = _toNumOrNull(end);
  if (!Array.isArray(segs) || !segs.length || s === null || e === null || e <= s) return [];

  const hits = [];
  for (const seg of segs) {
    const ss = _toNumOrNull(seg?.start);
    const ee = _toNumOrNull(seg?.end);
    if (ss === null || ee === null || ee <= ss) continue;
    if (ee > s && ss < e) {
      hits.push({ id: seg?.id, start: ss, end: ee, text: String(seg?.text || "").trim() });
    }
  }
  return hits;
}

function _getSubtitleRangeBounds() {
  const segs =
    allSubtitleSegments.value?.length
      ? allSubtitleSegments.value
      : subtitleSegments.value;

  if (!segs || !segs.length) return null;

  const starts = segs.map(s => Number(s.start)).filter(Number.isFinite);
  const ends = segs.map(s => Number(s.end)).filter(Number.isFinite);

  if (!starts.length || !ends.length) return null;

  return {
    start: Math.min(...starts),
    end: Math.max(...ends)
  };
}

function _getCodeSummaryRangeBounds() {
  const bounds = conceptDraftModal.codeSummaryRange;
  if (bounds && Number.isFinite(Number(bounds.start)) && Number.isFinite(Number(bounds.end))) {
    return {
      start: Math.floor(Number(bounds.start)),
      end: Math.floor(Number(bounds.end)),
      rawStart: Number(bounds.start),
      rawEnd: Number(bounds.end),
    };
  }

  const segs = Array.isArray(conceptDraftModal.codeSummarySegments) && conceptDraftModal.codeSummarySegments.length
    ? conceptDraftModal.codeSummarySegments
    : subtitleSegments.value || [];
  if (!Array.isArray(segs) || !segs.length) return null;

  let minStart = null;
  let maxEnd = null;
  for (const seg of segs) {
    const ss = _toNumOrNull(seg?.start);
    const ee = _toNumOrNull(seg?.end);
    if (ss === null || ee === null || ee <= ss) continue;
    minStart = minStart === null ? ss : Math.min(minStart, ss);
    maxEnd = maxEnd === null ? ee : Math.max(maxEnd, ee);
  }
  if (minStart === null || maxEnd === null) return null;
  return {
    start: Math.floor(minStart),
    end: Math.floor(maxEnd),
    rawStart: minStart,
    rawEnd: maxEnd,
  };
}

function _getTeachingRangeBounds() {
  // 老師輸入優先：支援 mm:ss / hh:mm:ss，避免回退顯示舊的系統預設範圍。
  const start = _parseTeachingRangeTimeToSec(conceptDraftModal.teachingRangeStart);
  const end = _parseTeachingRangeTimeToSec(conceptDraftModal.teachingRangeEnd);
  if (start !== null && end !== null && end > start) {
    return {
      start: Math.floor(start),
      end: Math.floor(end),
      rawStart: start,
      rawEnd: end,
    };
  }

  const bounds = conceptDraftModal.teachingRange;
  if (bounds && Number.isFinite(Number(bounds.start)) && Number.isFinite(Number(bounds.end))) {
    return {
      start: Math.floor(Number(bounds.start)),
      end: Math.floor(Number(bounds.end)),
      rawStart: Number(bounds.start),
      rawEnd: Number(bounds.end),
    };
  }

  const segs = Array.isArray(conceptDraftModal.teachingRangeSegments) && conceptDraftModal.teachingRangeSegments.length
    ? conceptDraftModal.teachingRangeSegments
    : _filterSegmentsByRange(subtitleSegments.value || [], _getSubtitleRangeBounds());
  if (!Array.isArray(segs) || !segs.length) return null;

  let minStart = null;
  let maxEnd = null;
  for (const seg of segs) {
    const ss = _toNumOrNull(seg?.start);
    const ee = _toNumOrNull(seg?.end);
    if (ss === null || ee === null || ee <= ss) continue;
    minStart = minStart === null ? ss : Math.min(minStart, ss);
    maxEnd = maxEnd === null ? ee : Math.max(maxEnd, ee);
  }
  if (minStart === null || maxEnd === null) return null;
  return {
    start: Math.floor(minStart),
    end: Math.floor(maxEnd),
    rawStart: minStart,
    rawEnd: maxEnd,
  };
}

function _formatRangeBounds(bounds) {
  if (!bounds) return "—";
  return `${_secToTC(bounds.start)} – ${_secToTC(bounds.end)}`;
}

function _formatSubtitleRangeBounds(bounds) {
  return _formatRangeBounds(bounds);
}

function _filterSegmentsByRange(segs, bounds) {
  if (!bounds) return Array.isArray(segs) ? segs : [];
  const start = Number(bounds?.rawStart ?? bounds?.start);
  const end = Number(bounds?.rawEnd ?? bounds?.end);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return Array.isArray(segs) ? segs : [];
  return (Array.isArray(segs) ? segs : []).filter((seg) => {
    const ss = _toNumOrNull(seg?.start);
    const ee = _toNumOrNull(seg?.end);
    if (ss === null || ee === null || ee <= ss) return false;
    return !(ee <= start || ss >= end);
  });
}

function _getCodeSummarySegments() {
  if (Array.isArray(conceptDraftModal.codeSummarySegments) && conceptDraftModal.codeSummarySegments.length) {
    return conceptDraftModal.codeSummarySegments;
  }
  const bounds = _getCodeSummaryRangeBounds();
  if (bounds) {
    return _filterSegmentsByRange(subtitleSegments.value || [], bounds);
  }
  return subtitleSegments.value || [];
}

function _getTeachingRangeSegments() {
  if (Array.isArray(conceptDraftModal.teachingRangeSegments) && conceptDraftModal.teachingRangeSegments.length) {
    return conceptDraftModal.teachingRangeSegments;
  }
  const start = _getTeachingRangeStartSec();
  const end = _parseTeachingRangeTimeToSec(conceptDraftModal.teachingRangeEnd);
  if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
    return _filterSegmentsByRange(subtitleSegments.value || [], {
      start,
      end,
      rawStart: start,
      rawEnd: end,
    });
  }
  return [];
}

function _getConceptAlignedSummarySegments() {
  const chapters = Array.isArray(conceptDraftModal.chapters) ? conceptDraftModal.chapters : [];
  if (!chapters.length) {
    return _getTeachingRangeSegments().slice(0, 20);
  }

  const merged = [];
  const seen = new Set();

  for (const chapter of chapters) {
    const hits = _collectSubtitleOverlapSegments(chapter?.start, chapter?.end);
    for (const seg of hits) {
      const ss = _toNumOrNull(seg?.start);
      const ee = _toNumOrNull(seg?.end);
      const text = String(seg?.text || "").trim();
      if (ss === null || ee === null || ee <= ss || !text) continue;
      const key = `${ss}|${ee}|${text}`;
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push({
        ...seg,
        start: ss,
        end: ee,
        text,
      });
    }
  }

  if (!merged.length) {
    return _getTeachingRangeSegments().slice(0, 20);
  }

  merged.sort((a, b) => Number(a.start || 0) - Number(b.start || 0));
  return merged.slice(0, 20);
}

function _selectedDraftChapterSubtitleText() {
  const chapter = selectedConceptDraftChapter.value;
  if (!chapter) return "";
  const hits = _collectSubtitleOverlapSegments(chapter.start, chapter.end);
  return hits.map((seg) => String(seg.text || "").trim()).filter(Boolean).join(" / ");
}

function _getDraftSubtitlePreview(start, end, limit = 2) {
  const hits = _collectSubtitleOverlapSegments(start, end);
  if (!hits.length) return "";
  return hits
    .slice(0, Math.max(1, Number(limit) || 2))
    .map((seg) => String(seg.text || "").trim())
    .filter(Boolean)
    .join(" / ");
}

function _getDraftSuggestion(row, idx) {
  const hits = _collectSubtitleOverlapSegments(row?.start, row?.end);
  const currentConcept = String(row?.concept || "").trim();
  const next = conceptDraftModal.chapters?.[idx + 1] || null;
  const prev = idx > 0 ? conceptDraftModal.chapters?.[idx - 1] || null : null;
  const currentTag = String(row?.concept_tag || currentConcept).trim();
  const nextTag = String(next?.concept_tag || next?.concept || "").trim();
  const prevTag = String(prev?.concept_tag || prev?.concept || "").trim();

  if (!hits.length) {
    return "此段沒有字幕對應，建議調整起訖點或設定程式開始點。";
  }
  if (hits.length >= 4) {
    return "這段內容偏長，建議檢查是否可與相鄰章節合併。";
  }
  if (currentTag && nextTag && currentTag === nextTag) {
    return "與下一格概念相近，可以考慮合併章節。";
  }
  if (currentTag && prevTag && currentTag === prevTag) {
    return "與上一格概念相近，可以考慮合併章節。";
  }
  if (hits.length === 1) {
    return "這段對應一個字幕句，概念名稱可以再更精準。";
  }
  return "可微調邊界，讓內容預覽更集中在同一個概念。";
}

function mergeConceptDraftRow(index, direction = 1) {
  const rows = conceptDraftModal.chapters || [];
  const targetIndex = Number(index) + Number(direction);
  if (!Array.isArray(rows) || targetIndex < 0 || targetIndex >= rows.length || targetIndex === index) {
    return;
  }

  const current = rows[index];
  const target = rows[targetIndex];
  if (!current || !target) return;

  const currentStart = _parseChapterTimeToSec(current.start);
  const currentEnd = _parseChapterTimeToSec(current.end);
  const targetStart = _parseChapterTimeToSec(target.start);
  const targetEnd = _parseChapterTimeToSec(target.end);
  const starts = [currentStart, targetStart].filter((v) => Number.isFinite(v));
  const ends = [currentEnd, targetEnd].filter((v) => Number.isFinite(v));

  if (!starts.length || !ends.length) return;

  current.start = _secToTC(Math.min(...starts));
  current.end = _secToTC(Math.max(...ends));
  current.concept_label = String(current.concept_label || current.concept || target.concept_label || target.concept || "").trim() || String(target.concept_label || target.concept || "").trim();
  current.concept_tag = String(current.concept_tag || target.concept_tag || _inferConceptTagFromLabel(current.concept_label) || "").trim();
  current.concept = String(current.concept_label || current.concept_tag || target.concept || "").trim();
  current.chapter_label = String(current.chapter_label || target.chapter_label || current.concept_label || current.concept || "");
  current.chapter_source = String(current.chapter_source || target.chapter_source || "");
  current.ai_chapter_title = String(current.ai_chapter_title || target.ai_chapter_title || "");
  current.ai_chapter_note = [current.ai_chapter_note, target.ai_chapter_note].filter(Boolean).join(" / ");
  current.chapter_name_candidates = Array.from(new Set([...(current.chapter_name_candidates || []), ...(target.chapter_name_candidates || [])].filter(Boolean)));
  current.concept_tags = Array.from(new Set([...(current.concept_tags || []), ...(target.concept_tags || [])].filter(Boolean)));

  rows.splice(targetIndex, 1);
  rows.forEach((item, rowIdx) => {
    item.cell_id = rowIdx + 1;
  });
}

function applyRenameSuggestion(suggestion) {
  const original = String(suggestion?.original || "").trim();
  const suggested = String(suggestion?.suggested || "").trim();
  if (!original || !suggested) return;

  let row = null;
  const cellId = suggestion?.cell_id;
  if (cellId !== undefined && cellId !== null && String(cellId).trim() !== "") {
    row = (conceptDraftModal.chapters || []).find((ch) => String(ch?.cell_id || "") === String(cellId));
  }
  if (!row) {
    row = (conceptDraftModal.chapters || []).find((ch) => {
      const label = String(ch?.concept_label || ch?.concept || ch?.chapter_label || "").trim();
      return label === original;
    });
  }
  if (!row) return;

  row.concept_label = suggested;
  row.concept_tag = String(row.concept_tag || _inferConceptTagFromLabel(suggested) || "").trim();
  row.concept = suggested;
  row.chapter_label = suggested;
}

// [新增] 套用 chapter_recommendations 的 AI 建議：
// - 只更新 concept_label（chapter_title）與 ai_chapter_note
// - 不修改 start / end（後端規則：不可覆蓋時間）
// - 不可覆蓋後端已儲存的正式章節內容（僅更新前端草稿）
function _findChapterRowForRecommendation(item) {
  const cellId = item?.cell_id;
  let row = null;
  if (cellId !== undefined && cellId !== null && String(cellId).trim() !== "") {
    row = (conceptDraftModal.chapters || []).find(
      (ch) => String(ch?.cell_id || "") === String(cellId)
    );
  }
  if (!row && item?.concept_tag) {
    row = (conceptDraftModal.chapters || []).find(
      (ch) => String(ch?.concept_tag || "").trim() === String(item.concept_tag).trim()
    );
  }
  return row;
}

function _pickRecommendationWindow(item) {
  const candidates = Array.isArray(item?.candidates) ? item.candidates : [];
  if (!candidates.length) return null;

  const bestId = String(item?.best_candidate_id || item?.candidate_id || "").trim();
  let selected = null;
  if (bestId) {
    selected = candidates.find((cand) => String(cand?.candidate_id || cand?.id || "").trim() === bestId) || null;
  }
  if (!selected) selected = candidates[0] || null;
  if (!selected) return null;

  const start = _toNumOrNull(selected?.start);
  const end = _toNumOrNull(selected?.end);
  if (start === null || end === null || end <= start) return null;

  return { start, end };
}

function _hasOverlapWithOtherChapters(targetRow, nextStart, nextEnd) {
  const rows = Array.isArray(conceptDraftModal.chapters) ? conceptDraftModal.chapters : [];
  for (const row of rows) {
    if (row === targetRow) continue;
    const rs = _parseChapterTimeToSec(row?.start);
    const re = _parseChapterTimeToSec(row?.end);
    if (!Number.isFinite(rs) || !Number.isFinite(re) || re <= rs) continue;
    if (nextEnd > rs && nextStart < re) {
      return true;
    }
  }
  return false;
}

function applyChapterRecommendation(item, options = {}) {
  const applyTitle = options?.applyTitle !== false;
  const applyTime = Boolean(options?.applyTime);
  const newTitle = String(item?.chapter_title || "").trim();
  const newNote = String(item?.chapter_note || "").trim();
  if (!applyTime && !newTitle && !newNote) return;

  const row = _findChapterRowForRecommendation(item);
  if (!row) return;

  // 套用標題（不改時間）
  if (applyTitle && newTitle) {
    row.concept_label = newTitle;
    row.concept = newTitle;
    row.chapter_label = newTitle;
    const inferredTag = _inferConceptTagFromLabel(newTitle);
    if (inferredTag) row.concept_tag = inferredTag;
  }
  // 套用 AI 說明備註
  if (applyTitle && newNote) {
    row.ai_chapter_note = newNote;
  }

  if (applyTime) {
    const picked = _pickRecommendationWindow(item);
    if (!picked) {
      conceptDraftModal.error = "目前候選沒有可用時間，無法套用。";
      return;
    }
    if (_hasOverlapWithOtherChapters(row, picked.start, picked.end)) {
      conceptDraftModal.error = "AI 候選時間會與其他章節重疊，已取消套用。";
      return;
    }
    row.start = picked.start;
    row.end = picked.end;
    conceptDraftModal.error = "";
  }
}

function addMissingConceptSuggestion(suggestion) {
  const label = String(suggestion?.label || "").trim();
  const reason = String(suggestion?.reason || "").trim();
  if (!label) return;

  const inferredTag = _inferConceptTagFromLabel(label);

  insertConceptDraftRow(conceptDraftModal.selectedChapterIndex, {
    cell_id: conceptDraftModal.chapters.length + 1,
    concept_tag: inferredTag,
    concept_label: label,
    concept: label,
    chapter_label: label,
    chapter_source: "ai_missing_concept",
    source: "ai_missing_concept",
    ai_chapter_note: reason ? `${reason}（請手動補上時間，或儲存時由系統推估）` : "請手動補上時間，或儲存時由系統推估",
    start: "",
    end: "",
  });
}

function applyTeachingRange() {
  const startValue = String(conceptDraftModal.teachingRangeStart || "").trim();
  const endValue = String(conceptDraftModal.teachingRangeEnd || "").trim();
  if (!startValue || !endValue) {
    conceptDraftModal.error = "請先輸入教學大區間的開始與結束時間。";
    return;
  }

  const validation = _validateTeachingRangeWithinSubtitleBounds(startValue, endValue);
  if (!validation.ok) {
    conceptDraftModal.error = validation.message;
    return;
  }

  const bounds = {
    start: Math.floor(validation.start),
    end: Math.floor(validation.end),
    rawStart: validation.start,
    rawEnd: validation.end,
  };
  conceptDraftModal.teachingRange = bounds;
  conceptDraftModal.teachingRangeWarning = null;
  conceptDraftModal.teachingRangeRecommendedRange = bounds;
  conceptDraftModal.codeStartWarning = null;
  conceptDraftModal.codeStartTs = _secToTC(Math.floor(validation.start));
  conceptDraftModal.teachingRangeMode = "teacher_range_only";
  rebuildConceptDraft("teacher_range_only");
}

function recoverAutoTeachingRange() {
  conceptDraftModal.teachingRangeStart = "";
  conceptDraftModal.teachingRangeEnd = "";
  conceptDraftModal.teachingRange = null;
  conceptDraftModal.teachingRangeWarning = null;
  conceptDraftModal.teachingRangeEffective = false;
  conceptDraftModal.codeStartTs = "";
  conceptDraftModal.codeStartWarning = null;
  conceptDraftModal.codeStartRecommendedRange = null;
  conceptDraftModal.codeStartEffective = true;
  conceptDraftModal.teachingRangeMode = "auto";
  conceptDraftModal.error = "";
  conceptDraftModal.statusText = "已回復自動判定，重新產生概念章節草稿...";
  rebuildConceptDraft("auto");
}

function applyConceptStartPoint() {
  return applyTeachingRange();
}

function recoverAutoConceptStart() {
  return recoverAutoTeachingRange();
}

function getSubtitleMatchMeta(row) {
  const eff = getEffectiveRange(row);
  const hits = eff.valid ? _collectSubtitleOverlapSegments(eff.start, eff.end) : [];
  return {
    source: eff.source,
    start: eff.start,
    end: eff.end,
    valid: Boolean(eff.valid),
    hitCount: hits.length,
  };
}

function _splitSubtitleTextToClauses(text) {
  const raw = String(text || "").trim();
  if (!raw) return [];

  let parts = raw
    .split(/(?<=[，。；！？、])/)
    .map((s) => s.trim())
    .filter(Boolean);

  const refined = [];
  for (const part of parts) {
    const sub = part
      .split(/(?=接著|再來|然後|如果|接下來|最後)/)
      .map((s) => s.trim())
      .filter(Boolean);
    refined.push(...sub);
  }

  return refined.length ? refined : [raw];
}

function _splitSegmentIntoClauseSegments(seg) {
  const ss = _toNumOrNull(seg?.start);
  const ee = _toNumOrNull(seg?.end);
  const text = String(seg?.text || "").trim();

  if (ss === null || ee === null || ee <= ss || !text) return [];

  const clauses = _splitSubtitleTextToClauses(text);
  if (clauses.length <= 1) {
    return [{
      start: ss,
      end: ee,
      text,
      parentStart: ss,
      parentEnd: ee,
    }];
  }

  const totalLen = clauses.reduce((sum, c) => sum + Math.max(1, c.length), 0);
  const totalDur = ee - ss;

  // 時間分配採「均分 + 字數權重」混合，並限制單句最大跨度，
  // 避免第一句吃掉過長時間，造成相鄰 slot 都命中同一句。
  const n = clauses.length;
  const equalDur = totalDur / Math.max(1, n);
  const minDur = Math.max(0.8, totalDur * 0.12);
  const maxDur = Math.max(minDur + 0.2, totalDur * 0.45);

  let weights = clauses.map((clause) => {
    const charRatio = Math.max(1, clause.length) / Math.max(1, totalLen);
    const mixed = (equalDur * 0.6) + (totalDur * charRatio * 0.4);
    return Math.min(maxDur, Math.max(minDur, mixed));
  });

  const weightSum = weights.reduce((a, b) => a + b, 0) || 1;
  weights = weights.map((w) => (w / weightSum) * totalDur);

  let cursor = ss;
  const out = [];

  for (let i = 0; i < clauses.length; i += 1) {
    const clause = clauses[i];
    const dur = weights[i] || equalDur;

    let end = (i === clauses.length - 1) ? ee : (cursor + dur);

    if (end <= cursor) {
      end = Math.min(ee, cursor + 0.2);
    }

    out.push({
      start: Number(cursor.toFixed(3)),
      end: Number(end.toFixed(3)),
      text: clause,
      parentStart: ss,
      parentEnd: ee,
    });

    cursor = end;
  }

  return out;
}

function _getClauseSegments() {
  const segs = subtitleSegments.value || [];
  const out = [];
  for (const seg of segs) {
    out.push(..._splitSegmentIntoClauseSegments(seg));
  }
  return out;
}

function _extractAssignedVariable(row) {
  const codeText = String(
    row?.codeText
    || row?.text
    || row?.blockText
    || row?.code
    || ""
  ).trim();
  if (!codeText) return "";
  const m = codeText.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=/);
  return m ? String(m[1] || "").toLowerCase() : "";
}

function _inputOrdinalForRow(row) {
  const rows = Array.isArray(segmentEditorRows.value) ? [...segmentEditorRows.value] : [];
  if (!rows.length) return 0;

  rows.sort((a, b) => Number(a?.slot || 0) - Number(b?.slot || 0));
  const targetSlot = Number(row?.slot);
  if (!Number.isFinite(targetSlot)) return 0;

  let order = 0;
  for (const r of rows) {
    const slot = Number(r?.slot);
    if (!Number.isFinite(slot) || slot > targetSlot) continue;
    if (_rowRoleFromCode(r) === "input") {
      order += 1;
    }
  }
  return order;
}

function _buildRowKeywords(row) {
  const concept = String(row?.concept || "").toLowerCase();
  const codeText = String(
    row?.codeText
    || row?.text
    || row?.blockText
    || row?.code
    || ""
  ).toLowerCase();

  const map = {
    function_def: ["函式", "def", "參數", "函式f", "函式 f"],
    addition: ["加", "加號", "相加", "+"],
    subtraction: ["減", "減號", "相減", "-"],
    multiplication: ["乘", "乘號", "相乘", "*"],
    division: ["除", "除號", "相除", "/"],
    input: ["輸入", "讀取", "讀入", "存進", "input"],
    output: ["輸出", "印出", "答案", "結果", "顯示", "print"],
    condition: ["如果", "判斷", "是不是", "條件"],
    return: ["回傳", "結果"],
  };

  const kws = [...(map[concept] || [])];

  if (codeText.startsWith("return")) kws.push("回傳", "結果");
  if (codeText.includes("input(") || codeText.includes("eval(input(")) {
    kws.push("輸入", "讀取", "讀入", "存進", "input");
    const inputOrder = _inputOrdinalForRow(row);
    if (inputOrder === 1) kws.push("第一個數字", "第一個輸入", "x", "存進x", "存進 x");
    if (inputOrder === 2) kws.push("第二個數字", "第二個輸入", "y", "存進y", "存進 y");
    if (inputOrder >= 3) kws.push("第三個輸入", "運算符號", "operator", "op");

    const varName = _extractAssignedVariable(row);
    if (varName === "x") kws.push("x", "第一個數字", "存進x", "存進 x");
    if (varName === "y") kws.push("y", "第二個數字", "存進y", "存進 y");
    if (varName === "op" || varName === "operator") kws.push("運算符號", "operator", "op", "第三個輸入");
  }
  if (codeText.startsWith("print(")) kws.push("輸出", "印出", "答案", "顯示");
  if (codeText.startsWith("if ") || codeText.startsWith("elif ")) kws.push("如果", "判斷", "是不是");

  return [...new Set(kws.filter(Boolean))];
}

function _keywordScore(text, keywords) {
  const t = String(text || "").toLowerCase();
  let score = 0;
  for (const kw of (keywords || [])) {
    if (kw && t.includes(String(kw).toLowerCase())) {
      score += 2;
    }
  }
  return score;
}

function _rowRoleFromCode(row) {
  const codeText = String(
    row?.codeText
    || row?.text
    || row?.blockText
    || row?.code
    || ""
  ).trim().toLowerCase();
  if (!codeText) return "any";
  if (codeText.startsWith("return")) return "return";
  if (codeText.startsWith("if ") || codeText.startsWith("elif ") || codeText.startsWith("else")) return "condition";
  if (codeText.includes("input(") || codeText.includes("eval(input(")) return "input";
  if (codeText.startsWith("print(")) return "output";
  return "any";
}

function _roleCompatibilityScore(clauseText, rowRole, row = null) {
  const t = String(clauseText || "").toLowerCase();
  if (!t || rowRole === "any") return 0;

  if (rowRole === "return") {
    let s = 0;
    if (/(回傳|return|結果)/.test(t)) s += 4;
    if (/(如果|判斷|是不是|條件|if|elif)/.test(t)) s -= 3;
    if (/(輸入|讀取|第一個數字|第二個數字)/.test(t)) s -= 3;
    if (/(接著|再來|下一個條件)/.test(t)) s -= 2;
    return s;
  }
  if (rowRole === "condition") {
    let s = 0;
    if (/(如果|判斷|是不是|條件|if|elif)/.test(t)) s += 4;
    if (/(回傳|return|結果)/.test(t)) s -= 2;
    return s;
  }
  if (rowRole === "input") {
    let s = 0;
    if (/(輸入|讀取|讀入|存進|input)/.test(t)) s += 4;
    if (/(回傳|結果|輸出|印出)/.test(t)) s -= 2;

    const varName = _extractAssignedVariable(row);
    const inputOrder = _inputOrdinalForRow(row);
    if (varName === "x" || inputOrder === 1) {
      if (/(第一個數字|第一個輸入|\bx\b|存進\s*x)/.test(t)) s += 3;
      if (/(第二個數字|第二個輸入|\by\b|存進\s*y)/.test(t)) s -= 2;
    }
    if (varName === "y" || inputOrder === 2) {
      if (/(第二個數字|第二個輸入|\by\b|存進\s*y)/.test(t)) s += 3;
      if (/(第一個數字|第一個輸入|\bx\b|存進\s*x)/.test(t)) s -= 2;
    }
    if (varName === "op" || varName === "operator" || inputOrder >= 3) {
      if (/(運算符號|operator|\bop\b|第三個輸入)/.test(t)) s += 3;
      if (/(第一個數字|第二個數字|\bx\b|\by\b)/.test(t)) s -= 1;
    }

    return s;
  }
  if (rowRole === "output") {
    let s = 0;
    if (/(輸出|印出|答案|結果|print)/.test(t)) s += 4;
    if (/(輸入|讀取)/.test(t)) s -= 2;
    return s;
  }
  return 0;
}

function _overlapQuality(s, e, ss, ee) {
  const overlap = Math.max(0, Math.min(e, ee) - Math.max(s, ss));
  if (overlap <= 0) return 0;
  const rowDur = Math.max(0.6, e - s);
  const segDur = Math.max(0.6, ee - ss);
  // Normalize overlap to prevent long clauses from dominating by sheer duration.
  return overlap / Math.min(rowDur, segDur);
}

function _rankClauseCandidatesForRange(row, startSec, endSec) {
  const clauseSegs = _getClauseSegments();
  const s = _toNumOrNull(startSec);
  const e = _toNumOrNull(endSec);
  if (!clauseSegs.length || s === null || e === null || e <= s) return [];

  const keywords = _buildRowKeywords(row);
  const rowRole = _rowRoleFromCode(row);
  const rowCenter = (s + e) / 2;
  const rowDur = Math.max(0.8, e - s);

  const hits = clauseSegs.filter((seg) => {
    const ss = _toNumOrNull(seg.start);
    const ee = _toNumOrNull(seg.end);
    if (ss === null || ee === null || ee <= ss) return false;
    return ee > s && ss < e;
  });
  if (!hits.length) return [];

  const ranked = [];
  for (const seg of hits) {
    const ss = Number(seg.start);
    const ee = Number(seg.end);
    const overlapScore = _overlapQuality(s, e, ss, ee);
    const kwScore = _keywordScore(seg.text, keywords);
    const roleScore = _roleCompatibilityScore(seg.text, rowRole, row);
    const transitionPenalty = _isTransitionSentence(seg.text) ? 1.8 : 0;
    const segCenter = (ss + ee) / 2;
    const centerGap = Math.abs(segCenter - rowCenter);
    const centerBonus = Math.max(-1.6, 1.2 - (centerGap / rowDur));
    const total = (overlapScore * 1.2) + (kwScore * 1.8) + (roleScore * 1.6) + centerBonus - transitionPenalty;
    ranked.push({ seg, total, kwScore, roleScore, transitionPenalty });
  }

  ranked.sort((a, b) => {
    if (b.total !== a.total) return b.total - a.total;
    const ad = Math.max(0.001, Number(a.seg.end) - Number(a.seg.start));
    const bd = Math.max(0.001, Number(b.seg.end) - Number(b.seg.start));
    return ad - bd;
  });

  return ranked;
}

function _pickBestClauseForRange(row, startSec, endSec) {
  const ranked = _rankClauseCandidatesForRange(row, startSec, endSec);
  return ranked.length ? ranked[0] : null;
}

function _sameWindowRange(aStart, aEnd, bStart, bEnd) {
  const as = _toNumOrNull(aStart);
  const ae = _toNumOrNull(aEnd);
  const bs = _toNumOrNull(bStart);
  const be = _toNumOrNull(bEnd);
  if (as === null || ae === null || bs === null || be === null) return false;
  return Math.abs(as - bs) <= 0.25 && Math.abs(ae - be) <= 0.25;
}

function _duplicateWindowRank(row) {
  const rows = Array.isArray(segmentEditorRows.value) ? segmentEditorRows.value : [];
  if (!rows.length) return 0;

  const slot = Number(row?.slot);
  const concept = String(row?.concept || "").trim().toLowerCase();
  const eff = getEffectiveRange(row);
  if (!Number.isFinite(slot)) return 0;

  let rank = 0;
  for (const prev of rows) {
    const prevSlot = Number(prev?.slot);
    if (!Number.isFinite(prevSlot) || prevSlot >= slot) continue;
    const prevConcept = String(prev?.concept || "").trim().toLowerCase();
    if (concept && prevConcept && prevConcept !== concept) continue;
    const prevEff = getEffectiveRange(prev);
    if (_sameWindowRange(eff.start, eff.end, prevEff.start, prevEff.end)) {
      rank += 1;
    }
  }
  return rank;
}

function getSubtitleAlignmentStatus(row) {
  const eff = getEffectiveRange(row);
  const pred = getPredictedRange(row);

  if (eff.source === "teacher" && !eff.valid) {
    return { mismatch: true, effBest: null, predBest: null };
  }

  const effBest = _pickBestClauseForRange(row, eff.start, eff.end);
  const predBest = _pickBestClauseForRange(row, pred.start, pred.end);

  let mismatch = false;
  if (effBest) {
    if (effBest.roleScore < 1 && effBest.kwScore < 2) mismatch = true;
    if (effBest.transitionPenalty > 0 && effBest.roleScore <= 0) mismatch = true;
  }

  if (effBest && predBest && (predBest.total >= effBest.total + 1.2)) {
    mismatch = true;
  }

  if (!effBest && predBest) mismatch = true;
  return { mismatch, effBest, predBest };
}

function applyPredictedRangeForRow(row) {
  const pred = getPredictedRange(row);
  const ps = _toNumOrNull(pred.start);
  const pe = _toNumOrNull(pred.end);
  if (ps === null || pe === null || pe <= ps) return;
  row.teacherStart = ps;
  row.teacherEnd = pe;
  row.subtitlePreview = _collectSubtitlePreview(row.teacherStart, row.teacherEnd);
  segmentEditorMessage.value = `slot ${row.slot} 已套用系統最終預測窗。`;
}

function buildSubtitleMatchLines(row) {
  const effective = getEffectiveRange(row);
  if (!effective.valid) return [];

  // Preview should show all subtitle segments overlapping the teacher window.
  // This makes teacher edits immediately verifiable and avoids hidden fallback behavior.
  const hits = _collectSubtitleOverlapSegments(effective.start, effective.end);
  if (!hits.length) return [];

  return hits.map((seg) => `[${_secToTC(seg.start)} - ${_secToTC(seg.end)}] ${String(seg.text || "").trim()}`);
}

function onTeacherRangeInput(row) {
  if (!row || typeof row !== "object") return;
  row.segSource = "teacher";
  const eff = getEffectiveRange(row);
  if (eff.valid) {
    row.subtitlePreview = _collectSubtitlePreview(eff.start, eff.end);
  } else {
    row.subtitlePreview = "";
  }
}

function _firstSubtitleRangeAfter(sec) {
  const segs = subtitleSegments.value || [];
  const anchor = _toNumOrNull(sec);
  if (!segs.length || anchor === null) return null;
  for (const seg of segs) {
    const ss = _toNumOrNull(seg?.start);
    const ee = _toNumOrNull(seg?.end);
    if (ss === null || ee === null || ee <= ss) continue;
    if (ss >= (anchor - 0.5)) {
      return { start: ss, end: ee };
    }
  }
  return null;
}

function _enforceMonotonicSegmentRows(rows) {
  const arr = Array.isArray(rows) ? rows : [];
  if (!arr.length) return 0;

  let fixed = 0;
  let prevEnd = null;

  for (const row of arr) {
    const s = _toNumOrNull(row?.teacherStart);
    const e = _toNumOrNull(row?.teacherEnd);
    if (s === null || e === null || e <= s) continue;

    if (prevEnd !== null && s + 0.2 < prevEnd) {
      // 僅修正 AI 來源，避免覆蓋老師手動儲存結果。
      if (row?.segSource === "teacher") {
        prevEnd = Math.max(prevEnd, e);
        continue;
      }

      const aiS = _toNumOrNull(row?.aiStart);
      const aiE = _toNumOrNull(row?.aiEnd);
      let nextRange = null;

      if (aiS !== null && aiE !== null && aiE > aiS && aiS >= (prevEnd - 0.5)) {
        nextRange = { start: aiS, end: aiE };
      } else {
        nextRange = _firstSubtitleRangeAfter(prevEnd);
      }

      if (nextRange && nextRange.end > nextRange.start) {
        row.teacherStart = nextRange.start;
        row.teacherEnd = nextRange.end;
        row.subtitlePreview = _collectSubtitlePreview(row.teacherStart, row.teacherEnd);
        fixed += 1;
      }
    }

    const ns = _toNumOrNull(row?.teacherStart);
    const ne = _toNumOrNull(row?.teacherEnd);
    if (ns !== null && ne !== null && ne > ns) {
      prevEnd = prevEnd === null ? ne : Math.max(prevEnd, ne);
    }
  }

  return fixed;
}




function _suggestRangeByNeighbors(row) {
  const segs = subtitleSegments.value || [];
  if (!segs.length) return null;

  const seed = _findSeedIndex(row);
  if (seed < 0) return null;

  const seedSeg = segs[seed] || {};
  const concept = _conceptFromCodeAndMap(row);
  const seedRole = _segmentRoleByText(seedSeg.text || "");

  let left = seed;
  let right = seed;

  for (let i = seed - 1; i >= 0; i--) {
    const seg = segs[i] || {};
    const txt = String(seg.text || "");
    const role = _segmentRoleByText(txt);
    if (_isTransitionSentence(txt)) break;
    if (!_conceptMatchText(txt, concept)) break;
    if (seedRole !== "any" && role !== "any" && role !== seedRole && !(seedRole === "condition" && role === "compute") && !(seedRole === "compute" && role === "condition")) {
      break;
    }
    left = i;
  }

  for (let i = seed + 1; i < segs.length; i++) {
    const seg = segs[i] || {};
    const txt = String(seg.text || "");
    const role = _segmentRoleByText(txt);
    if (_isTransitionSentence(txt)) break;
    if (!_conceptMatchText(txt, concept)) break;
    if (seedRole !== "any" && role !== "any" && role !== seedRole && !(seedRole === "condition" && role === "compute") && !(seedRole === "compute" && role === "condition")) {
      break;
    }
    right = i;
  }

  const start = _toNumOrNull(segs[left]?.start);
  const end = _toNumOrNull(segs[right]?.end);
  if (start === null || end === null || end <= start) return null;

  return {
    start,
    end,
    leftCount: seed - left,
    rightCount: right - seed,
    reason: `left ${seed - left}, right ${right - seed}`,
  };
}

function hasSuggestion(row) {
  const s = _toNumOrNull(row?.suggestStart);
  const e = _toNumOrNull(row?.suggestEnd);
  return s !== null && e !== null && e > s;
}

function refreshRowSuggestion(row) {
  const suggested = _suggestRangeByNeighbors(row);
  if (suggested) {
    row.suggestStart = suggested.start;
    row.suggestEnd = suggested.end;
    row.suggestReason = suggested.reason;
  } else {
    row.suggestStart = null;
    row.suggestEnd = null;
    row.suggestReason = "—";
  }
  row.subtitlePreview = _collectSubtitlePreview(row.teacherStart, row.teacherEnd);
}

function refreshNeighborSuggestions() {
  for (const row of segmentEditorRows.value) {
    refreshRowSuggestion(row);
  }
  segmentEditorMessage.value = "已更新鄰句建議（可逐格套用或全部套用）。";
}

function applySuggestionForRow(row) {
  if (!hasSuggestion(row)) return;
  row.teacherStart = row.suggestStart;
  row.teacherEnd = row.suggestEnd;
  row.subtitlePreview = _collectSubtitlePreview(row.teacherStart, row.teacherEnd);
}

function applyAllNeighborSuggestions() {
  let count = 0;
  for (const row of segmentEditorRows.value) {
    refreshRowSuggestion(row);
    if (hasSuggestion(row)) {
      row.teacherStart = row.suggestStart;
      row.teacherEnd = row.suggestEnd;
      row.subtitlePreview = _collectSubtitlePreview(row.teacherStart, row.teacherEnd);
      count += 1;
    }
  }
  segmentEditorMessage.value = count > 0
    ? `已套用 ${count} 格鄰句建議。`
    : "沒有可套用的鄰句建議。";
}

function snapRowToNextSentence(row) {
  const segs = subtitleSegments.value || [];
  if (!segs.length) return;
  const endNow = _toNumOrNull(row.teacherEnd);
  if (endNow === null) return;
  const next = segs.find((seg) => {
    const ss = _toNumOrNull(seg.start);
    return ss !== null && ss > endNow;
  });
  if (!next) return;
  const nend = _toNumOrNull(next.end);
  if (nend === null) return;
  row.teacherEnd = nend;
  row.subtitlePreview = _collectSubtitlePreview(row.teacherStart, row.teacherEnd);
  segmentEditorMessage.value = `slot ${row.slot} 已對齊到下一句字幕邊界。`;
}

function initSegmentEditorRows() {
  const pd = previewData.value || {};
  const blocks = Array.isArray(pd.parsons_blocks) ? pd.parsons_blocks : [];
  const aiMap = pd.ai_segment_map || {};
  const teacherMap = pd.teacher_segment_map || {};
  const slotConceptMap = (pd.slot_concept_map && typeof pd.slot_concept_map === "object") ? pd.slot_concept_map : {};
  const aiSlotConceptMap = (pd.ai_slot_hints_concept && typeof pd.ai_slot_hints_concept === "object") ? pd.ai_slot_hints_concept : {};

  const readConcept = (idx, codeText) => {
    const keys = [String(idx), `s${idx + 1}`, `第${idx + 1}格`];
    let raw = "";
    for (const k of keys) {
      const v1 = String(slotConceptMap[k] || "").trim().toLowerCase();
      if (v1 && v1 !== "unknown") {
        raw = v1;
        break;
      }
      const v2 = String(aiSlotConceptMap[k] || "").trim().toLowerCase();
      if (v2 && v2 !== "unknown") {
        raw = v2;
        break;
      }
    }
    const rowLike = { concept: raw, code: String(codeText || "") };
    const fallback = _conceptFromCodeAndMap(rowLike);
    return fallback || "unknown";
  };

  const rows = blocks.map((b, idx) => {
    const ai = _readSlotSeg(aiMap, idx);
    const teacher = _readSlotSeg(teacherMap, idx);
    const hasTeacher = teacher.start !== null && teacher.end !== null && teacher.end > teacher.start;
    const codeText = String(b?.text || "");
    return {
      slot: idx,
      code: codeText,
      concept: readConcept(idx, codeText),
      aiStart: ai.start,
      aiEnd: ai.end,
      teacherStart: teacher.start ?? ai.start,
      teacherEnd: teacher.end ?? ai.end,
      segSource: hasTeacher ? "teacher" : "ai",
      suggestStart: null,
      suggestEnd: null,
      suggestReason: "—",
      subtitlePreview: "",
    };
  });

  const fixedCount = _enforceMonotonicSegmentRows(rows);
  segmentEditorRows.value = rows;
  segmentEditorMessage.value = fixedCount > 0
    ? `偵測到 ${fixedCount} 格秒數倒退，已先套用順序修正（僅 AI 來源）。可直接按「儲存老師校正秒數」。`
    : "";
}

function resetSegmentEditorFromAI() {
  for (const row of segmentEditorRows.value) {
    row.teacherStart = row.aiStart;
    row.teacherEnd = row.aiEnd;
  }
  segmentEditorMessage.value = "已重設為 AI 對齊秒數。";
}

async function saveConceptOverride() {
  const taskId = segmentEditorTaskId.value || previewData.value?.meta?.task_id;
  if (!taskId) return;

  const validChapters = [];
  for (const ch of (previewData.value.teacher_concept_chapters || [])) {
    const s = _toNumOrNull(ch.start);
    const e = _toNumOrNull(ch.end);
    if (s === null || e === null || e <= s) continue;
    validChapters.push({
      concept: String(ch.concept || "unknown"),
      start: s,
      end: e
    });
  }

  if (!validChapters.length) {
    segmentEditorMessage.value = "沒有可儲存的有效秒數（請確認 end > start）。";
    return;
  }

  segmentEditorSaving.value = true;
  segmentEditorMessage.value = "";
  try {
    const { data } = await parsonsPost("/fixed_task/save_teacher_concept_chapters", {
      task_id: taskId,
      chapters: validChapters,
    });
    if (!data?.ok) {
      throw new Error(data?.message || "儲存失敗");
    }
    segmentEditorMessage.value = `已儲存 ${validChapters.length} 筆老師概念段落。`;
    await loadSegmentEditorTask(taskId);
  } catch (e) {
    segmentEditorMessage.value = `儲存失敗：${e?.response?.data?.message || e?.message || "unknown"}`;
  } finally {
    segmentEditorSaving.value = false;
  }
}

function findQuestionByTaskId(taskId) {
  const tid = String(taskId || "").trim();
  return (questions.value || []).find((q) => String(q?.task_id || "") === tid) || null;
}

async function loadSegmentEditorTask(taskId) {
  const tid = String(taskId || "").trim();
  if (!tid) return false;

  segmentEditorTaskId.value = tid;
  segmentEditorMessage.value = "";

  try {
    const { data } = await t5Get("/question", {
      params: { task_id: tid }
    });
    if (!data?.ok) {
      throw new Error(data?.error || "讀取題目失敗");
    }

    if (String(data?.task_id || "") && String(data.task_id) !== tid) {
      throw new Error(`載入版本不一致（預期 ${tid}，實際 ${data.task_id}）`);
    }

    const selectedQ = findQuestionByTaskId(tid);

    if (!previewData.value) previewData.value = {};
    const mapBlocks = (arr = []) =>
      (arr || []).map((b, idx) => ({
        id: String(b.id ?? b._id ?? `b${idx}`),
        text: b.text || b.code || b.line || "",
        meaning_zh: b.semantic_zh || b.semantic || b.zh || "",
        enabled: b?.enabled !== false,
        indent: Number.isFinite(Number(b.indent))
          ? Number(b.indent)
          : (String(b.text || b.code || b.line || "").length - String(b.text || b.code || b.line || "").replace(/^\s+/, "").length)
      }));

    previewData.value.source_type = (selectedQ?.source_type || data.source_type || data.gen_source || "fixed").toString().toLowerCase();
    previewData.value.meta = {
      ...(previewData.value.meta || {}),
      task_id: data.task_id,
      version: selectedQ?.version || data.version || "—",
      unit: selectedUnit.value || "—",
      title: selectedVideoTitle.value || "—",
      segment_label: data.segment_label || "—",
      subtitle_version: selectedSubtitleVersion.value || "—",
      status: data.status_zh || data.status || "—",
      enabled: !!data.student_visible,
      created_at: data.created_at || "—",
      created_by: "AI Agent"
    };
    previewData.value.parsons_blocks = mapBlocks(data.solution_blocks);

    // 從 /question API 直接帶回章節欄位，確保重新開啟可讀到已儲存正式章節。
    previewData.value.concept_chapters_formal = data.concept_chapters_formal || [];
    previewData.value.concept_chapters_draft = data.concept_chapters_draft || [];
    previewData.value.teacher_concept_chapters = data.teacher_concept_chapters || [];
    previewData.value.concept_chapters_warnings = data.concept_chapters_warnings || [];
    previewData.value.concept_align_status = data.concept_align_status || "draft";
    previewData.value.teaching_range_start = data.teaching_range_start;
    previewData.value.teaching_range_end = data.teaching_range_end;
    previewData.value.code_start_ts = data.code_start_ts;
    previewData.value.block_chapter_map = data.block_chapter_map || {};
    previewData.value.chapter_recommendations = data.chapter_recommendations || [];

    const dbg = await parsonsGet("/fixed_task/debug", {
      params: { task_id: tid },
    });
    if (dbg?.data?.ok) {
      previewData.value.ai_segment_map = dbg.data.ai_segment_map || {};
      previewData.value.teacher_segment_map = dbg.data.teacher_segment_map || {};
      previewData.value.slot_concept_map = dbg.data.slot_concept_map || {};
      previewData.value.ai_slot_hints_concept = dbg.data.ai_slot_hints_concept || {};
      previewData.value.concept_segment_map = dbg.data.concept_segment_map || {};
      previewData.value.teacher_concept_segment_map = dbg.data.teacher_concept_segment_map || {};
      subtitleSegments.value = Array.isArray(dbg.data.subtitle_segments) ? dbg.data.subtitle_segments : [];
      segmentEditorDebug.value = dbg.data.latest_attempt_debug || null;
      if (!Array.isArray(previewData.value.teacher_concept_chapters) || !previewData.value.teacher_concept_chapters.length) {
        previewData.value.teacher_concept_chapters = dbg.data.teacher_concept_chapters || dbg.data.concept_chapters_draft || [];
      }
    } else {
      previewData.value.ai_segment_map = {};
      previewData.value.teacher_segment_map = {};
      previewData.value.slot_concept_map = {};
      previewData.value.ai_slot_hints_concept = {};
      previewData.value.concept_segment_map = {};
      previewData.value.teacher_concept_segment_map = {};
      subtitleSegments.value = [];
      segmentEditorDebug.value = null;
      if (!Array.isArray(previewData.value.teacher_concept_chapters)) {
        previewData.value.teacher_concept_chapters = [];
      }
    }

    try {
      const pw = await parsonsGet("/fixed_task/predicted_windows", {
        params: { task_id: tid },
      });
      const list = Array.isArray(pw?.data?.predicted_windows) ? pw.data.predicted_windows : [];
      const out = {};
      for (const item of list) {
        if (!item || typeof item !== "object") continue;
        const idx = Number(item.slot);
        if (!Number.isFinite(idx)) continue;
        out[String(idx)] = item;
      }
      predictedWindowMap.value = out;
    } catch (_) {
      predictedWindowMap.value = {};
    }

    initSegmentEditorRows();
    return true;
  } catch (e) {
    segmentEditorRows.value = [];
    subtitleSegments.value = [];
    segmentEditorDebug.value = null;
    predictedWindowMap.value = {};
    segmentEditorMessage.value = `載入失敗：${e?.response?.data?.message || e?.message || "unknown"}`;
    return false;
  }
}

async function loadPageSegmentEditor() {
  if (!selectedFixedTaskId.value) return;
  const pickedId = String(selectedFixedTaskId.value || "").trim();
  pageSegmentEditorLoading.value = true;
  const ok = await loadSegmentEditorTask(pickedId);
  if (ok) {
    const q = findQuestionByTaskId(pickedId);
    segmentEditorMessage.value = `已載入：${(q?.version || "—")}（task_id: ${pickedId}）`;
  }
  pageSegmentEditorLoading.value = false;
}

function boolText(v) {
  if (v === true) return "yes";
  if (v === false) return "no";
  return "—";
}

function fmtRatio(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${Math.round(n * 100)}%`;
}

function mark(v) {
  if (v === true) return "✅";
  if (v === false) return "❌";
  return "—";
}


async function openPreview(row = null) {
  console.log("[openPreview] row =", row);

  modal.open = true;
  modal.loading = true;
  modal.err = "";
  previewData.value = null;

  try {
    const taskId = row?.task_id;
    console.log("[openPreview] task_id =", taskId);

    if (!taskId) {
      throw new Error("這一列沒有 task_id（D 區塊列表資料缺少 task_id）");
    }

    const { data } = await t5Get("/question", {
      params: { task_id: taskId }
    });

    console.log("[openPreview] api data =", data);

    if (!data?.ok) {
      throw new Error(data?.error || "讀取題目失敗");
    }

    const question = data.question || {};
    const prompt = question.prompt || question.title || question.text || "";


    const mapBlocks = (arr = []) =>
      (arr || []).map((b, idx) => ({
        id: String(b.id ?? b._id ?? `b${idx}`),
        text: b.text || b.code || b.line || "",
        meaning_zh: b.semantic_zh || b.semantic || b.zh || "",
        enabled: b?.enabled !== false,
        indent: Number.isFinite(Number(b.indent))
          ? Number(b.indent)
          : (String(b.text || b.code || b.line || "").length - String(b.text || b.code || b.line || "").replace(/^\s+/, "").length)
      }));

    const solutionOrderText = Array.isArray(data.solution_order)
      ? data.solution_order.join(" → ")
      : (data.solution_order || "");

    previewData.value = {
      ok: true,
      source_type: (row?.source_type || data.source_type || data.gen_source || "ai").toString().toLowerCase(),
      meta: {
        task_id: data.task_id,
        version: data.version || row.version || "—",
        unit: selectedUnit.value || "—",
        title: selectedVideoTitle.value || "—",
        segment_label: data.segment_label || "—",
        subtitle_version: selectedSubtitleVersion.value || "—",
        status: data.status_zh || data.status || "—",
        enabled: !!data.student_visible,
        created_at: data.created_at || "—",
        created_by: "AI Agent"
      },
      prompt,
      key_sentences: Array.isArray(data.key_sentences) ? data.key_sentences : [],
      key_sentences_typed: Array.isArray(data.key_sentences_typed) ? data.key_sentences_typed : [],
      selector_meta: (data.selector_meta && typeof data.selector_meta === "object") ? data.selector_meta : {},
      unified_policy_meta: (data.unified_policy_meta && typeof data.unified_policy_meta === "object") ? data.unified_policy_meta : {},
      function_profile: (data.function_profile && typeof data.function_profile === "object") ? data.function_profile : {},
      alignment_confidence: (data.alignment_confidence && typeof data.alignment_confidence === "object") ? data.alignment_confidence : {},
      parsons_blocks: mapBlocks(data.solution_blocks),
      distractor_blocks: mapBlocks(data.distractor_blocks),
      solution_order_text: solutionOrderText
    };

    await loadSegmentEditorTask(data.task_id);

    resetDistractorKeep(previewData.value?.distractor_blocks || []);

    hideSemanticZh.value = !!data.hide_semantic_zh;

    reviewForm.tags = data.review_tags || [];
    reviewForm.note = data.review_note || "";

  } catch (e) {
    console.error("[openPreview] error =", e);
    modal.err = e?.message || "讀取預覽失敗（請看 console）";
  } finally {
    modal.loading = false;
  }
}

// 老師審核用：從 previewData 計算出正確答案區塊的詳細資訊（包含中文語意）
const solutionDetailList = computed(() => {
  // blocks 來源：你目前預覽用的是 parsons_blocks（保底也支援 solution_blocks）
  const blocks =
    previewData.value?.parsons_blocks ||
    previewData.value?.solution_blocks ||
    [];

  // order 來源 1：如果後端有給 solution_order（array）
  let order =
    previewData.value?.solution_order ||
    previewData.value?.solution_ids ||
    [];

  // order 來源 2：如果只有 solution_order_text（像 "b1 → b2 → b3 → b4"）
  if (!Array.isArray(order) || order.length === 0) {
    const s = String(previewData.value?.solution_order_text || "").trim();

    // 同時支援 "→" 或 "->"
    order = s
      .split(/→|->/g)
      .map(x => x.trim())
      .filter(Boolean);
  }

  const map = new Map(blocks.map(b => [String(b.id), b]));

  const rows = (order || []).map((id, idx) => {
    const b = map.get(String(id));
    return {
      idx: idx + 1,
      id: String(id),
      text: b?.text || "（找不到對應區塊）",
      indent: Number.isFinite(Number(b?.indent)) ? Number(b?.indent) : 0,
      meaning_zh: b?.meaning_zh || "",
    };
  });

  // 若後端 text 已去除前導空白且 indent 也缺失，前端用程式結構推回縮排層級。
  let level = 0;
  return rows.map((r) => {
    const line = String(r?.text || "").trim();
    const lower = line.toLowerCase();

    if (/^(elif\b|else\s*:|except\b|finally\s*:)/.test(lower)) {
      level = Math.max(0, level - 1);
    }

    const fallbackIndent = level * 4;
    const finalIndent = Number(r?.indent || 0) > 0 ? Number(r.indent) : fallbackIndent;

    if (/:\s*$/.test(line) && !/^#/.test(line)) {
      level += 1;
    }

    return {
      ...r,
      indent: finalIndent,
      indent_level: Math.max(0, Math.round(finalIndent / 4)),
    };
  });
});

function formatTeacherCodeLine(row) {
  const raw = String(row?.text || "").replace(/\t/g, "    ");
  if (!raw) return "（空）";

  const rawIndent = Number.isFinite(Number(row?.indent))
    ? Number(row.indent)
    : (raw.length - raw.replace(/^\s+/, "").length);

  if (teacherIndentMode.value === "raw") {
    if (rawIndent > 0 && raw.length === raw.trimStart().length) {
      return " ".repeat(rawIndent) + raw;
    }
    return raw;
  }

  const unit = Number(teacherIndentMode.value);
  const trimmed = raw.trimStart();
  const level = Math.max(0, Math.round(rawIndent / 4));
  const displayIndent = " ".repeat(level * (Number.isFinite(unit) ? unit : 4));
  return displayIndent + trimmed;
}

function getIndentLevel(row) {
  const n = Number(row?.indent_level);
  if (Number.isFinite(n) && n >= 0) return n;
  const rawIndent = Number(row?.indent || 0);
  return Math.max(0, Math.round(rawIndent / 4));
}

function indentGuidePrefix(row) {
  const level = getIndentLevel(row);
  if (level <= 0) return "";
  return "│ ".repeat(level);
}


// review in modal
const reviewTagOptions = [
  "題幹過長",
  "中文語意提示不清楚",
  "干擾選項不清楚",
  "題目難度過高",
  "其他"
];
const reviewTags = ref([]);
const reviewNote = ref("");
const dKeep = reactive({}); // {block_id: true/false}

// ===== computed =====
const selectedVideoTitle = computed(() => {
  const v = videos.value.find(x => x.id === selectedVideo.value);
  return v?.title || "—";
});

const editableQuestions = computed(() => {
  return (questions.value || []).filter((q) => !!q?.task_id);
});

const modalPrompt = computed(() => {
  const q = modal.data?.question || {};
  return q.prompt || q.title || q.text || "（未提供題目敘述）";
});

const solutionBlocks = computed(() => {
  const arr = modal.data?.solution_blocks || [];
  return arr.map((b, idx) => ({
    id: String(b.id ?? b._id ?? `s${idx}`),
    _key: `s-${idx}-${b.id ?? ""}`,
    code: b.code || b.text || b.line || "",
    semantic_zh: b.semantic_zh || b.semantic || b.zh || ""
  }));
});

const distractorBlocks = computed(() => {
  const arr = modal.data?.distractor_blocks || [];
  return arr.map((b, idx) => ({
    id: String(b.id ?? b._id ?? `d${idx}`),
    _key: `d-${idx}-${b.id ?? ""}`,
    code: b.code || b.text || b.line || "",
    semantic_zh: b.semantic_zh || b.semantic || b.zh || ""
  }));
});

const answerText = computed(() => {
  const so = modal.data?.solution_order;
  if (Array.isArray(so)) return so.join(" → ");
  if (typeof so === "string") return so;
  // fallback: 用區塊 code 拼成一行
  return solutionBlocks.value.map(b => b.code).join(" → ");
});

// ===== navigation =====
function goVideoManage() {
  router.push({ name: "TeacherVideoManage" });
}
function goSubtitleCheck() {
  router.push({ name: "TeacherSubtitles" });
}

async function deleteSelectedVideo() {
  const videoId = String(selectedVideo.value || "").trim();
  const title = String(selectedVideoTitle.value || "").trim();
  if (!videoId) return;

  const ok = window.confirm(`確認要刪除影片「${title || videoId}」嗎？\n\n此操作為軟刪除，老師端與學生端都將不再顯示該影片。`);
  if (!ok) return;

  try {
    await api.patch(`/api/admin_upload/video/${encodeURIComponent(videoId)}/delete`, {
      deleted_by: "teacher",
    });

    // 先清掉目前依賴選中影片的頁面狀態，避免殘留舊資料。
    selectedVideo.value = "";
    questions.value = [];
    previewData.value = null;
    selectedFixedTaskId.value = "";
    segmentEditorRows.value = [];
    segmentEditorTaskId.value = "";
    segmentEditorDebug.value = null;
    predictedWindowMap.value = {};

    await fetchVideos(selectedUnit.value);

    if (!videos.value.length) {
      await fetchUnits();
      const stillExists = units.value.some((u) => String(u.id || "") === String(selectedUnit.value || ""));
      if (!stillExists) {
        selectedUnit.value = "";
      }
    }

    segmentEditorMessage.value = `已刪除影片：${title || videoId}`;
  } catch (e) {
    err.a = `刪除失敗：${e?.response?.data?.message || e?.message || "unknown"}`;
  }
}

// ===== helpers =====
function fmtTime(iso) {
  if (!iso) return "";

  // ✅ 如果沒有 Z 或 +08:00，代表它是「假UTC」→ 補 Z
  const fixedIso = /[zZ]|[+\-]\d{2}:\d{2}$/.test(iso)
    ? iso
    : iso + "Z";

  const d = new Date(fixedIso);

  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(d).replace(" ", " ");
}


// [新增] 將同一影片的題目依「生成時間（舊→新）」編號為 v1, v2, v3…（不改 DB schema）
function applyVersionSeq(list) {
  const arr = Array.isArray(list) ? [...list] : [];
  // created_at 可能是 ISO 字串；用 Date 排序（舊→新）
  arr.sort((a, b) => new Date(a?.created_at || 0) - new Date(b?.created_at || 0));
  const id2v = {};
  arr.forEach((q, i) => {
    const v = `v${i + 1}`;
    id2v[q.task_id] = v;
    q.version = v;
  });
  // 若後端有 parent_task_id，但沒有 parent_version，這裡補上顯示用的 parent_version
  arr.forEach((q) => {
    if (!q.parent_version && q.parent_task_id && id2v[q.parent_task_id]) {
      q.parent_version = id2v[q.parent_task_id];
    }
  });
  return arr;
}

function dotClass(status) {
  return {
    draft: "dot-gray",
    pending: "dot-yellow",
    approved: "dot-blue",
    published: "dot-green",
    rejected: "dot-red"
  }[status] || "dot-yellow";
}

function closeModal() {
  modal.open = false;
  modal.loading = false;
  modal.err = "";
  modal.data = null;
  reviewTags.value = [];
  reviewNote.value = "";
  Object.keys(dKeep).forEach(k => delete dKeep[k]);
}

// ===== API =====
async function fetchUnits() {
  err.a = "";
  loading.units = true;
  try {
    const { data } = await t5Get("/units");
    units.value = data.items || [];
  } catch {
    err.a = "讀取單元失敗，請稍後再試。";
  } finally {
    loading.units = false;
  }
}

async function fetchVideos(unitId) {
  err.a = "";
  loading.videos = true;
  videos.value = [];
  selectedVideo.value = "";
  try {
    const { data } = await t5Get("/videos", { params: { unit_id: unitId } });
    const raw = Array.isArray(data?.items) ? data.items : [];
    videos.value = raw.filter((v) => !(v?.deleted === true || v?.is_deleted === true));
  } catch {
    err.a = "讀取影片列表失敗。";
  } finally {
    loading.videos = false;
  }
}

async function fetchVideoInfo(videoId) {
  err.a = "";
  loading.videoInfo = true;
  try {
    const { data } = await t5Get("/video_info", { params: { video_id: videoId } });
    Object.assign(videoInfo, data);

    if (videoInfo.subtitle_versions?.length) {
      selectedSubtitleVersion.value = videoInfo.subtitle_versions[0];
    } else {
      selectedSubtitleVersion.value = "";
    }
  } catch {
    err.a = "讀取影片資訊失敗。";
  } finally {
    loading.videoInfo = false;
  }
}

async function fetchQuestions() {
  err.d = "";
  if (!selectedVideo.value) return;
  loading.questions = true;
  try {
    const { data } = await t5Get("/questions", {
      params: {
        video_id: selectedVideo.value,
        status: filterStatus.value,
        sort: sortOrder.value
      }
    });
    const rawItems = Array.isArray(data?.items) ? data.items : [];
    const safeItems = rawItems.filter((q) => !(
      q?.deleted === true
      || q?.is_deleted === true
      || q?.video_deleted === true
      || q?.video_is_deleted === true
    ));

    questions.value = safeItems.map((q, idx) => ({
      ...q,
      source_type: q.source_type || q.gen_source || "ai",
      review_status: q.review_status || q.status || "pending",
      task_code: q.task_code || (q.gen_source === "fixed" ? "FIXED-01" : ""),
      enabled: typeof q.enabled === "boolean" ? q.enabled : (q.status === "published")
    }));

// [新增] 套用版本序號 v1/v2…（依生成時間舊→新，不改 DB）
questions.value = applyVersionSeq(questions.value);

if (!selectedFixedTaskId.value || !editableQuestions.value.some((q) => q.task_id === selectedFixedTaskId.value)) {
  selectedFixedTaskId.value = editableQuestions.value[0]?.task_id || "";
}

  } catch {
    err.d = "伺服器連線失敗";
  } finally {
    loading.questions = false;
  }
}

async function regenerate() {
  err.e = "";
  if (!selectedVideo.value) return;
  busy.regen = true;
  try {
    await t5Post("/regenerate", {
      video_id: selectedVideo.value,
      level: "L1", // 不做適性化：固定
      subtitle_version: selectedSubtitleVersion.value || null,
      stable: stableMode.value,
    });
    await fetchQuestions();
  } catch {
    err.e = "重新生成失敗，請稍後再試。";
  } finally {
    busy.regen = false;
  }
}

async function saveReviewOnly() {
  if (!modal.data?.task_id) return;
  const payload = {
    task_id: modal.data.task_id,
    review_tags: reviewTags.value,
    review_note: reviewNote.value,
    distractor_keep: { ...dKeep },
    hide_semantic_zh: !!hideSemanticZh.value,
  };
  await t5Post("/question/review_save", payload);
}

async function publish(row) {
  await t5Post("/question/publish", { task_id: row.task_id });
  await fetchQuestions();
}

async function unpublish(row) {
  await t5Post("/question/unpublish", { task_id: row.task_id });
  await fetchQuestions();
}

// 新增：系統判斷章節（固定題 / AI 題皆可用）
function _mapSystemChapterRows(data, options = {}) {
  const preferDraft = Boolean(options?.preferDraft);
  const systemDrafts = Array.isArray(data?.concept_chapters_draft) ? data.concept_chapters_draft : [];
  const formalChapters = Array.isArray(data?.concept_chapters_formal) ? data.concept_chapters_formal : [];
  const teacherChapters = Array.isArray(data?.teacher_concept_chapters) ? data.teacher_concept_chapters : [];
  const recommendations = Array.isArray(data?.subtitle_health?.chapter_recommendations)
    ? data.subtitle_health.chapter_recommendations
    : [];

  if (preferDraft && systemDrafts.length) {
    return systemDrafts.map((ch, idx) => ({
      cell_id: Number(ch?.cell_id ?? idx + 1),
      concept_tag: String(ch?.concept_tag || ch?.concept || "").trim(),
      concept_label: String(ch?.concept_label || ch?.chapter_label || ch?.concept || `章節 ${idx + 1}`),
      concept: String(ch?.concept_label || ch?.concept || ch?.concept_tag || `章節 ${idx + 1}`),
      start: Number(ch?.start ?? ch?.start_sec ?? 0),
      end: Number(ch?.end ?? ch?.end_sec ?? 0),
      source: String(ch?.source || ch?.chapter_source || "").trim(),
      chapter_key: ch?.chapter_key || `draft_${idx + 1}`,
      chapter_label: String(ch?.chapter_label || ch?.concept_label || ch?.concept || ""),
      ai_chapter_title: String(ch?.ai_chapter_title || ""),
      ai_chapter_note: String(ch?.ai_chapter_note || ch?.ai_reason || ""),
      chapter_name_candidates: Array.isArray(ch?.chapter_name_candidates) ? ch.chapter_name_candidates : [],
      concept_tags: Array.isArray(ch?.concept_tags) ? ch.concept_tags : [],
      chapter_source: ch?.chapter_source || "concept_chapters_draft",
    }));
  }

  if (formalChapters.length) {
    return formalChapters.map((ch, idx) => ({
      cell_id: Number(ch?.cell_id ?? idx + 1),
      concept_tag: String(ch?.concept_tag || ch?.concept || "").trim(),
      concept_label: String(ch?.concept_label || ch?.chapter_label || ch?.concept || `章節 ${idx + 1}`),
      concept: String(ch?.concept_label || ch?.concept || ch?.concept_tag || `章節 ${idx + 1}`),
      start: Number(ch?.start),
      end: Number(ch?.end),
      source: String(ch?.source || ch?.chapter_source || "").trim(),
      chapter_key: ch?.chapter_key || `formal_${idx + 1}`,
      chapter_label: String(ch?.chapter_label || ch?.concept_label || ch?.concept || ""),
      ai_chapter_title: String(ch?.ai_chapter_title || ""),
      ai_chapter_note: String(ch?.ai_chapter_note || ch?.ai_reason || ""),
      chapter_name_candidates: Array.isArray(ch?.chapter_name_candidates) ? ch.chapter_name_candidates : [],
      concept_tags: Array.isArray(ch?.concept_tags) ? ch.concept_tags : [],
      chapter_source: ch?.chapter_source || "concept_chapters_formal",
    }));
  }

  if (teacherChapters.length) {
    return teacherChapters.map((ch, idx) => ({
      cell_id: Number(ch?.cell_id ?? idx + 1),
      concept_tag: String(ch?.concept_tag || ch?.concept || "").trim(),
      concept_label: String(ch?.concept_label || ch?.chapter_label || ch?.concept || `章節 ${idx + 1}`),
      concept: String(ch?.concept_label || ch?.concept || ch?.concept_tag || `章節 ${idx + 1}`),
      start: Number(ch?.start ?? ch?.start_sec ?? 0),
      end: Number(ch?.end ?? ch?.end_sec ?? 0),
      source: String(ch?.source || ch?.chapter_source || "").trim(),
      chapter_key: ch?.chapter_key || `teacher_${idx + 1}`,
      chapter_label: String(ch?.chapter_label || ch?.concept_label || ch?.concept || ""),
      ai_chapter_title: String(ch?.ai_chapter_title || ""),
      ai_chapter_note: String(ch?.ai_chapter_note || ch?.ai_reason || ""),
      chapter_name_candidates: Array.isArray(ch?.chapter_name_candidates) ? ch.chapter_name_candidates : [],
      concept_tags: Array.isArray(ch?.concept_tags) ? ch.concept_tags : [],
      chapter_source: ch?.chapter_source || "teacher_concept_chapters",
    }));
  }

  if (systemDrafts.length) {
    return systemDrafts.map((ch, idx) => ({
      cell_id: Number(ch?.cell_id ?? idx + 1),
      concept_tag: String(ch?.concept_tag || ch?.concept || "").trim(),
      concept_label: String(ch?.concept_label || ch?.chapter_label || ch?.concept || `章節 ${idx + 1}`),
      concept: String(ch?.concept_label || ch?.concept || ch?.concept_tag || `章節 ${idx + 1}`),
      start: Number(ch?.start ?? ch?.start_sec ?? 0),
      end: Number(ch?.end ?? ch?.end_sec ?? 0),
      source: String(ch?.source || ch?.chapter_source || "").trim(),
      chapter_key: ch?.chapter_key || `draft_${idx + 1}`,
      chapter_label: String(ch?.chapter_label || ch?.concept_label || ch?.concept || ""),
      ai_chapter_title: String(ch?.ai_chapter_title || ""),
      ai_chapter_note: String(ch?.ai_chapter_note || ch?.ai_reason || ""),
      chapter_name_candidates: Array.isArray(ch?.chapter_name_candidates) ? ch.chapter_name_candidates : [],
      concept_tags: Array.isArray(ch?.concept_tags) ? ch.concept_tags : [],
      chapter_source: ch?.chapter_source || "concept_chapters_draft",
    }));
  }

  const fallbackRows = teacherChapters.length ? teacherChapters : recommendations;

  return fallbackRows.map((ch, idx) => ({
    cell_id: Number(ch?.cell_id ?? idx + 1),
    concept_tag: String(ch?.concept_tag || ch?.concept || "").trim(),
    concept_label: String(ch?.concept_label || ch?.chapter_label || ch?.concept || `章節 ${idx + 1}`),
    concept: String(ch?.concept_label || ch?.concept || ch?.concept_tag || `章節 ${idx + 1}`),
    start: Number(ch?.start ?? ch?.start_sec ?? ch?.slot_start ?? 0),
    end: Number(ch?.end ?? ch?.end_sec ?? ch?.slot_end ?? 0),
    source: String(ch?.source || ch?.chapter_source || "").trim(),
    chapter_key: ch?.chapter_key || `draft_${idx + 1}`,
    chapter_label: String(ch?.chapter_label || ch?.concept_label || ch?.concept || ""),
    ai_chapter_title: String(ch?.ai_chapter_title || ""),
    ai_chapter_note: String(ch?.ai_chapter_note || ch?.ai_reason || ""),
    chapter_name_candidates: Array.isArray(ch?.chapter_name_candidates) ? ch.chapter_name_candidates : [],
    concept_tags: Array.isArray(ch?.concept_tags) ? ch.concept_tags : [],
    chapter_source: ch?.chapter_source || (teacherChapters.length ? "teacher_concept_chapters" : "subtitle_health.chapter_recommendations"),
  }));
}

function openConceptDraftModal(taskId, data) {
  const rows = _mapSystemChapterRows(data, {
    preferDraft: String(data?.mode || data?.teaching_range_mode || "").trim().toLowerCase() === "teacher_range_only"
      || Boolean(data?.forced_rebuild),
  });
  const teachingRangeWarning = data?.teaching_range_warning || null;
  const preserveExistingDraft = Boolean(teachingRangeWarning) && Array.isArray(conceptDraftModal.chapters) && conceptDraftModal.chapters.length > 0;
  conceptDraftModal.taskId = taskId;
  conceptDraftModal.selectedChapterIndex = preserveExistingDraft && conceptDraftModal.chapters.length ? 0 : (rows.length ? 0 : null);
  conceptDraftModal.teachingRangeStart = data?.teaching_range_start !== undefined && data?.teaching_range_start !== null
    ? _formatTimeInputValue(data.teaching_range_start)
    : (conceptDraftModal.teachingRangeStart || "");
  conceptDraftModal.teachingRangeEnd = data?.teaching_range_end !== undefined && data?.teaching_range_end !== null
    ? _formatTimeInputValue(data.teaching_range_end)
    : (conceptDraftModal.teachingRangeEnd || "");
  conceptDraftModal.teachingRange = data?.teaching_range || conceptDraftModal.teachingRange || null;
  conceptDraftModal.teachingRangeMode = data?.mode || data?.teaching_range_mode || conceptDraftModal.teachingRangeMode || "";
  conceptDraftModal.teachingRangeSegments = Array.isArray(data?.teaching_range_segments)
    ? data.teaching_range_segments
    : _filterSegmentsByRange(subtitleSegments.value || [], conceptDraftModal.teachingRange || _getTeachingRangeBounds());
  conceptDraftModal.teachingRangeWarning = teachingRangeWarning || null;
  conceptDraftModal.teachingRangeRecommendedRange = data?.teaching_range_recommended_range || conceptDraftModal.teachingRangeRecommendedRange || conceptDraftModal.teachingRange || _getTeachingRangeBounds();
  conceptDraftModal.teachingRangeEffective = data?.teaching_range_effective !== false;
  conceptDraftModal.codeStartTs = data?.code_start_ts !== undefined && data?.code_start_ts !== null
    ? _formatTimeInputValue(data.code_start_ts)
    : (conceptDraftModal.codeStartTs || "");
  conceptDraftModal.codeSummaryRange = data?.code_summary_range || conceptDraftModal.codeSummaryRange || null;
  conceptDraftModal.codeSummarySegments = Array.isArray(data?.code_summary_segments)
    ? data.code_summary_segments
    : _filterSegmentsByRange(subtitleSegments.value || [], conceptDraftModal.codeSummaryRange || _getCodeSummaryRangeBounds());
  conceptDraftModal.codeStartWarning = data?.code_start_warning || null;
  conceptDraftModal.codeStartRecommendedRange = data?.code_start_recommended_range || conceptDraftModal.codeStartRecommendedRange || conceptDraftModal.codeSummaryRange || _getCodeSummaryRangeBounds();
  conceptDraftModal.codeStartEffective = data?.code_start_effective !== false;
  conceptDraftModal.preservedPreviousDraft = Boolean(data?.preserved_previous_draft);
  const nextChapters = (Array.isArray(rows) ? rows : []).map((ch) => ({
    ...ch,
    start: _secToTC(ch.start),
    end: _secToTC(ch.end),
  }));
  if (!(preserveExistingDraft && !nextChapters.length)) {
    conceptDraftModal.chapters = nextChapters;
  }
  conceptDraftModal.blockChapterMap = (data?.block_chapter_map && typeof data.block_chapter_map === "object")
    ? data.block_chapter_map
    : {};
  conceptDraftModal.blockChapterCodeMap = Array.isArray(data?.block_chapter_code_map)
    ? data.block_chapter_code_map
    : [];
  conceptDraftModal.chapterCodeMap = Array.isArray(data?.chapter_code_map)
    ? data.chapter_code_map
    : [];
  const mergedAiSuggestions = {
    ...(data?.ai_suggestions || {}),
    chapter_recommendations: Array.isArray(data?.chapter_recommendations)
      ? data.chapter_recommendations
      : (Array.isArray(data?.ai_suggestions?.chapter_recommendations) ? data.ai_suggestions.chapter_recommendations : []),
  };
  conceptDraftModal.aiSuggestions = _normalizeAiSuggestions(mergedAiSuggestions);
  conceptDraftModal.error = teachingRangeWarning ? "" : (_warningText(data?.code_start_warning) || "");
  conceptDraftModal.statusText = teachingRangeWarning
    ? "本次教學區間無效，下方仍顯示上一版章節草稿"
    : (conceptDraftModal.teachingRangeMode === 'teacher_range_only'
      ? `已依 teacher_range_only 重算，已載入 ${conceptDraftModal.chapters.length} 筆章節。`
      : `已載入 ${conceptDraftModal.chapters.length} 筆章節。`);
  conceptDraftModal.debugText = `mode=${conceptDraftModal.teachingRangeMode || 'legacy'} / formal=${Array.isArray(data?.concept_chapters_formal) ? data.concept_chapters_formal.length : 0} / draft=${Array.isArray(data?.concept_chapters_draft) ? data.concept_chapters_draft.length : 0} / teacher=${Array.isArray(data?.teacher_concept_chapters) ? data.teacher_concept_chapters.length : 0} / rec=${Array.isArray(data?.chapter_recommendations) ? data.chapter_recommendations.length : 0}`;
  conceptDraftModal.open = true;
  return true;
}

async function alignConceptTask(row) {
  resetConceptDraftModalState();

  conceptDraftModal.loading = true;
  conceptDraftModal.error = "";
  conceptDraftModal.statusText = "已載入草稿資料，請先輸入教學大區間，再按 [設定教學大區間] 進行判定。";
  conceptDraftModal.debugText = "";
  conceptDraftModal.open = true;
  conceptDraftModal.teachingRangeMode = "awaiting_teacher_range";

  try {
    const taskId = row?.task_id;
    if (!taskId) throw new Error("缺少 task_id");
    openConceptDraftModal(taskId, previewData.value || {});
    conceptDraftModal.taskId = taskId;
    conceptDraftModal.statusText = "草稿已載入，請先輸入教學大區間，再按 [設定教學大區間] 進行判定。";
  } catch (e) {
    const message = e?.response?.data?.message || e?.message || "unknown";
    conceptDraftModal.error = `⚠️ 載入草稿失敗：${message}`;
    conceptDraftModal.statusText = "草稿載入未成功。";
    conceptDraftModal.teachingRangeMode = "";
  } finally {
    conceptDraftModal.loading = false;
  }
}

async function rebuildConceptDraft(mode = "teacher_range_only") {
  if (!conceptDraftModal.taskId) return;

  try {
    conceptDraftModal.rebuilding = true;
    conceptDraftModal.error = "";
    conceptDraftModal.statusText = "重新產生概念章節草稿...";

    const useAutoStart = mode === "auto";
    const teachingRangeMode = useAutoStart ? "auto" : "teacher_range_only";

    if (!useAutoStart) {
      const validation = _validateTeachingRangeWithinSubtitleBounds(
        conceptDraftModal.teachingRangeStart,
        conceptDraftModal.teachingRangeEnd
      );

      if (!validation.ok) {
        conceptDraftModal.error = validation.message;
        conceptDraftModal.statusText = validation.message;
        conceptDraftModal.teachingRangeMode = "";
        return;
      }
    }

    const previousChapters = Array.isArray(conceptDraftModal.chapters)
      ? conceptDraftModal.chapters.map((ch) => ({ ...ch }))
      : [];

    const { data } = await parsonsPost("/fixed_task/align_concept", {
      task_id: conceptDraftModal.taskId,
      video_id: selectedVideo.value || "",
      subtitle_version: selectedSubtitleVersion.value || null,
      teaching_range_start: useAutoStart ? null : (conceptDraftModal.teachingRangeStart || null),
      teaching_range_end: useAutoStart ? null : (conceptDraftModal.teachingRangeEnd || null),
      teaching_range_mode: teachingRangeMode,
      mode: teachingRangeMode,
      force_rebuild: true,
    });

    if (!data?.ok) {
      throw new Error(data?.message || data?.error || "重新判斷失敗");
    }

    // 只在有完整字幕時才更新全集，避免被局部 subtitle_segments 污染
    const responseSubtitleSegments = Array.isArray(data?.subtitle_segments)
      ? data.subtitle_segments
      : [];

    if (responseSubtitleSegments.length) {
      const responseBounds = (() => {
        const starts = responseSubtitleSegments
          .map((s) => Number(s?.start))
          .filter((v) => Number.isFinite(v));
        const ends = responseSubtitleSegments
          .map((s) => Number(s?.end))
          .filter((v) => Number.isFinite(v));

        if (!starts.length || !ends.length) return null;

        return {
          start: Math.min(...starts),
          end: Math.max(...ends),
        };
      })();

      const currentBounds = (() => {
        const segs = allSubtitleSegments.value || [];
        const starts = segs.map((s) => Number(s?.start)).filter((v) => Number.isFinite(v));
        const ends = segs.map((s) => Number(s?.end)).filter((v) => Number.isFinite(v));

        if (!starts.length || !ends.length) return null;

        return {
          start: Math.min(...starts),
          end: Math.max(...ends),
        };
      })();

      // 只有在目前沒有全集，或新資料範圍更完整時，才更新 allSubtitleSegments
      if (
        !currentBounds ||
        (responseBounds && responseBounds.end >= currentBounds.end)
      ) {
        allSubtitleSegments.value = responseSubtitleSegments;
      }

      // subtitleSegments 保留給畫面/當次回傳資料使用，不再當作全片全集
      subtitleSegments.value = responseSubtitleSegments;
    }

    conceptDraftModal.teachingRangeStart =
      data?.teaching_range_start !== undefined && data?.teaching_range_start !== null
        ? _formatTimeInputValue(data.teaching_range_start)
        : (conceptDraftModal.teachingRangeStart || "");

    conceptDraftModal.teachingRangeEnd =
      data?.teaching_range_end !== undefined && data?.teaching_range_end !== null
        ? _formatTimeInputValue(data.teaching_range_end)
        : (conceptDraftModal.teachingRangeEnd || "");

    conceptDraftModal.teachingRange =
      data?.teaching_range || conceptDraftModal.teachingRange || null;

    conceptDraftModal.teachingRangeMode =
      data?.teaching_range_mode || teachingRangeMode;

    conceptDraftModal.teachingRangeSegments = Array.isArray(data?.teaching_range_segments)
      ? data.teaching_range_segments
      : _filterSegmentsByRange(
          allSubtitleSegments.value || [],
          conceptDraftModal.teachingRange || _getTeachingRangeBounds()
        );

    conceptDraftModal.teachingRangeWarning = data?.teaching_range_warning || null;

    conceptDraftModal.teachingRangeRecommendedRange =
      data?.teaching_range_recommended_range ||
      conceptDraftModal.teachingRange ||
      _getTeachingRangeBounds();

    conceptDraftModal.teachingRangeEffective =
      data?.teaching_range_effective !== false;

    const mergedAiSuggestions = {
      ...(data?.ai_suggestions || {}),
      chapter_recommendations: Array.isArray(data?.chapter_recommendations)
        ? data.chapter_recommendations
        : (Array.isArray(data?.ai_suggestions?.chapter_recommendations) ? data.ai_suggestions.chapter_recommendations : []),
    };
    conceptDraftModal.aiSuggestions = _normalizeAiSuggestions(mergedAiSuggestions);

    conceptDraftModal.codeSummaryRange =
      data?.code_summary_range || conceptDraftModal.codeSummaryRange || null;

    conceptDraftModal.codeSummarySegments = Array.isArray(data?.code_summary_segments)
      ? data.code_summary_segments
      : _filterSegmentsByRange(
          allSubtitleSegments.value || [],
          conceptDraftModal.codeSummaryRange || _getCodeSummaryRangeBounds()
        );

    conceptDraftModal.codeStartWarning = data?.code_start_warning || null;

    conceptDraftModal.codeStartRecommendedRange =
      data?.code_start_recommended_range ||
      conceptDraftModal.codeSummaryRange ||
      _getCodeSummaryRangeBounds();

    conceptDraftModal.codeStartEffective =
      data?.code_start_effective !== false;

    conceptDraftModal.preservedPreviousDraft =
      Boolean(data?.preserved_previous_draft);

    conceptDraftModal.blockChapterMap = (data?.block_chapter_map && typeof data.block_chapter_map === "object")
      ? data.block_chapter_map
      : {};
    conceptDraftModal.blockChapterCodeMap = Array.isArray(data?.block_chapter_code_map)
      ? data.block_chapter_code_map
      : [];
    conceptDraftModal.chapterCodeMap = Array.isArray(data?.chapter_code_map)
      ? data.chapter_code_map
      : [];

    const mappedRows = _mapSystemChapterRows(data, {
      preferDraft: teachingRangeMode === "teacher_range_only",
    }).map((ch) => ({
      ...ch,
      start: _secToTC(ch.start),
      end: _secToTC(ch.end),
    }));

    if (mappedRows.length) {
      conceptDraftModal.chapters = mappedRows;
      conceptDraftModal.selectedChapterIndex = 0;
    }

    if (_warningText(data?.teaching_range_warning)) {
      if (!Array.isArray(conceptDraftModal.chapters) || !conceptDraftModal.chapters.length) {
        conceptDraftModal.chapters = previousChapters;
      }
      conceptDraftModal.selectedChapterIndex =
        conceptDraftModal.chapters.length ? 0 : null;
      conceptDraftModal.statusText = "本次教學區間無效，下方仍顯示上一版章節草稿";
      conceptDraftModal.error = "";
      conceptDraftModal.preservedPreviousDraft = true;
    } else {
      conceptDraftModal.statusText = useAutoStart
        ? "已回復自動判定教學區間。"
        : "已依教學大區間重新判斷。";
    }

    if (
      !_warningText(data?.teaching_range_warning) &&
      !_warningText(data?.code_start_warning)
    ) {
      conceptDraftModal.error = "";
    }
  } catch (e) {
    conceptDraftModal.error =
      "⚠️ 重新判斷失敗：" +
      (e?.response?.data?.message || e?.message || "unknown");
    conceptDraftModal.statusText = "重新判斷失敗。";
    conceptDraftModal.teachingRangeMode = "";
  } finally {
    conceptDraftModal.rebuilding = false;
  }
}

// 新增：儲存系統判斷後的正式章節
async function saveConceptDraft() {
  if (!conceptDraftModal.taskId) return;
  try {
    conceptDraftModal.saving = true;

    if (conceptDraftModal.chapters.length === 1) {
      const ok = window.confirm("目前只有 1 個章節，請確認是否真的只有單一概念");
      if (!ok) return;
    }

    for (const c of conceptDraftModal.chapters) {
      c.concept_label = String(c.concept_label || c.concept || c.chapter_label || "").trim();
      c.concept_tag = String(c.concept_tag || _inferConceptTagFromLabel(c.concept_label) || c.concept || "").trim();
      c.source = String(c.source || c.chapter_source || "").trim() || "teacher_confirmed";

      const startRaw = String(c.start ?? "").trim();
      const endRaw = String(c.end ?? "").trim();
      const isMissingConcept = c.source === "ai_missing_concept" || c.chapter_source === "ai_missing_concept";

      if (startRaw || endRaw) {
        const startSec = _parseChapterTimeToSec(c.start);
        const endSec = _parseChapterTimeToSec(c.end);
        if (startSec === null || endSec === null || startSec < 0 || endSec <= startSec) {
          if (!isMissingConcept) {
            throw new Error(`章節 "${c.concept}" 的時間區段無效 (start: ${c.start}, end: ${c.end})`);
          }
        } else {
          c.start = startSec;
          c.end = endSec;
        }
      } else if (!isMissingConcept) {
        throw new Error(`章節 "${c.concept}" 的時間區段無效 (start: ${c.start}, end: ${c.end})`);
      }
    }

    const { data } = await parsonsPost("/fixed_task/save_teacher_concept_chapters", {
      task_id: conceptDraftModal.taskId,
      chapters: conceptDraftModal.chapters,
      subtitle_version: selectedSubtitleVersion.value || null,
      teaching_range_start: conceptDraftModal.teachingRangeStart || null,
      teaching_range_end: conceptDraftModal.teachingRangeEnd || null,
      code_start_ts: conceptDraftModal.teachingRangeStart || null,
    });

    if (!data?.ok) throw new Error(data?.message || "儲存失敗");

    conceptDraftModal.open = false;
    alert("章節已儲存為正式章節。");
    await loadSegmentEditorTask(conceptDraftModal.taskId);

  } catch (e) {
    alert("⚠️ 儲存章節失敗：" + (e?.response?.data?.message || e?.message || "unknown"));
  } finally {
    conceptDraftModal.saving = false;
  }
}


async function alignFixedTask(row) {
  try {
    const taskId = row?.task_id;
    if (!taskId) throw new Error("缺少 task_id");

    const { data } = await api.post("/api/parsons/fixed_task/align_subtitle", {
      task_id: taskId,
      video_id: selectedVideo.value || ""
    });

    if (!data?.ok) {
      throw new Error(data?.message || "字幕對齊失敗");
    }

    await fetchQuestions();
    alert(`✅ 對齊完成：${(data?.slots_aligned || []).length} 格已建立回看片段`);
  } catch (e) {
    alert("⚠️ 對齊失敗：" + (e?.response?.data?.message || e?.message || "unknown"));
  }
}

async function publishFromModal() {
  if (!modal.data?.task_id) return;
  await saveReviewOnly();
  await t5Post("/question/publish", { task_id: modal.data.task_id });
  await fetchQuestions();
  closeModal();
}

async function regenFromModal() {
  await saveReviewOnly();
  await regenerate();
  closeModal();
}

async function rejectFromModal() {
  if (!modal.data?.task_id) return;
  await t5Post("/question/reject", {
    task_id: modal.data.task_id,
    review_tags: reviewTags.value,
    review_note: reviewNote.value
  });
  await fetchQuestions();
  closeModal();
}

// 干擾區塊切換移除/保留
/** 每次載入預覽都重置為該題目前狀態，避免沿用上一題切換結果 */
function resetDistractorKeep(distractors = []) {
  Object.keys(distractorKeep).forEach((k) => delete distractorKeep[k]);

  for (const b of distractors) {
    const id = String(b.id ?? b._id ?? "");
    if (!id) continue;
    distractorKeep[id] = b?.enabled !== false;
  }
}

/** 是否保留（預設 true） */
function isKeepDistractor(id) {
  const key = String(id || "");
  return typeof distractorKeep[key] === "undefined" ? true : !!distractorKeep[key];
}

/** 點 ✅：保留 */
function keepDistractor(id) {
  distractorKeep[String(id)] = true;
}

/** 點 ❌：移除 */
function removeDistractor(id) {
  distractorKeep[String(id)] = false;
}

function setDistractorVisible(id, visible) {
  if (visible) keepDistractor(id);
  else removeDistractor(id);
}

async function onToggleSemanticVisibility(checked) {
  hideSemanticZh.value = !checked;

  const taskId = previewData.value?.meta?.task_id;
  if (!taskId) return;

  semanticToggleSaving.value = true;
  try {
    await t5Post("/question/review_save", {
      task_id: taskId,
      review_tags: reviewForm.tags || [],
      review_note: reviewForm.note || "",
      hide_semantic_zh: !!hideSemanticZh.value,
    });
  } catch (e) {
    alert("⚠️ 中文語意顯示設定儲存失敗，請再試一次");
  } finally {
    semanticToggleSaving.value = false;
  }
}

// ===== watchers =====
watch(selectedUnit, async (u) => {
  if (!u) return;
  await fetchVideos(u);
});

watch(selectedVideo, async (v) => {
  if (!v) {
    questions.value = [];
    previewData.value = null;
    selectedFixedTaskId.value = "";
    segmentEditorRows.value = [];
    segmentEditorTaskId.value = "";
    segmentEditorDebug.value = null;
    predictedWindowMap.value = {};
    return;
  }
  await fetchVideoInfo(v);
  await fetchQuestions();
});

watch([filterStatus, sortOrder], async () => {
  if (!selectedVideo.value) return;
  await fetchQuestions();
});

// ===== init =====
onMounted(async () => {
  // [新增] 讀取後測開放狀態
  fetchPostOpen();
  await fetchUnits();
});

const reviewForm = reactive({
  tags: [],
  note: ""
});

async function publishFromPreview() {
  try {
    const taskId = previewData.value?.meta?.task_id;
    if (!taskId) throw new Error("缺少 task_id，無法發布");

    // 1) 先存老師審核（tags/note + 干擾保留移除）
    await t5Post("/question/review_save", {
      task_id: taskId,
      review_tags: reviewForm.tags || [],
      review_note: reviewForm.note || "",
      distractor_keep: { ...distractorKeep }, // ✅ 把 ✅/❌ 狀態送到後端
      hide_semantic_zh: !!hideSemanticZh.value,
    });

    // 2) 再發布（學生端可見）
    await t5Post("/question/publish", {
      task_id: taskId,
    });

    // 3) UI 更新：重抓列表 + 重新讀取預覽狀態
    await fetchQuestions();
    await openPreview({ task_id: taskId }); // 重新載入（可選）
    if (Array.isArray(previewData.value?.subtitle_segments)) {
        allSubtitleSegments.value = previewData.value.subtitle_segments;
        subtitleSegments.value = previewData.value.subtitle_segments;}
    alert("✅ 已發布：學生端現在看得到這題了");
  } catch (e) {
    alert("⚠️ 發布失敗：" + (e?.message || "unknown"));
  }
}

// ai中文語意提示
function enhanceMeaning(codeText, rawMeaning) {
  const t = (codeText || "").trim();

  // 先用你原本的 rawMeaning 當 fallback
  const base = rawMeaning || "（未提供）";

  // 針對常見模式做教學版補強
  if (/^total\s*=\s*0$/.test(t)) {
    return "建立變數 total，用來累積加總結果，先把初始值設為 0。";
  }
  if (/^for\s+\w+\s+in\s+range\(\s*1\s*,\s*6\s*\)\s*:\s*$/.test(t)) {
    return "使用迴圈讓 i 依序取值 1 到 5，準備逐一加總（range(1,6) 不包含 6）。";
  }
  if (/^total\s*\+=\s*i$/.test(t)) {
    return "把目前的 i 加到 total 中，逐步累積總和。";
  }
  if (/^print\(\s*total\s*\)$/.test(t)) {
    return "迴圈結束後，輸出最後計算完成的總和結果。";
  }

  // 其他行：維持原本語意
  return base;
}


function returnNotPublish() {
  alert("（示意）已退回：你下一步要接後端 /return，把題目 status=已退回 並存 review tags/note。");
}



// [新增] 切換單元時同步讀取後測開放狀態
watch(selectedUnit, () => {
  fetchPostOpen();
});</script>

<style scoped>
/* ===== Layout ===== */
.t5-layout { display: grid; grid-template-columns: 240px 1fr; min-height: 100vh; background: #f5f6f8; }
.sidebar { background: #d4b34a; padding: 18px 16px; display: flex; flex-direction: column; gap: 16px; }
.profile { display: flex; align-items: center; gap: 10px; }
.avatar { width: 48px; height: 48px; border-radius: 50%; background: #fff; opacity: 0.9; }
.hello { font-weight: 900; }
.menu { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.menu-item { border: none; background: transparent; text-align: left; padding: 10px 10px; border-radius: 10px; cursor: pointer; font-weight: 800; }
.menu-item.active { background: rgba(255,255,255,0.35); }
.logout { margin-top: auto; }
.btn-outline { width: 100%; border: 2px solid #0b2a4a; background: #fff; padding: 10px; border-radius: 10px; font-weight: 900; cursor: pointer; }

/* ===== Main ===== */
.main { padding: 18px 22px; }
.title { margin: 6px 0 16px; text-align: center; font-size: 20px; font-weight: 900; }

/* ===== Cards grid ===== */
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.card { background: #fff; border-radius: 18px; border: 2px solid #1d1d1d; padding: 14px 14px 12px; }
.card-wide { grid-column: 1 / -1; }
.card-title { margin: 0 0 10px; font-size: 15px; font-weight: 900; }

.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
.field label { display: block; font-size: 12px; font-weight: 900; margin-bottom: 6px; }
.field select { width: 100%; padding: 8px; border-radius: 10px; border: 1px solid #d0d0d0; background: #fff; }
.info .kv { font-size: 13px; }
.k { font-weight: 900; }
.actions { display: flex; gap: 10px; justify-content: flex-end; align-items: center; }

.kvcol { display: grid; gap: 10px; font-size: 13px; }

/* ===== chips ===== */
.chip { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 900; margin-right: 6px; }
.chip-ok { background: #e7f6ee; color: #1b6b3a; border: 1px solid #a7e0bd; }
.chip-off { background: #f2f2f2; color: #777; border: 1px solid #ddd; }

/* ===== flow ===== */
.flow { display: flex; align-items: center; justify-content: center; gap: 10px; margin-top: 4px; }
.step { display: grid; justify-items: center; gap: 6px; }
.num { width: 22px; height: 22px; border-radius: 50%; background: #dfe9ff; display: grid; place-items: center; font-weight: 900; }
.box { width: 130px; height: 78px; border-radius: 14px; background: #f7e1b2; display: grid; place-items: center; border: 1px solid #e3c07c; }
.icon { font-size: 20px; }
.txt { font-weight: 900; }
.arrow { font-weight: 900; color: #777; }
.meta { margin-top: 10px; display: grid; gap: 6px; font-size: 13px; font-weight: 800; }

/* ===== D ===== */
.d-toolbar { display: flex; gap: 14px; align-items: center; margin-bottom: 10px; }

.pin-badge { display: inline-block; background: #e0f0ff; color: #1a56db; font-size: 11px; padding: 2px 7px; border-radius: 10px; font-weight: 700; white-space: nowrap; }
.fixed-row { background: #f5faff; }
.fixed-row td { border-left: 3px solid #93c5fd; }
.d-filter { display: flex; gap: 8px; align-items: center; font-weight: 900; font-size: 13px; }
.d-filter select { padding: 8px 10px; border-radius: 10px; border: 1px solid #d0d0d0; background: #fff; }

.d-state { background: #f6f7fb; border: 1px dashed #c9d3e6; border-radius: 12px; padding: 10px; font-weight: 800; color: #445; }
.d-state.err { border-color: #f1a5a5; background: #fff2f2; color: #8b1a1a; }

.table-wrap { margin-top: 10px; border: 1px solid #e2e2e2; border-radius: 12px; overflow: hidden; }
.t { width: 100%; border-collapse: collapse; font-size: 13px; }
.t th, .t td { padding: 10px; border-bottom: 1px solid #eee; vertical-align: middle; }
.t thead th { background: #f3f4f6; font-weight: 900; }
.sub { font-size: 11px; color: #666; margin-top: 4px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }

.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.dot-yellow { background: #e7b84a; }

.segTableWrap {
  margin-top: 8px;
  border: 1px solid #e6e6e6;
  border-radius: 10px;
  overflow: auto;
}

.segTable {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  min-width: 1280px;
}

.segTable th,
.segTable td {
  border-bottom: 1px solid #f0f0f0;
  padding: 8px;
  text-align: left;
  vertical-align: middle;
}

.segTable thead th {
  background: #f8fafc;
  font-weight: 800;
  white-space: nowrap;
}

.segCode {
  min-width: 260px;
  max-width: 420px;
  white-space: pre-wrap;
  word-break: break-word;
}

.segTag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border: 1px solid #c9def9;
  color: #244b7a;
  font-size: 11px;
  font-weight: 800;
}

.segSub {
  color: #5f6b7a;
}

.segSourceHint {
  font-size: 11px;
  color: #5f6b7a;
  margin-bottom: 6px;
}
.segWarn {
  font-size: 11px;
  color: #9a3412;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  padding: 4px 8px;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.segMiniBtn {
  border: 1px solid #fdba74;
  background: #ffedd5;
  color: #9a3412;
  border-radius: 6px;
  font-size: 11px;
  line-height: 1;
  padding: 4px 8px;
  cursor: pointer;
}

.segInput {
  width: 100%;
  min-width: 90px;
  padding: 6px 8px;
  border-radius: 8px;
  border: 1px solid #cfd7e3;
}

.segDelta {
  white-space: nowrap;
  color: #334155;
  font-size: 11px;
}

.segCtx {
  min-width: 260px;
  max-width: 360px;
}

.segCtxLine {
  max-height: 46px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  line-height: 1.4;
  margin-bottom: 6px;
}

.segSubList {
  max-height: 180px;
  overflow: auto;
  background: #ffffff;
  border: 1px solid #dfe6f1;
  border-radius: 8px;
  padding: 6px 8px;
}

.segSubItem {
  font-size: 12px;
  line-height: 1.45;
  padding: 4px 0;
  border-bottom: 1px dashed #edf1f7;
  white-space: pre-wrap;
  word-break: break-word;
}

.segSubItem:last-child {
  border-bottom: none;
}

.segActions {
  margin-top: 10px;
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  flex-wrap: wrap;
}

.segDebug {
  margin-top: 10px;
  border: 1px solid #dce6f3;
  background: #f8fbff;
  border-radius: 10px;
  padding: 10px;
}

.segDebugTitle {
  font-size: 12px;
  font-weight: 800;
  color: #234062;
  margin-bottom: 6px;
}

.segMiniBtn {
  margin-top: 6px;
  white-space: nowrap;
}
.dot-green { background: #2fbf71; }
.dot-red { background: #e74c3c; }

.btns { display: flex; gap: 8px; flex-wrap: wrap; }
.pillBtn { border: none; padding: 7px 12px; border-radius: 10px; font-weight: 900; cursor: pointer; }
.pillBtn.preview { background: #6d85a5; color: #fff; }
.pillBtn.publish { background: #f2c266; }
.pillBtn.unpub { background: #ffe0a6; }
.pillBtn.regen { background: #ffd6d6; }

.noteBtn { border: none; background: transparent; cursor: pointer; font-weight: 900; color: #1f3b5b; }
.btn { border: none; background: #f2c266; padding: 10px 14px; border-radius: 10px; font-weight: 900; cursor: pointer; }
.btn.mini { padding: 8px 12px; margin-top: 8px; }
.warn { background: #ffd6d6; }
.hint { margin-top: 8px; font-size: 12px; color: #666; }
.err { color: #b00020; }

/* ===== Modal ===== */
/* ===== Modal 美化（TeacherT5AgentLog.vue）===== */

.modal-mask{
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, .45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  z-index: 9999;
}

.modal{
  width: min(1180px, 96vw);
  height: min(86vh, 920px);
  background: #fff;
  border-radius: 14px;
  border: 1px solid rgba(0,0,0,.08);
  box-shadow: 0 18px 60px rgba(0,0,0,.25);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Header */
.modal-head{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
  background: linear-gradient(180deg, #ffffff, #fbfbfd);
  border-bottom: 1px solid rgba(0,0,0,.08);
}

.modal-title{
  font-weight: 800;
  letter-spacing: .2px;
  font-size: 16px;
  color: #111827;
}

.modal-head .x{
  width: 34px;
  height: 34px;
  border-radius: 10px;
  border: 1px solid rgba(0,0,0,.12);
  background: #fff;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: transform .08s ease, background .12s ease;
}
.modal-head .x:hover{ background: #f3f4f6; }
.modal-head .x:active{ transform: scale(.98); }

/* Body（可滾動） */
.modal-body{
  flex: 1 1 auto;
  overflow: auto;
  padding: 18px;
  background: #ffffff;
}

/* 內容 container */
.pv{
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* Meta pills */
.pvTop{
  background: #fff;
}
.pvMetaRow{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.pill{
  font-size: 13px;
  color: #111827;
  background: #f3f4f6;
  border: 1px solid rgba(0,0,0,.08);
  border-radius: 999px;
  padding: 6px 10px;
}
.pvHr{
  margin: 12px 0 0;
  border: none;
  border-top: 1px dashed rgba(0,0,0,.12);
}

/* 區塊標題 */
.pvSection{
  background: #fff;
  border: 1px solid rgba(0,0,0,.08);
  border-radius: 12px;
  padding: 14px;
}
.pvH{
  font-weight: 800;
  font-size: 14px;
  color: #111827;
  margin-bottom: 10px;
}

.pvHRow{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.pvHRow .pvH{
  margin-bottom: 0;
}

/* 題目敘述 box */
.pvBox{
  background: #f8fafc;
  border: 1px solid rgba(0,0,0,.08);
  border-radius: 10px;
  padding: 12px;
  color: #111827;
  line-height: 1.7;
  white-space: pre-wrap;
}

.pvList{
  margin: 8px 0 0 18px;
  padding: 0;
}

.pvList li{
  margin: 4px 0;
  line-height: 1.6;
}

.typeTag{
  display: inline-block;
  min-width: 78px;
  margin-right: 8px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid #cbd5e1;
  background: #f8fafc;
  color: #334155;
  font-size: 12px;
}

/* Blocks grid */
.pvGrid{
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

/* 每個 block */
.pvBlock{
  border: 1px solid rgba(0,0,0,.08);
  border-radius: 12px;
  background: #ffffff;
  padding: 12px;
}
.pvBlock .code{
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 13px;
  background: #0b1220;
  color: #e5e7eb;
  padding: 10px 12px;
  border-radius: 10px;
  white-space: pre-wrap;
  line-height: 1.6;
  overflow-x: auto;
}
.pvBlock .zh{
  margin-top: 8px;
  color: #374151;
  font-size: 13px;
  line-height: 1.65;
  background: #f8fafc;
  border: 1px solid rgba(0,0,0,.06);
  border-radius: 10px;
  padding: 10px 12px;
}

/* Empty state */
.pvEmpty{
  border-radius: 12px;
  padding: 12px;
  background: #fff7ed;
  border: 1px solid rgba(245, 158, 11, .35);
  color: #9a3412;
  font-size: 13px;
  line-height: 1.6;
}

/* 正確答案順序 */
.pvOrder{
  background: #f8fafc;
  border: 1px dashed rgba(0,0,0,.18);
  border-radius: 10px;
  padding: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 13px;
  color: #111827;
  white-space: pre-wrap;
}
.pvSmall{
  margin-top: 8px;
  font-size: 12px;
  color: #6b7280;
}

/* Checkboxes */
.pvChecks{
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  padding: 6px 0 0;
}
.pvChecks label{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #111827;
}
.pvChecks input[type="checkbox"]{
  width: 16px;
  height: 16px;
}

/* Note textarea */
.pvNote{
  width: 100%;
  min-height: 110px;
  resize: vertical;
  border-radius: 12px;
  border: 1px solid rgba(0,0,0,.12);
  background: #fff;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.6;
  outline: none;
}
.pvNote:focus{
  border-color: rgba(59,130,246,.55);
  box-shadow: 0 0 0 4px rgba(59,130,246,.12);
}

/* ✅ 讓按鈕列更像 footer（即使你還沒搬到 modal-foot，也會好看） */
.pvActions{
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
  padding-top: 6px;
}

.concept-meta-bar{
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 8px 0 12px;
}

.concept-meta-block{
  flex: 1 1 320px;
  min-width: 260px;
  padding: 12px;
  border: 1px solid rgba(0,0,0,.08);
  border-radius: 12px;
  background: #fbfcfe;
}

.concept-meta-title{
  font-size: 12px;
  font-weight: 800;
  color: #334155;
  margin-bottom: 6px;
}

.concept-meta-row{
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.concept-start-input{
  width: 120px;
  border: 1px solid #cbd5e1;
  padding: 6px 8px;
  border-radius: 10px;
  background: #fff;
}

.concept-meta-note{
  flex: 1 1 220px;
  min-width: 220px;
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
  padding: 12px;
  border: 1px dashed #dbe3ef;
  border-radius: 12px;
  background: #fff;
}

.concept-shell{
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(300px, 0.95fr);
  gap: 14px;
  align-items: start;
}

.concept-left,
.concept-right{
  min-width: 0;
}

.subtitle-fold{
  margin: 0 0 12px;
  padding: 10px 12px;
  border: 1px solid #dbe3ef;
  border-radius: 12px;
  background: #f8fafc;
}

.subtitle-fold summary{
  cursor: pointer;
  font-weight: 700;
  color: #334155;
}

.subtitle-fold-body{
  margin-top: 10px;
  max-height: 180px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.55;
}

.subtitle-row{
  padding: 4px 0;
  border-bottom: 1px solid #eef2f7;
}

.subtitle-row-active{
  background: #fff4cc;
  border-radius: 8px;
  padding-left: 8px;
  padding-right: 8px;
}

.subtitle-row:last-child{
  border-bottom: none;
}

.subtitle-time{
  color: #64748b;
  margin-right: 6px;
}

.subtitle-text{
  color: #0f172a;
}

.draft-table th,
.draft-table td{
  vertical-align: top;
}

.concept-text-input,
.concept-time-input{
  width: 100%;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  padding: 6px 8px;
  background: #fff;
}

.concept-text-input:focus,
.concept-time-input:focus{
  outline: none;
  border-color: rgba(59,130,246,.55);
  box-shadow: 0 0 0 4px rgba(59,130,246,.12);
}

.concept-row-meta{
  margin-top: 6px;
  font-size: 12px;
  color: #64748b;
  line-height: 1.45;
}

.draft-row{
  cursor: pointer;
}

.draft-row-selected{
  background: #f8fbff;
  outline: 2px solid rgba(59,130,246,.28);
  outline-offset: -2px;
}

.draft-focus-bar{
  margin: 0 0 12px;
  padding: 10px 12px;
  border: 1px solid #dbe3ef;
  border-radius: 12px;
  background: #f8fbff;
}

.draft-focus-title{
  font-size: 12px;
  font-weight: 800;
  color: #334155;
  margin-bottom: 4px;
}

.draft-focus-text{
  font-size: 13px;
  color: #0f172a;
  line-height: 1.5;
}

.draft-focus-preview{
  margin-top: 6px;
  font-size: 12px;
  color: #475569;
  line-height: 1.5;
  background: #fff;
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
  padding: 8px 10px;
}

.draft-focus-tag,
.draft-focus-range,
.suggest-meta{
  display: inline-block;
  margin-left: 8px;
  font-size: 11px;
  color: #64748b;
}

.concept-actions{
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.suggest-panel{
  border: 1px solid #dbe3ef;
  border-radius: 14px;
  background: linear-gradient(180deg, #fbfdff, #f8fbff);
  padding: 12px;
  position: sticky;
  top: 0;
}

.suggest-panel-title{
  font-size: 14px;
  font-weight: 800;
  color: #0f172a;
  margin-bottom: 10px;
}

.suggest-section + .suggest-section{
  margin-top: 14px;
}

.suggest-section-title{
  font-size: 12px;
  font-weight: 800;
  color: #475569;
  margin-bottom: 8px;
}

.suggest-list{
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.suggest-card{
  background: #fff;
  border: 1px solid #dbe3ef;
  border-radius: 12px;
  padding: 10px;
}

.missing-card{
  background: #fffaf2;
}

.suggest-main{
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.suggest-original{
  font-weight: 700;
  color: #334155;
}

.suggest-arrow{
  color: #94a3b8;
}

.suggest-target{
  font-weight: 800;
  color: #0f172a;
}

.suggest-actions-row{
  margin-top: 8px;
  display: flex;
  justify-content: flex-end;
}

.suggest-empty{
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
  background: #fff;
  border: 1px dashed #cbd5e1;
  border-radius: 12px;
  padding: 10px;
}

.suggest-missing-label{
  font-weight: 800;
  color: #7c2d12;
}

.suggest-missing-reason{
  margin-top: 6px;
  font-size: 12px;
  color: #92400e;
  line-height: 1.5;
}

/* Buttons */
.btn{
  border: 1px solid rgba(0,0,0,.14);
  background: #fff;
  color: #111827;
  border-radius: 12px;
  padding: 10px 14px;
  cursor: pointer;
  font-weight: 700;
  font-size: 13px;
  transition: transform .08s ease, background .12s ease, box-shadow .12s ease;
}
.btn:hover{
  background: #f3f4f6;
}
.btn:active{ transform: scale(.99); }
.btn:disabled{
  opacity: .55;
  cursor: not-allowed;
}

.btn.primary{
  background: #111827;
  color: #fff;
  border-color: rgba(17,24,39,.9);
}
.btn.primary:hover{ background: #0b1220; }

.btn.warn{
  background: #fff1f2;
  border-color: rgba(244,63,94,.35);
  color: #9f1239;
}
.btn.warn:hover{ background: #ffe4e6; }

/* 老師解答區 */
.pvOrderList{
  margin-top: 10px;
  border: 1px dashed rgba(0,0,0,.12);
  border-radius: 12px;
  padding: 10px 12px;
  background: #fafafa;
}

.pvOrderItem{
  display: flex;
  gap: 10px;
  padding: 8px 6px;
  border-bottom: 1px solid rgba(0,0,0,.06);
}
.pvOrderItem:last-child{ border-bottom: none; }

.pvOrderIdx{
  width: 28px;
  font-weight: 700;
  opacity: .7;
}

.pvOrderCode{
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 13px;
  white-space: pre;
  tab-size: 4;
}

.indentGuide{
  color: #94a3b8;
  font-weight: 700;
}

.pvIndentControls{
  margin-top: 10px;
  display: flex;
  justify-content: flex-end;
}

.pvIndentControls label{
  font-size: 12px;
  color: #475569;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.pvIndentControls select{
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 4px 8px;
  background: #fff;
}

.pvOrderId{
  display: inline-block;
  font-weight: 800;
  margin-right: 6px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef2ff;
  border: 1px solid rgba(99,102,241,.25);
}

.pvOrderZh{
  margin-top: 4px;
  font-size: 12px;
  opacity: .8;
}




/* 手機適配 */
@media (max-width: 720px){
  .modal{ width: 96vw; height: 90vh; }
  .modal-body{ padding: 14px; }
  .concept-shell{ grid-template-columns: 1fr; }
  .suggest-panel{ position: static; }
  .pvSection{ padding: 12px; }
  .pvActions{ justify-content: stretch; }
  .btn{ flex: 1 1 auto; }
}

/* 干擾提 */
/* 干擾卡：右上控制 */
.pvBlockD{
  position: relative;
}

.dCtrl{
  position: absolute;
  top: 10px;
  right: 10px;
  display: inline-flex;
  gap: 10px;
  z-index: 2;
}

.dSwitch{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 800;
  color: #334155;
  user-select: none;
  background: rgba(255, 255, 255, .9);
  border: 1px solid rgba(0,0,0,.12);
  border-radius: 999px;
  padding: 4px 8px;
}

.dSwitch input{
  display: none;
}

.dSlider{
  position: relative;
  width: 34px;
  height: 18px;
  border-radius: 999px;
  background: #cbd5e1;
  transition: background .15s ease;
}

.dSlider::after{
  content: "";
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0,0,0,.2);
  transition: transform .15s ease;
}

.dSwitch input:checked + .dSlider{
  background: #22c55e;
}

.dSwitch input:checked + .dSlider::after{
  transform: translateX(16px);
}

.dSwitchText{
  min-width: 28px;
}

/* 移除狀態：卡片淡掉 */
.pvBlock.removed{
  opacity: .45;
  filter: grayscale(.4);
  background: #fff1f2;               /* 淡紅底 */
  border-color: rgba(220,38,38,.35);
}

.pvBlock.removed .code{
  text-decoration: line-through;
  opacity: .8;
}

.dMask{
  position: absolute;
  inset: 0;
  background: rgba(148, 163, 184, 0.26);
  border-radius: 12px;
  z-index: 1;
  pointer-events: auto;
  cursor: not-allowed;
}

/* 標籤 */
.removedTag{
  display: inline-block;
  margin-left: 8px;
  font-size: 12px;
  font-weight: 700;
  color: #9f1239;
  background: #ffe4e6;
  border: 1px solid rgba(244,63,94,.35);
  padding: 3px 10px;
  border-radius: 999px;
}


/* ===== Preview Modal UI ===== */
.pv { padding: 6px 2px; }
.pvTop .pvMetaRow { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
.pill { background:#f2f2f2; border:1px solid #e0e0e0; padding:6px 10px; border-radius:999px; font-size:12px; }
.pvHr { border:0; border-top:1px solid #e6e6e6; margin:12px 0; }

.pvSection { margin: 12px 0; }
.pvH { font-weight: 800; margin-bottom: 8px; }
.pvBox { background:#f6f8ff; border:1px solid #dde3ff; padding:12px; border-radius:10px; }
.pvGrid { display:grid; grid-template-columns: 1fr 1fr; gap:12px; }
.pvBlock { background:#fff; border:1px solid #e8e8e8; border-radius:12px; padding:12px; }
.pvBlock .code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-weight:700; }
.pvBlock .zh { margin-top:8px; color:#444; background:#fff7d6; border:1px solid #ffe29a; padding:8px 10px; border-radius:10px; }

.pvEmpty { grid-column: 1 / -1; background:#fff3f3; border:1px solid #ffd2d2; padding:12px; border-radius:10px; color:#8a2f2f; }

.pvOrder { border:1px dashed #cfcfcf; border-radius:12px; padding:12px; background:#fafafa; }
.pvSmall { margin-top:8px; font-size:12px; color:#666; }

.pvChecks { display:flex; flex-wrap:wrap; gap:16px; }
.pvNote { width:100%; min-height:90px; border-radius:12px; border:1px solid #e0e0e0; padding:12px; outline:none; }

.pvActions { display:flex; justify-content:center; gap:12px; margin-top: 14px; flex-wrap:wrap; }


/* [新增] 後測發布/取消發布按鈕區（最小樣式，不影響既有排版） */
.posttest-bar{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  margin: 8px 0 16px;
  padding: 10px 12px;
  border: 1px solid #e6e6e6;
  border-radius: 10px;
  background: #fff;
}
.posttest-left{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
  font-size:14px;
}
.posttest-status{
  font-weight:700;
}
.posttest-status.open{ color:#2e7d32; }
.posttest-status.closed{ color:#c62828; }
.posttest-hint{ color:#888; font-size:12px; }
.posttest-actions{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
}

/* ===== 穩定模式切換開關 ===== */
.stable-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1.5px solid #d0d0d0;
  background: #f7f8fa;
  cursor: pointer;
  user-select: none;
  transition: background .15s ease, border-color .15s ease;
  font-size: 13px;
  font-weight: 700;
}
.stable-toggle:hover {
  background: #eef0f5;
}
.stable-toggle.stable-on {
  background: #eff6ff;
  border-color: #3b82f6;
  color: #1d4ed8;
}
.stable-knob {
  width: 30px;
  height: 16px;
  border-radius: 999px;
  background: #d0d0d0;
  position: relative;
  flex-shrink: 0;
  transition: background .15s ease;
}
.stable-knob::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  transition: transform .15s ease;
}
.stable-on .stable-knob {
  background: #3b82f6;
}
.stable-on .stable-knob::after {
  transform: translateX(14px);
}
.stable-label {
  white-space: nowrap;
}

/* [新增] 題型 badge */
.ai-badge {
  display: inline-block;
  background: #fff7d6;
  color: #8a6500;
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 10px;
  font-weight: 700;
  white-space: nowrap;
}

/* [新增] 狀態顏色 */
.dot-gray { background: #9ca3af; }
.dot-blue { background: #3b82f6; }

/* [新增] 學生可見 */
.visible-yes {
  color: #15803d;
  font-weight: 900;
}
.visible-no {
  color: #9ca3af;
  font-weight: 900;
}

/* [新增] 固定題編輯按鈕 */
.pillBtn.edit {
  background: #dbeafe;
  color: #1d4ed8;
}

</style>