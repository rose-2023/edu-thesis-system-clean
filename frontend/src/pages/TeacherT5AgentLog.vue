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

          <p class="hint err" v-if="err.e">{{ err.e }}</p>
        </div>
      </section>

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
                <div class="pvH">Parsons 區塊（AI 生成｜逐區塊中文語意）</div>
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

// Modal
const modal = reactive({
  open: false,
  loading: false,
  err: "",
  data: null
});


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
    videos.value = data.items || [];
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
    questions.value = (data.items || []).map((q, idx) => ({
      ...q,
      source_type: q.source_type || q.gen_source || "ai",
      review_status: q.review_status || q.status || "pending",
      task_code: q.task_code || (q.gen_source === "fixed" ? "FIXED-01" : ""),
      enabled: typeof q.enabled === "boolean" ? q.enabled : (q.status === "published")
    }));

// [新增] 套用版本序號 v1/v2…（依生成時間舊→新，不改 DB）
questions.value = applyVersionSeq(questions.value);

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
  if (!v) return;
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