/* OJ Sentinel — single-page vanilla JS.
 *
 * No build step, no external dependencies.  Student-controlled strings are
 * always inserted with textContent or HTML-escaped; source code is only ever
 * written with textContent so a malicious submission cannot inject markup.
 */
(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const enc = (value) => encodeURIComponent(String(value ?? ''));
  const toArray = (value) => (Array.isArray(value) ? value : []);

  const JOB_LABELS = { acquire: '采集', analyze: '分析', judge_batch: '批量评测' };
  const JOB_STATUS = {
    queued: '排队中', running: '运行中', succeeded: '已完成', failed: '失败', cancelled: '已取消',
  };
  const DECISION_LABELS = { copied: '确认抄袭', independent: '独立完成', pending: '待复核' };
  const REASON_LABELS = {
    burst: '短时间爆发', burst_10min: '10 分钟爆发', rapid_distinct: '提交间突变',
    first_high: '首次高分', fast_accept: '过快通过', style_drift: '风格漂移',
    ai_paste: 'AI 生成/粘贴特征', cross_user: '跨用户同源', same_code: '跨用户同源代码',
    exact_copy: '精确复制', rapid_alternating: '快速交替提交', unusual_hours: '异常时段',
  };
  const LANG_LABELS = {
    python: 'Python', py: 'Python', python3: 'Python', 'python 3': 'Python',
    c: 'C', c11: 'C', c17: 'C', gcc: 'C',
    cpp: 'C++', 'c++': 'C++', cxx: 'C++', 'g++': 'C++', 'c++17': 'C++', 'c++20': 'C++',
  };

  const state = {
    snapshots: [],
    currentKey: '',
    dataDir: '',
    analysis: null,
    analysisKey: '',
    stats: null,
    pairsResponse: null,
    anomaliesResponse: null,
    finals: null,
    session: null,
    jobs: [],
    selectedJobId: null,
    jobStatus: new Map(),
    loaded: { stats: '', analysis: '', pairs: '', anomalies: '', clusters: '', finals: '' },
    tab: 'overview',
    drawer: { mode: null, pair: null, lastFocus: null },
    logHidden: false,
    pollBusy: false,
    refreshing: false,
    projectFiles: [],
    projectResult: null,
    judgeResult: null,
    batchJob: null,
    batchResult: null,
    batchJobId: null,
    batchRendered: '',
    theme: 'light',
  };

  /* ------------------------------------------------------------------ utils */
  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function fmtNumber(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toLocaleString('zh-CN');
  }

  function fmtScore(value, digits = 3) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toFixed(digits);
  }

  function fmtPercent(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return `${(number * 100).toFixed(0)}%`;
  }

  function fmtBytes(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return '0 B';
    if (number < 1024) return `${number} B`;
    if (number < 1024 * 1024) return `${(number / 1024).toFixed(1)} KB`;
    return `${(number / 1024 / 1024).toFixed(1)} MB`;
  }

  function fmtTs(seconds) {
    const number = Number(seconds);
    if (!number) return '—';
    const date = new Date(number * 1000);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    });
  }

  function fmtDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
      });
    }
    return String(value);
  }

  function scoreClass(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return 'none';
    if (number >= 0.85) return 'high';
    if (number >= 0.65) return 'mid';
    return 'low';
  }

  function statusClass(text) {
    const value = String(text || '').toLowerCase();
    if (value.includes('accept') || value.includes('通过') || value.includes('正确')) return 'ok';
    if (value.includes('wrong') || value.includes('runtime') || value.includes('compile') || value.includes('error') || value.includes('错误')) return 'danger';
    if (value.includes('time') || value.includes('超时')) return 'warn';
    return 'info';
  }

  function statusBadge(text) {
    return `<span class="badge ${statusClass(text)}">${esc(text || '未知')}</span>`;
  }

  function languageMeta(language, filename = '') {
    const raw = String(language || '').trim();
    let lower = raw.toLowerCase();
    if (!lower && filename) {
      if (/\.py$/i.test(filename)) lower = 'python';
      else if (/\.(c|h)$/i.test(filename)) lower = 'c';
      else if (/\.(cpp|cc|cxx|hpp|hh)$/i.test(filename)) lower = 'cpp';
      else if (/\.zip$/i.test(filename)) return { key: 'other', label: 'ZIP' };
      else if (/\.(md|txt)$/i.test(filename)) return { key: 'other', label: '文本' };
    }
    if (/python|^py\d*$/.test(lower)) return { key: 'python', label: 'Python' };
    if (/c\+\+|cpp|cxx|g\+\+/.test(lower)) return { key: 'cpp', label: 'C++' };
    if (lower === 'c' || /^c\d+$/.test(lower)) return { key: 'c', label: 'C' };
    if (!raw) return { key: 'other', label: '未知语言' };
    return { key: 'other', label: raw };
  }

  function languageBadge(language, filename = '') {
    const meta = languageMeta(language, filename);
    return `<span class="badge tiny lang-badge lang-${meta.key}">${esc(meta.label)}</span>`;
  }

  function languageBadgeElement(language, filename = '') {
    const meta = languageMeta(language, filename);
    const node = el('span', `badge tiny lang-badge lang-${meta.key}`, meta.label);
    return node;
  }

  function emptyRow(colspan, text) {
    return `<tr><td colspan="${Number(colspan) || 1}" class="empty-cell">${esc(text)}</td></tr>`;
  }

  function setBusy(button, busy, busyText = '处理中…') {
    if (!button) return;
    if (busy) {
      if (!button.dataset.originalText) button.dataset.originalText = button.textContent;
      button.disabled = true;
      button.classList.add('loading');
      button.textContent = busyText;
    } else {
      button.disabled = false;
      button.classList.remove('loading');
      button.textContent = button.dataset.originalText || button.textContent;
    }
  }

  function toast(message, type = 'info', timeout = 4600) {
    const region = $('#toast-region');
    if (!region) return;
    const node = el('div', `toast ${type}`, message);
    region.appendChild(node);
    window.setTimeout(() => {
      node.style.opacity = '0';
      window.setTimeout(() => node.remove(), 220);
    }, timeout);
  }

  function errorMessage(error) {
    if (!error) return '未知错误';
    if (error.data && typeof error.data === 'object') {
      const detail = error.data.detail;
      if (typeof detail === 'string') return detail;
      if (detail && typeof detail === 'object') return detail.error || detail.detail || JSON.stringify(detail);
      if (typeof error.data.error === 'string') return error.data.error;
      if (typeof error.data.hint === 'string') return `${error.data.error || '请求失败'}：${error.data.hint}`;
    }
    return error.message || String(error);
  }

  async function api(path, options = {}) {
    const init = { credentials: 'same-origin', ...options };
    init.headers = { Accept: 'application/json', ...(options.headers || {}) };
    if (init.body !== undefined && !(init.body instanceof FormData)) {
      init.headers['Content-Type'] = 'application/json';
      if (typeof init.body !== 'string') init.body = JSON.stringify(init.body);
    }
    const response = await fetch(path, init);
    const contentType = response.headers.get('content-type') || '';
    let payload = null;
    if (contentType.includes('application/json')) {
      payload = await response.json().catch(() => null);
    } else {
      payload = await response.text().catch(() => '');
    }
    if (!response.ok) {
      const detail = payload && typeof payload === 'object' ? (payload.detail ?? payload.error) : payload;
      let message;
      if (typeof detail === 'string') message = detail;
      else if (detail && typeof detail === 'object') message = detail.detail || detail.error || JSON.stringify(detail);
      else message = `HTTP ${response.status}`;
      const error = new Error(message || `HTTP ${response.status}`);
      error.status = response.status;
      error.data = payload;
      throw error;
    }
    return payload;
  }

  function apiBase() {
    const snapshot = currentSnapshot();
    if (!snapshot) throw new Error('请先选择本地快照');
    return `/api/snapshots/${enc(snapshot.domain)}/${enc(snapshot.hid)}`;
  }

  function currentSnapshot() {
    return state.snapshots.find((snapshot) => snapshot.key === state.currentKey) || null;
  }

  function applyTheme(theme) {
    state.theme = theme === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = state.theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', state.theme === 'dark' ? '#101719' : '#f3f5f6');
    localStorage.setItem('ojs.theme', state.theme);
  }

  function setupTheme() {
    const saved = localStorage.getItem('ojs.theme');
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    applyTheme(saved || (prefersDark ? 'dark' : 'light'));
    $('#theme-toggle')?.addEventListener('click', () => {
      applyTheme(state.theme === 'dark' ? 'light' : 'dark');
    });
  }

  /* ------------------------------------------------------------- navigation */
  function setupTabs() {
    $$('.tab').forEach((tab) => {
      tab.addEventListener('click', () => switchTab(tab.dataset.tab));
      tab.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
        const tabs = $$('.tab');
        const index = tabs.indexOf(tab);
        const next = event.key === 'ArrowRight'
          ? tabs[(index + 1) % tabs.length]
          : tabs[(index - 1 + tabs.length) % tabs.length];
        next.focus();
        switchTab(next.dataset.tab);
      });
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && state.drawer.mode) closeDrawer();
    });
  }

  function switchTab(name, { load = true } = {}) {
    if (!name) return;
    state.tab = name;
    $$('.tab').forEach((tab) => {
      const active = tab.dataset.tab === name;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    $$('.panel').forEach((panel) => panel.classList.toggle('active', panel.id === `panel-${name}`));
    try { history.replaceState(null, '', `#${name}`); } catch (_) { /* ignore */ }
    if (load) ensureTabData(name).catch((error) => toast(errorMessage(error), 'error'));
  }

  async function ensureTabData(tab, force = false) {
    const snapshot = currentSnapshot();
    if (!snapshot && !['acquire', 'project'].includes(tab)) return;
    if (tab === 'overview') {
      renderSnapshotCards();
      renderOverviewEmpty();
      await loadStats(force);
      await loadAnalysisSummary(force);
      return;
    }
    if (tab === 'acquire') {
      renderSession();
      await loadJobs();
      return;
    }
    if (tab === 'pairs') {
      await loadAnalysisSummary(force);
      await loadPairs(force);
      await loadClusters(force);
      return;
    }
    if (tab === 'anomalies') {
      await loadAnalysisSummary(force);
      await loadAnomalies(force);
      return;
    }
    if (tab === 'final' || tab === 'judge') {
      await loadFinals(force);
      if (tab === 'judge') {
        renderRidOptions();
        renderBatchProblemOptions();
        await loadJobs();
        renderBatchJob();
      }
      return;
    }
    if (tab === 'project') {
      renderProjectResult(state.projectResult);
    }
  }

  /* ---------------------------------------------------------------- health */
  async function loadHealth() {
    const pill = $('#server-status');
    try {
      const data = await api('/api/health');
      state.dataDir = data.data_dir || '';
      pill.textContent = `已连接 · v${data.version || '0.1.0'}`;
      pill.className = 'status-pill ok';
      pill.title = `数据目录：${state.dataDir}`;
      const dataDir = $('#data-dir');
      if (dataDir) {
        dataDir.textContent = state.dataDir || 'data/';
        dataDir.title = state.dataDir || '';
      }
    } catch (error) {
      pill.textContent = '服务不可达';
      pill.className = 'status-pill error';
      toast(`无法连接本地服务：${errorMessage(error)}`, 'error');
    }
  }

  /* ------------------------------------------------------------- snapshots */
  async function loadSnapshots({ force = false } = {}) {
    const items = await api('/api/snapshots');
    const list = Array.isArray(items) ? items : toArray(items.snapshots);
    state.snapshots = list.map((snapshot) => ({
      ...snapshot,
      key: snapshot.key || snapshot.id || `${snapshot.domain}/${snapshot.hid}`,
    }));
    const select = $('#snapshot-select');
    if (select) {
      select.innerHTML = '';
      if (!state.snapshots.length) {
        const option = el('option', '', '尚无本地快照');
        option.value = '';
        select.appendChild(option);
        state.currentKey = '';
      } else {
        for (const snapshot of state.snapshots) {
          const option = el('option', '', `${snapshot.title || snapshot.hid} · ${snapshot.domain}`);
          option.value = snapshot.key;
          select.appendChild(option);
        }
        const saved = localStorage.getItem('ojs.lastSnapshot');
        let key = state.currentKey;
        if (!key || !state.snapshots.some((snapshot) => snapshot.key === key)) {
          key = (saved && state.snapshots.some((snapshot) => snapshot.key === saved)) ? saved : state.snapshots[0].key;
        }
        state.currentKey = key;
        select.value = key;
        localStorage.setItem('ojs.lastSnapshot', key);
      }
    }
    if (!state.currentKey) resetSnapshotState();
    updateContext();
    renderSnapshotCards();
    renderOverviewEmpty();
    if (state.currentKey) {
      if (force) {
        resetLoaded();
        await ensureTabData(state.tab, true);
      } else if (state.analysisKey !== state.currentKey) {
        await ensureTabData(state.tab, false);
      }
    }
  }

  function resetLoaded() {
    state.loaded = { stats: '', analysis: '', pairs: '', anomalies: '', clusters: '', finals: '' };
    state.analysis = null;
    state.analysisKey = '';
    state.stats = null;
    state.pairsResponse = null;
    state.anomaliesResponse = null;
    state.finals = null;
    state.judgeResult = null;
  }

  function resetSnapshotState() {
    resetLoaded();
    updateContext();
    $('#stat-tiles').innerHTML = '<div class="skeleton-tile"></div>'.repeat(4);
    $('#status-bars').innerHTML = '<p class="hint">暂无数据。</p>';
    $('#problem-rows').innerHTML = emptyRow(4, '暂无数据');
    $('#report-links').innerHTML = '<p class="hint">尚未生成报告。</p>';
    $('#pairs-rows').innerHTML = emptyRow(10, '请选择快照并运行分析');
    $('#pairs-meta').textContent = '';
    $('#anomaly-rows').innerHTML = emptyRow(6, '请选择快照并运行分析');
    $('#anomalies-meta').textContent = '';
    $('#final-head').innerHTML = '<tr><th>学生</th><th>总分</th></tr>';
    $('#final-rows').innerHTML = emptyRow(2, '暂无数据');
    $('#final-meta').textContent = '';
    $('#final-source').textContent = '加载中…';
    $('#final-source').className = 'badge muted';
    $('#judge-rid-options').innerHTML = '';
    state.batchJobId = '';
    state.batchResult = null;
    state.batchRendered = '';
    $('#batch-progress-wrap')?.classList.add('hidden');
    $('#batch-result')?.classList.add('hidden');
    updateContext();
  }

  function updateContext() {
    const snapshot = currentSnapshot();
    const badge = $('#analysis-badge');
    const analyzeButton = $('#btn-analyze');
    if (analyzeButton) analyzeButton.disabled = !snapshot;
    if (!badge) return;
    if (!snapshot) {
      badge.textContent = '未分析';
      badge.className = 'badge muted';
      return;
    }
    if (state.analysis) {
      const counts = state.analysis.counts || {};
      badge.textContent = `已分析 · ${fmtNumber(counts.pairs)} 对 / ${fmtNumber(counts.anomalies)} 异常`;
      badge.className = 'badge ok';
      badge.title = state.analysis.generated_at ? `生成于 ${fmtDate(state.analysis.generated_at)}` : '';
    } else if (snapshot.analysis_available) {
      badge.textContent = '分析文件已存在';
      badge.className = 'badge info';
      badge.title = snapshot.analysis?.updated_at ? `更新于 ${fmtDate(snapshot.analysis.updated_at)}` : '';
    } else {
      badge.textContent = '未分析';
      badge.className = 'badge muted';
      badge.title = '运行分析后可查看查重与异常';
    }
  }

  function renderSnapshotCards() {
    const container = $('#snapshot-cards');
    if (!container) return;
    container.innerHTML = '';
    for (const snapshot of state.snapshots) {
      const card = el('article', `snapshot-card${snapshot.key === state.currentKey ? ' selected' : ''}`);
      card.dataset.key = snapshot.key;
      card.tabIndex = 0;
      card.setAttribute('role', 'button');
      card.setAttribute('aria-label', `选择快照 ${snapshot.title || snapshot.hid}`);
      const counts = snapshot.counts || {};
      const analysisBadge = snapshot.analysis_available
        ? '<span class="badge ok tiny">已分析</span>'
        : '<span class="badge muted tiny">未分析</span>';
      const partial = snapshot.partial ? '<span class="badge warn tiny">采集中</span>' : '';
      card.innerHTML = `
        <div class="snapshot-title">${esc(snapshot.title || snapshot.hid)}</div>
        <div class="snapshot-ids">${esc(snapshot.domain)} / ${esc(snapshot.hid)}</div>
        <div class="snapshot-counts">
          <span>提交 <b>${fmtNumber(snapshot.records ?? counts.records)}</b></span>
          <span>详情 <b>${fmtNumber(snapshot.details ?? counts.details)}</b></span>
          <span>题目 <b>${fmtNumber(snapshot.problems ?? counts.problems)}</b></span>
          <span>学生 <b>${fmtNumber(snapshot.users ?? counts.users)}</b></span>
        </div>
        <div class="snapshot-foot">${analysisBadge}${partial}
          ${snapshot.reports && snapshot.reports.length ? `<span class="badge info tiny">${snapshot.reports.length} 个报告</span>` : ''}
        </div>`;
      card.addEventListener('click', () => selectSnapshot(snapshot.key));
      card.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          selectSnapshot(snapshot.key);
        }
      });
      container.appendChild(card);
    }
    renderOverviewEmpty();
  }

  async function selectSnapshot(key) {
    if (key === state.currentKey) return;
    state.currentKey = key;
    localStorage.setItem('ojs.lastSnapshot', key);
    const select = $('#snapshot-select');
    if (select) select.value = key;
    resetSnapshotState();
    updateContext();
    renderSnapshotCards();
    renderOverviewEmpty();
    try {
      await ensureTabData(state.tab, true);
    } catch (error) {
      toast(errorMessage(error), 'error');
    }
  }

  function renderOverviewEmpty() {
    const empty = $('#overview-empty');
    const content = $('#overview-content');
    if (!empty || !content) return;
    const isEmpty = state.snapshots.length === 0;
    empty.classList.toggle('hidden', !isEmpty);
    content.classList.toggle('hidden', isEmpty);
  }

  /* ----------------------------------------------------------------- stats */
  async function loadStats(force = false) {
    const snapshot = currentSnapshot();
    if (!snapshot) return;
    if (!force && state.loaded.stats === snapshot.key && state.stats) {
      renderStats(state.stats);
      return;
    }
    $('#stat-tiles').innerHTML = '<div class="skeleton-tile"></div>'.repeat(4);
    $('#status-bars').innerHTML = '<p class="hint">加载快照统计中…</p>';
    try {
      const data = await api(`${apiBase()}/stats`);
      if (state.currentKey !== snapshot.key) return;
      state.stats = data;
      state.loaded.stats = snapshot.key;
      renderStats(data);
    } catch (error) {
      if (state.currentKey !== snapshot.key) return;
      $('#stat-tiles').innerHTML = '<p class="hint">统计加载失败。</p>';
      $('#status-bars').innerHTML = '<p class="hint">统计加载失败。</p>';
      toast(`读取快照统计失败：${errorMessage(error)}`, 'error');
    }
  }

  function renderStats(data) {
    const snapshot = currentSnapshot();
    if (!snapshot || !data) return;
    $('#overview-caption').textContent = `${snapshot.title || ''} · ${data.domain || snapshot.domain}`;
    const tiles = [
      ['提交记录', data.records],
      ['代码详情', data.details],
      ['学生数', data.users],
      ['题目数', Object.keys(data.problems || {}).length],
      ['代码中位行数', data.code_length?.p50 ? `${fmtNumber(data.code_length.p50)} 字符` : '—'],
    ];
    $('#stat-tiles').innerHTML = tiles.map(([label, value]) => `
      <div class="stat-tile"><div class="num">${esc(typeof value === 'number' ? fmtNumber(value) : value)}</div><div class="label">${esc(label)}</div></div>
    `).join('');
    const range = data.time_range || {};
    $('#overview-time').textContent = range.from
      ? `提交时间范围：${fmtTs(range.from)} — ${fmtTs(range.to)}`
      : '提交时间范围：—';

    const statuses = Object.entries(data.status || {});
    const total = statuses.reduce((sum, [, count]) => sum + Number(count || 0), 0) || 1;
    const bars = statuses.slice(0, 12).map(([name, count]) => {
      const width = Math.max(2, Math.round((Number(count) / total) * 100));
      return `<div class="status-row"><span class="name" title="${esc(name)}">${esc(name)}</span>
        <span class="status-track"><span class="status-fill" style="width:${width}%"></span></span>
        <span class="count">${fmtNumber(count)}</span></div>`;
    });
    $('#status-bars').innerHTML = bars.length ? bars.join('') : '<p class="hint">暂无状态数据。</p>';

    const problems = data.problems || {};
    const perProblem = data.per_problem || {};
    const keys = Object.keys(perProblem).sort();
    $('#problem-rows').innerHTML = keys.length ? keys.map((key) => {
      const row = perProblem[key] || {};
      const meta = problems[key] || {};
      return `<tr>
        <td><strong>${esc(key)}</strong><div class="muted small">${esc(meta.title || '')}</div></td>
        <td>${fmtNumber(row.submissions)}</td>
        <td>${fmtNumber(row.accepted)}</td>
        <td>${fmtNumber(row.users)}</td>
      </tr>`;
    }).join('') : emptyRow(4, '暂无题目数据');
  }

  /* --------------------------------------------------------------- analysis */
  async function loadAnalysisSummary(force = false) {
    const snapshot = currentSnapshot();
    if (!snapshot) return;
    if (!force && state.analysisKey === snapshot.key) {
      renderAnalysisState();
      return;
    }
    try {
      const data = await api(`${apiBase()}/analysis`);
      if (state.currentKey !== snapshot.key) return;
      state.analysis = data;
      state.analysisKey = snapshot.key;
      state.loaded.analysis = snapshot.key;
    } catch (error) {
      if (state.currentKey !== snapshot.key) return;
      if (error.status === 404) {
        state.analysis = null;
        state.analysisKey = snapshot.key;
        state.loaded.analysis = snapshot.key;
      } else {
        toast(`读取分析摘要失败：${errorMessage(error)}`, 'error');
        return;
      }
    }
    renderAnalysisState();
  }

  function renderAnalysisState() {
    const snapshot = currentSnapshot();
    updateContext();
    const available = Boolean(state.analysis);
    $('#pairs-missing')?.classList.toggle('hidden', available);
    if (!snapshot) return;
    const thresholdInput = $('#analysis-threshold');
    if (available) {
      const threshold = Number(state.analysis.config?.threshold);
      if (Number.isFinite(threshold) && threshold >= 0 && threshold <= 1) {
        if (thresholdInput) thresholdInput.value = String(threshold);
        const minInput = $('#pairs-min-score');
        if (minInput && !minInput.dataset.touched) minInput.value = String(threshold);
      }
      const stats = state.analysis.stats || {};
      const counts = state.analysis.counts || {};
      $('#analysis-meta').textContent =
        `生成于 ${fmtDate(state.analysis.generated_at)} · ${fmtNumber(stats.profiles)} 个代码画像 · ` +
        `${fmtNumber(counts.pairs)} 对 / ${fmtNumber(counts.anomalies)} 异常 / ${fmtNumber(counts.clusters)} 聚类`;
    } else {
      $('#analysis-meta').textContent = '尚未生成分析结果。运行分析后可查看查重与异常。';
    }
    renderReports();
    renderPairProblemOptions();
    renderBatchProblemOptions();
  }

  function renderBatchProblemOptions() {
    const select = $('#batch-problem');
    if (!select) return;
    const previous = select.value;
    const keys = new Set();
    const snapshot = currentSnapshot();
    if (snapshot) toArray(snapshot.problem_keys).forEach((key) => keys.add(String(key)));
    Object.keys(state.analysis?.per_problem || {}).forEach((key) => keys.add(String(key)));
    const sorted = Array.from(keys).sort();
    select.innerHTML = '<option value="">全部题目</option>' +
      sorted.map((key) => `<option value="${esc(key)}">${esc(key)}</option>`).join('');
    if (previous && sorted.includes(previous)) select.value = previous;
  }

  function renderPairProblemOptions() {
    const select = $('#pairs-problem');
    if (!select) return;
    const previous = select.value;
    const keys = new Set();
    const snapshot = currentSnapshot();
    if (snapshot) toArray(snapshot.problem_keys).forEach((key) => keys.add(String(key)));
    Object.keys(state.analysis?.per_problem || {}).forEach((key) => keys.add(String(key)));
    const sorted = Array.from(keys).sort();
    select.innerHTML = '<option value="">全部题目</option>' +
      sorted.map((key) => `<option value="${esc(key)}">${esc(key)}</option>`).join('');
    if (previous && sorted.includes(previous)) select.value = previous;
  }

  function renderReports() {
    const snapshot = currentSnapshot();
    const container = $('#report-links');
    if (!container || !snapshot) return;
    const reports = state.analysis?.reports?.length ? state.analysis.reports : (snapshot.reports || []);
    if (!reports.length) {
      container.innerHTML = `<p class="hint">尚未生成报告。可运行 <code>ojs report --homework ${esc(snapshot.hid)}</code> 后在此下载。</p>`;
      return;
    }
    container.innerHTML = reports.map((report) => {
      const inline = report.name.endsWith('.html') ? '?inline=1' : '';
      const url = `/api/report/${enc(snapshot.domain)}/${enc(snapshot.hid)}/${enc(report.name)}${inline}`;
      return `<a class="btn ghost small" href="${esc(url)}" target="_blank" rel="noopener">${esc(report.name)} · ${fmtBytes(report.size)}</a>`;
    }).join('');
  }

  /* ----------------------------------------------------------------- pairs */
  async function loadPairs(force = false) {
    const snapshot = currentSnapshot();
    if (!snapshot) return;
    if (!state.analysis) {
      renderPairs(null);
      return;
    }
    if (!force && state.loaded.pairs === snapshot.key) {
      renderPairs(state.pairsResponse);
      return;
    }
    $('#pairs-rows').innerHTML = emptyRow(10, '加载可疑代码对…');
    const minScore = $('#pairs-min-score')?.value ?? '0';
    const problem = $('#pairs-problem')?.value ?? '';
    const limit = $('#pairs-limit')?.value ?? '200';
    const query = new URLSearchParams({ min_score: minScore || '0', limit: limit || '200' });
    if (problem) query.set('problem', problem);
    try {
      const data = await api(`${apiBase()}/pairs?${query.toString()}`);
      if (state.currentKey !== snapshot.key) return;
      state.pairsResponse = data;
      state.loaded.pairs = snapshot.key;
      renderPairs(data);
    } catch (error) {
      if (state.currentKey !== snapshot.key) return;
      if (error.status === 404) {
        state.analysis = null;
        state.analysisKey = snapshot.key;
        renderAnalysisState();
      }
      renderPairs(null, errorMessage(error));
    }
  }

  function pairDecision(pair) {
    try {
      const decisions = JSON.parse(localStorage.getItem('ojs.decisions') || '{}');
      return decisions[pairKey(pair)] || null;
    } catch (_) {
      return null;
    }
  }

  function pairKey(pair) {
    const a = String(pair.rid_a ?? '');
    const b = String(pair.rid_b ?? '');
    return [a, b].sort().join('::');
  }

  function decisionBadgeHtml(pair) {
    const decision = pairDecision(pair);
    if (!decision) return '<span class="badge muted tiny">未判定</span>';
    const classes = { copied: 'danger', independent: 'ok', pending: 'warn' };
    return `<span class="badge ${classes[decision.decision] || 'muted'} tiny">${esc(DECISION_LABELS[decision.decision] || decision.decision)}</span>`;
  }

  function renderPairs(data, errorText) {
    const body = $('#pairs-rows');
    if (!body) return;
    const pairs = toArray(data?.pairs);
    if (!data) {
      body.innerHTML = emptyRow(10, errorText || '尚未生成分析结果。运行分析后显示代码对。');
      $('#pairs-meta').textContent = '';
      return;
    }
    $('#pairs-meta').textContent =
      `共 ${fmtNumber(data.total)} 对 · 筛选后 ${fmtNumber(data.matched)} 对 · 显示 ${fmtNumber(pairs.length)} 对` +
      (data.generated_at ? ` · 生成于 ${fmtDate(data.generated_at)}` : '');
    if (!pairs.length) {
      body.innerHTML = emptyRow(10, '当前筛选条件下没有代码对。可降低最低分或检查题目筛选。');
      return;
    }
    body.innerHTML = pairs.map((pair, index) => {
      const problem = pair.problem || '';
      const userA = pair.username_a || pair.uid_a || pair.rid_a;
      const userB = pair.username_b || pair.uid_b || pair.rid_b;
      const evidenceCount = toArray(pair.evidence).length;
      return `<tr class="clickable" data-index="${index}">
        <td>${index + 1}</td>
        <td><strong>${esc(problem)}</strong></td>
        <td><div class="person"><strong>${esc(userA)}</strong><span>${esc(pair.rid_a || '')}</span></div></td>
        <td><div class="person"><strong>${esc(userB)}</strong><span>${esc(pair.rid_b || '')}</span></div></td>
        <td><span class="score-number ${scoreClass(pair.score)}">${fmtScore(pair.score)}</span></td>
        <td>${fmtPercent(pair.containment)}</td>
        <td>${fmtPercent(pair.jaccard)}</td>
        <td>${fmtPercent(pair.ast_cosine)}</td>
        <td>${evidenceCount ? `${evidenceCount} 段` : '<span class="muted">无</span>'}</td>
        <td>${decisionBadgeHtml(pair)}</td>
      </tr>`;
    }).join('');
    body.onclick = (event) => {
      const row = event.target.closest('tr[data-index]');
      if (!row) return;
      const pair = toArray(data.pairs)[Number(row.dataset.index)];
      if (pair) openPairDrawer(pair);
    };
  }

  async function loadClusters(force = false) {
    const snapshot = currentSnapshot();
    if (!snapshot || !state.analysis) {
      renderClusters(null);
      return;
    }
    if (!force && state.loaded.clusters === snapshot.key) {
      renderClusters(state.clustersResponse);
      return;
    }
    try {
      const data = await api(`${apiBase()}/clusters`);
      if (state.currentKey !== snapshot.key) return;
      state.clustersResponse = data;
      state.loaded.clusters = snapshot.key;
      renderClusters(data);
    } catch (error) {
      if (state.currentKey !== snapshot.key) return;
      renderClusters(null, errorMessage(error));
    }
  }

  function renderClusters(data, errorText) {
    const container = $('#cluster-cards');
    if (!container) return;
    const clusters = toArray(data?.clusters);
    $('#clusters-meta').textContent = data ? `共 ${fmtNumber(data.total)} 个团伙` : '';
    $('#clusters-empty').classList.toggle('hidden', Boolean(clusters.length) || !data);
    container.innerHTML = '';
    if (!data) {
      container.innerHTML = `<p class="hint">${esc(errorText || '运行分析后显示聚类结果。')}</p>`;
      return;
    }
    if (!clusters.length) {
      container.innerHTML = '';
      return;
    }
    clusters.forEach((cluster, index) => {
      const card = el('div', 'cluster-card');
      card.innerHTML = `<div class="card-head compact"><h4>团伙 ${index + 1}</h4><span class="badge warn tiny">${fmtNumber(cluster.size)} 人</span></div>
        <div class="muted small">root: <code>${esc(cluster.root || '')}</code></div>`;
      const members = el('div', 'cluster-members');
      toArray(cluster.members).forEach((rid, memberIndex) => {
        const username = toArray(cluster.usernames)[memberIndex] || '';
        const button = el('button', 'badge tiny info', username || rid);
        button.type = 'button';
        button.title = rid;
        button.addEventListener('click', () => openSubmissionDrawer(rid, currentSnapshot()));
        members.appendChild(button);
      });
      card.appendChild(members);
      container.appendChild(card);
    });
  }

  /* -------------------------------------------------------------- anomalies */
  async function loadAnomalies(force = false) {
    const snapshot = currentSnapshot();
    if (!snapshot || !state.analysis) {
      renderAnomalies(null);
      return;
    }
    if (!force && state.loaded.anomalies === snapshot.key) {
      renderAnomalies(state.anomaliesResponse);
      return;
    }
    const minScore = $('#anomalies-min-score')?.value ?? '0';
    $('#anomaly-rows').innerHTML = emptyRow(6, '加载异常行为…');
    try {
      const data = await api(`${apiBase()}/anomalies?min_score=${enc(minScore || '0')}&limit=500`);
      if (state.currentKey !== snapshot.key) return;
      state.anomaliesResponse = data;
      state.loaded.anomalies = snapshot.key;
      renderAnomalies(data);
    } catch (error) {
      if (state.currentKey !== snapshot.key) return;
      renderAnomalies(null, errorMessage(error));
    }
  }

  function renderAnomalies(data, errorText) {
    const body = $('#anomaly-rows');
    if (!body) return;
    const anomalies = toArray(data?.anomalies);
    $('#anomalies-meta').textContent = data
      ? `显示 ${fmtNumber(anomalies.length)} / ${fmtNumber(data.total)} 人` + (data.generated_at ? ` · 生成于 ${fmtDate(data.generated_at)}` : '')
      : '';
    if (!data) {
      body.innerHTML = emptyRow(6, errorText || '尚未生成分析结果。');
      return;
    }
    if (!anomalies.length) {
      body.innerHTML = emptyRow(6, '没有达到阈值的异常用户。');
      return;
    }
    body.innerHTML = anomalies.map((anomaly) => {
      const user = anomaly.display_name || anomaly.username || `UID ${anomaly.uid}`;
      const reasons = toArray(anomaly.reasons).map((reason) => {
        if (typeof reason === 'string') return `<span class="badge warn tiny">${esc(reason)}</span>`;
        const code = reason.code || reason.kind || 'signal';
        const label = REASON_LABELS[code] || code;
        const detail = reason.detail || '';
        return `<span class="badge warn tiny" title="${esc(detail)}">${esc(label)}${reason.weight != null ? ` ${fmtScore(reason.weight, 2)}` : ''}</span>`;
      }).join(' ');
      const metrics = Object.entries(anomaly.metrics || {})
        .filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object')
        .slice(0, 8)
        .map(([key, value]) => `<span class="muted small">${esc(key)}=${esc(value)}</span>`)
        .join(' · ');
      const evidence = toArray(anomaly.evidence).slice(0, 8).map((rid) =>
        `<button type="button" class="badge tiny info" data-rid="${esc(rid)}" title="查看提交详情">${esc(String(rid).slice(0, 10))}</button>`
      ).join(' ');
      return `<tr>
        <td><div class="person"><strong>${esc(user)}</strong><span>${esc(anomaly.username || '')}</span></div></td>
        <td class="uid">${esc(anomaly.uid ?? '')}</td>
        <td><span class="score-number ${Number(anomaly.score) >= 0.7 ? 'high' : Number(anomaly.score) >= 0.45 ? 'mid' : 'low'}">${fmtScore(anomaly.score, 2)}</span></td>
        <td><div class="badges-wrap">${reasons || '<span class="muted">—</span>'}</div></td>
        <td class="muted small">${metrics || '—'}</td>
        <td>${evidence || '<span class="muted">—</span>'}</td>
      </tr>`;
    }).join('');
    body.onclick = (event) => {
      const button = event.target.closest('button[data-rid]');
      if (!button) return;
      openSubmissionDrawer(button.dataset.rid, currentSnapshot());
    };
  }

  /* ---------------------------------------------------------------- finals */
  async function loadFinals(force = false) {
    const snapshot = currentSnapshot();
    if (!snapshot) return;
    if (!force && state.loaded.finals === snapshot.key && state.finals) {
      renderFinals(state.finals);
      return;
    }
    $('#final-rows').innerHTML = emptyRow(2, '加载成绩…');
    try {
      const data = await api(`${apiBase()}/final-scores`);
      if (state.currentKey !== snapshot.key) return;
      state.finals = data;
      state.loaded.finals = snapshot.key;
      renderFinals(data);
    } catch (error) {
      if (state.currentKey !== snapshot.key) return;
      $('#final-rows').innerHTML = emptyRow(2, '成绩加载失败');
      $('#final-meta').textContent = errorMessage(error);
      toast(`读取成绩失败：${errorMessage(error)}`, 'error');
    }
  }

  function renderFinals(data) {
    const source = $('#final-source');
    const head = $('#final-head');
    const body = $('#final-rows');
    if (!head || !body) return;
    if (!data) return;
    const problems = toArray(data.problems);
    const students = toArray(data.students || data.final_scores);
    source.textContent = data.source === 'analysis' ? '来源：analysis.json（每题最后一次提交）' : '来源：本地 records 计算（每题最后一次提交）';
    source.className = `badge ${data.source === 'analysis' ? 'ok' : 'info'}`;
    $('#final-empty').classList.toggle('hidden', Boolean(students.length));
    head.innerHTML = `<tr><th>学生</th>${problems.map((problem) =>
      `<th><div class="problem-head"><b>${esc(problem.key)}</b><span>${esc(problem.title || '')} · ${fmtNumber(problem.points)} 分</span></div></th>`
    ).join('')}<th>总分</th></tr>`;
    if (!students.length) {
      body.innerHTML = emptyRow(problems.length + 2, '暂无成绩数据');
      return;
    }
    body.innerHTML = students.map((student) => {
      const name = student.display_name || student.username || `UID ${student.uid}`;
      const cells = problems.map((problem) => {
        const cell = (student.problems || {})[problem.key] || {};
        const score = cell.score ?? 0;
        if (!cell.rid) {
          return `<td><span class="score-cell none" title="${esc(cell.status || '未提交')}">—</span></td>`;
        }
        return `<td><div class="cell-wrap"><button type="button" class="score-cell ${scoreClass(Number(score) / Math.max(1, Number(problem.points || 100)))}" data-rid="${esc(cell.rid)}" title="${esc(cell.status || '')} · ${esc(cell.submit_at || '')}">${esc(score)}</button><span class="cell-status">${esc(cell.status || '')}</span></div></td>`;
      }).join('');
      return `<tr>
        <td><div class="person"><strong>${esc(name)}</strong><span>${esc(student.username || '')}${student.uid ? ` · uid ${esc(student.uid)}` : ''}</span></div></td>
        ${cells}
        <td><strong class="score-number">${esc(student.total ?? 0)}</strong><span class="muted small"> / ${fmtNumber(student.total_points ?? 0)}</span></td>
      </tr>`;
    }).join('');
    body.onclick = (event) => {
      const button = event.target.closest('button[data-rid]');
      if (!button) return;
      openSubmissionDrawer(button.dataset.rid, currentSnapshot());
    };
    const cellsCount = students.reduce((sum, student) => sum + problems.filter((problem) => (student.problems || {})[problem.key]?.rid).length, 0);
    $('#final-meta').textContent = `${fmtNumber(students.length)} 名学生 · ${fmtNumber(problems.length)} 道题 · ${fmtNumber(cellsCount)} 条最后一次提交。` +
      ' 分数来自最后一次提交的状态（含未通过）；点击分数可查看代码。';
    renderRidOptions();
  }

  function renderRidOptions() {
    const datalist = $('#judge-rid-options');
    if (!datalist) return;
    datalist.innerHTML = '';
    const finals = state.finals;
    if (!finals) return;
    const problems = toArray(finals.problems);
    for (const student of toArray(finals.students || finals.final_scores)) {
      for (const problem of problems) {
        const cell = (student.problems || {})[problem.key];
        if (!cell || !cell.rid) continue;
        const option = document.createElement('option');
        option.value = cell.rid;
        option.label = `${student.display_name || student.username || student.uid} · ${problem.key} · ${cell.score ?? 0} 分`;
        datalist.appendChild(option);
      }
    }
  }

  /* --------------------------------------------------------------- session */
  async function loadSession() {
    try {
      const data = await api('/api/session');
      state.session = data;
    } catch (error) {
      if (error.status === 401) state.session = null;
      else toast(`读取登录态失败：${errorMessage(error)}`, 'error');
    }
    renderSession();
  }

  function renderSession() {
    const form = $('#login-form');
    const info = $('#session-info');
    const badge = $('#acquire-session-badge');
    const acquireButton = $('#btn-acquire');
    const repairButton = $('#btn-repair');
    if (!form || !info) return;
    const authenticated = Boolean(state.session?.user);
    form.classList.toggle('hidden', authenticated);
    info.classList.toggle('hidden', !authenticated);
    if (badge) {
      badge.textContent = authenticated ? '已登录' : '未登录';
      badge.className = `badge ${authenticated ? 'ok' : 'muted'}`;
    }
    if (acquireButton) acquireButton.disabled = !authenticated;
    if (repairButton) repairButton.disabled = !authenticated;
    if (!authenticated) return;
    const user = state.session.user;
    $('#session-avatar').textContent = (user.username || 'T').slice(0, 1).toUpperCase();
    $('#session-user').textContent = user.username || '教师账号';
    $('#session-meta').textContent = `${user.base_url || ''} · ${user.domain || ''}`;
    $('#login-base-url').value = user.base_url || $('#login-base-url').value;
    $('#login-domain').value = user.domain || $('#login-domain').value;
    $('#login-username').value = user.username || $('#login-username').value;
    $('#homework-error').classList.toggle('hidden', !state.session.homeworks_error);
    $('#homework-error').textContent = state.session.homeworks_error || '';
    renderHomeworks();
  }

  function renderHomeworks() {
    const container = $('#homework-list');
    if (!container) return;
    const homeworks = toArray(state.session?.homeworks);
    container.innerHTML = '';
    if (!homeworks.length) {
      container.innerHTML = '<p class="hint">没有列出作业。可点击“刷新作业”，或手动填写作业 ID。</p>';
      return;
    }
    homeworks.forEach((homework) => {
      const button = el('button', 'homework-item');
      button.type = 'button';
      button.innerHTML = `<strong>${esc(homework.title || homework.id || '未命名作业')}</strong><span>${esc(homework.id || '')}</span>`;
      button.addEventListener('click', () => {
        $$('.homework-item').forEach((item) => item.classList.remove('selected'));
        button.classList.add('selected');
        $('#acquire-hid').value = homework.id || '';
      });
      container.appendChild(button);
    });
  }

  async function doLogin(event) {
    event.preventDefault();
    const button = $('#login-submit');
    const payload = {
      base_url: $('#login-base-url').value.trim(),
      domain: $('#login-domain').value.trim(),
      username: $('#login-username').value.trim(),
      password: $('#login-password').value,
    };
    if (!payload.username || !payload.password) {
      toast('请输入用户名和密码。', 'warn');
      return;
    }
    setBusy(button, true, '登录中…');
    try {
      const data = await api('/api/session', { method: 'POST', body: payload });
      state.session = data;
      $('#login-password').value = '';
      renderSession();
      toast(`已登录：${data.user?.username || payload.username}`, 'success');
      if (data.warning) toast(data.warning, 'warn');
    } catch (error) {
      toast(`登录失败：${errorMessage(error)}`, 'error', 6500);
      $('#login-password').select();
    } finally {
      setBusy(button, false);
    }
  }

  async function doLogout() {
    try {
      await api('/api/session', { method: 'DELETE' });
    } catch (error) {
      toast(`退出失败：${errorMessage(error)}`, 'error');
    }
    state.session = null;
    renderSession();
    toast('已退出登录，内存中的 OJ 会话已清除。', 'info');
  }

  async function refreshHomeworks() {
    const button = $('#btn-homeworks-refresh');
    setBusy(button, true, '刷新中…');
    try {
      const data = await api('/api/session?refresh=1');
      state.session = data;
      renderSession();
      if (data.homeworks_error) toast(data.homeworks_error, 'warn');
      else toast(`已刷新 ${toArray(data.homeworks).length} 个作业`, 'success');
    } catch (error) {
      toast(`刷新作业失败：${errorMessage(error)}`, 'error');
    } finally {
      setBusy(button, false);
    }
  }

  /* ----------------------------------------------------------------- jobs */
  async function loadJobs() {
    try {
      const list = await api('/api/jobs');
      handleJobs(Array.isArray(list) ? list : toArray(list.jobs), false);
    } catch (error) {
      toast(`读取任务失败：${errorMessage(error)}`, 'error');
    }
  }

  function handleJobs(list, detectFinished = true) {
    const previous = state.jobStatus;
    let finished = false;
    for (const job of list) {
      const before = previous.get(job.id);
      if (detectFinished && before && ['queued', 'running'].includes(before) && !['queued', 'running'].includes(job.status)) {
        finished = true;
      }
    }
    state.jobStatus = new Map(list.map((job) => [job.id, job.status]));
    state.jobs = list;
    if (!state.selectedJobId || !list.some((job) => job.id === state.selectedJobId)) {
      state.selectedJobId = list.find((job) => ['queued', 'running'].includes(job.status))?.id || list[0]?.id || null;
      state.logHidden = false;
    }
    renderJobIndicator();
    if (state.tab === 'acquire') {
      renderJobs();
      renderJobLog();
    } else {
      renderJobs();
    }
    renderBatchJob();
    if (finished) {
      toast('后台任务已完成，正在刷新本地数据…', 'success');
      refreshAfterJob().catch((error) => toast(errorMessage(error), 'error'));
    }
  }

  async function pollJobs() {
    if (state.pollBusy) return;
    state.pollBusy = true;
    try {
      const list = await api('/api/jobs');
      handleJobs(Array.isArray(list) ? list : toArray(list.jobs), true);
    } catch (_) {
      /* polling failures are silent; the status pill covers server loss */
    } finally {
      state.pollBusy = false;
    }
  }

  async function refreshAfterJob() {
    if (state.refreshing) return;
    state.refreshing = true;
    try {
      await loadSnapshots({ force: false });
      state.loaded.analysis = '';
      state.loaded.pairs = '';
      state.loaded.anomalies = '';
      state.loaded.clusters = '';
      state.loaded.stats = '';
      state.loaded.finals = '';
      if (currentSnapshot()) {
        await Promise.allSettled([loadAnalysisSummary(true), loadStats(true), loadFinals(true)]);
        await ensureTabData(state.tab, true);
      }
    } finally {
      state.refreshing = false;
    }
  }

  function renderJobIndicator() {
    const indicator = $('#job-indicator');
    if (!indicator) return;
    const active = state.jobs.filter((job) => ['queued', 'running'].includes(job.status));
    indicator.hidden = !active.length;
    indicator.textContent = active.length
      ? `${active.length} 个后台任务运行中 · 点击查看`
      : '';
    indicator.onclick = () => switchTab('acquire');
    const analyzeButton = $('#btn-analyze');
    if (analyzeButton) {
      analyzeButton.disabled = !currentSnapshot() || active.some((job) => job.kind === 'analyze');
    }
  }

  function renderJobs() {
    const body = $('#job-rows');
    if (!body) return;
    const jobs = state.jobs;
    $('#jobs-meta').textContent = jobs.length ? `${jobs.length} 个任务（显示最近 50 个）` : '';
    if (!jobs.length) {
      body.innerHTML = emptyRow(5, '暂无任务。开始采集或分析后会在这里显示进度。');
      return;
    }
    body.innerHTML = jobs.map((job) => {
      const status = job.status || 'queued';
      const selected = job.id === state.selectedJobId ? ' style="background:color-mix(in srgb, var(--accent-soft) 55%, transparent)"' : '';
      return `<tr class="clickable" data-job-id="${esc(job.id)}"${selected}>
        <td>${esc(JOB_LABELS[job.kind] || job.kind)}</td>
        <td><span class="job-status ${esc(status)}">${esc(JOB_STATUS[status] || status)}</span></td>
        <td><div class="job-progress"><div class="progress-track"><div class="progress-fill" style="width:${Math.max(0, Math.min(100, Number(job.progress) || 0))}%"></div></div><div class="progress-label">${Number(job.progress) || 0}%</div></div></td>
        <td class="muted small">${esc(job.message || job.error || '')}</td>
        <td class="muted small">${esc(fmtDate(job.created_at))}</td>
      </tr>`;
    }).join('');
    body.onclick = (event) => {
      const row = event.target.closest('tr[data-job-id]');
      if (!row) return;
      state.selectedJobId = row.dataset.jobId;
      state.logHidden = false;
      renderJobs();
      renderJobLog();
    };
  }

  function renderJobLog() {
    const view = $('#job-log');
    const title = $('#job-log-title');
    if (!view) return;
    const job = state.jobs.find((item) => item.id === state.selectedJobId);
    if (!job) {
      view.textContent = '暂无日志。任务开始后会自动刷新。';
      if (title) title.textContent = '';
      return;
    }
    if (title) title.textContent = `${JOB_LABELS[job.kind] || job.kind} · ${job.id} · ${JOB_STATUS[job.status] || job.status}`;
    if (state.logHidden) {
      view.textContent = '日志显示已清空（服务端仍保留最近 500 行）。重新选择任务可恢复。';
      return;
    }
    const lines = toArray(job.log);
    view.textContent = lines.length ? lines.join('\n') : '任务已创建，等待日志…';
    if (view.dataset.autoScroll !== 'off') view.scrollTop = view.scrollHeight;
  }

  function upsertJob(job) {
    if (!job || !job.id) return;
    const index = state.jobs.findIndex((item) => item.id === job.id);
    if (index >= 0) state.jobs[index] = job;
    else state.jobs.unshift(job);
    state.selectedJobId = job.id;
    state.logHidden = false;
    renderJobIndicator();
    renderJobs();
    renderJobLog();
  }

  /* ------------------------------------------------------------- acquire */
  async function doAcquire(repair = false) {
    if (!state.session?.user) {
      toast('请先登录 Hydro OJ。', 'warn');
      return;
    }
    const homeworkId = $('#acquire-hid').value.trim();
    if (!homeworkId) {
      toast('请填写或选择作业 ID。', 'warn');
      $('#acquire-hid').focus();
      return;
    }
    const button = repair ? $('#btn-repair') : $('#btn-acquire');
    const options = {
      workers: Number($('#acquire-workers').value) || 6,
      download_testdata: $('#acquire-testdata').checked,
      download_records: $('#acquire-records').checked,
      force: $('#acquire-force').checked,
      repair,
    };
    const limit = Number($('#acquire-limit').value);
    if (Number.isFinite(limit) && limit > 0) options.limit_records = limit;
    setBusy(button, true, repair ? '修复任务创建中…' : '采集任务创建中…');
    try {
      const job = await api('/api/acquire', { method: 'POST', body: { homework_id: homeworkId, options } });
      upsertJob(job);
      toast(`${repair ? '修复' : '采集'}任务已开始，可在下方查看实时日志。`, 'success');
      pollJobs();
    } catch (error) {
      toast(`${repair ? '修复' : '采集'}任务创建失败：${errorMessage(error)}`, 'error', 6500);
    } finally {
      setBusy(button, false);
    }
  }

  async function doAnalyze() {
    const snapshot = currentSnapshot();
    if (!snapshot) {
      toast('请先选择本地快照。', 'warn');
      return;
    }
    const threshold = Number($('#analysis-threshold')?.value || 0.6);
    if (!(threshold > 0 && threshold <= 1)) {
      toast('阈值必须在 0 到 1 之间。', 'warn');
      return;
    }
    const button = $('#btn-analyze');
    const panelButton = $('#btn-analyze-panel');
    setBusy(button, true, '创建分析任务…');
    setBusy(panelButton, true, '创建分析任务…');
    try {
      const job = await api('/api/analyze', {
        method: 'POST',
        body: { domain: snapshot.domain, hid: snapshot.hid, threshold },
      });
      upsertJob(job);
      toast('分析任务已开始，进度可在「采集」页的任务日志查看。', 'success');
      pollJobs();
    } catch (error) {
      toast(`分析任务创建失败：${errorMessage(error)}`, 'error', 6500);
    } finally {
      setBusy(button, false);
      setBusy(panelButton, false);
      renderJobIndicator();
    }
  }

  /* ------------------------------------------------------------ drawer code */
  function splitCodeLines(code) {
    const lines = String(code ?? '').replace(/\r\n?/g, '\n').split('\n');
    if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
    return lines;
  }

  function mergeRanges(ranges) {
    const sorted = ranges
      .map(([start, end]) => [Math.max(1, Number(start) || 0), Math.max(1, Number(end) || 0)])
      .filter(([start, end]) => start > 0 && end >= start)
      .sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const range of sorted) {
      const last = merged[merged.length - 1];
      if (last && range[0] <= last[1] + 1) last[1] = Math.max(last[1], range[1]);
      else merged.push([...range]);
    }
    return merged;
  }

  function rangesToSet(ranges) {
    const set = new Set();
    for (const [start, end] of mergeRanges(ranges)) {
      for (let line = start; line <= end; line += 1) set.add(line);
    }
    return set;
  }

  function computeLineDiffRanges(codeA, codeB) {
    const a = splitCodeLines(codeA);
    const b = splitCodeLines(codeB);
    const n = a.length;
    const m = b.length;
    if (!n || !m || n * m > 1500000) return null;
    const normA = a.map((line) => line.trim());
    const normB = b.map((line) => line.trim());
    const stride = m + 1;
    const dp = new Uint16Array((n + 1) * (m + 1));
    for (let i = n - 1; i >= 0; i -= 1) {
      for (let j = m - 1; j >= 0; j -= 1) {
        if (normA[i] && normA[i] === normB[j]) {
          dp[i * stride + j] = dp[(i + 1) * stride + (j + 1)] + 1;
        } else {
          const down = dp[(i + 1) * stride + j];
          const right = dp[i * stride + (j + 1)];
          dp[i * stride + j] = down >= right ? down : right;
        }
      }
    }
    const matches = [];
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (normA[i] && normA[i] === normB[j]) {
        matches.push([i, j]);
        i += 1;
        j += 1;
      } else if (dp[(i + 1) * stride + j] >= dp[i * stride + (j + 1)]) {
        i += 1;
      } else {
        j += 1;
      }
    }
    const blocks = [];
    let block = null;
    for (const [ai, bj] of matches) {
      if (block && ai === block[1] + 1 && bj === block[3] + 1) {
        block[1] = ai;
        block[3] = bj;
      } else {
        block = [ai, ai, bj, bj];
        blocks.push(block);
      }
    }
    const useful = blocks.filter((blockItem) => blockItem[1] - blockItem[0] + 1 >= 2);
    if (!useful.length) return null;
    return {
      a: useful.map((blockItem) => [blockItem[0] + 1, blockItem[1] + 1]),
      b: useful.map((blockItem) => [blockItem[2] + 1, blockItem[3] + 1]),
    };
  }

  function evidenceRanges(pair) {
    const rangesA = [];
    const rangesB = [];
    for (const evidence of toArray(pair.evidence)) {
      if (!evidence || typeof evidence !== 'object') continue;
      const aStart = Number(evidence.a_start);
      const aEnd = Number(evidence.a_end);
      const bStart = Number(evidence.b_start);
      const bEnd = Number(evidence.b_end);
      if (aStart > 0 && aEnd >= aStart) rangesA.push([aStart, aEnd]);
      if (bStart > 0 && bEnd >= bStart) rangesB.push([bStart, bEnd]);
    }
    return { a: mergeRanges(rangesA), b: mergeRanges(rangesB) };
  }

  function buildCodeView(code, ranges, onlyRanges, emptyText = '（无代码）') {
    const lines = splitCodeLines(code);
    const container = el('div', 'code-scroll');
    if (!lines.length) {
      container.appendChild(el('div', 'code-empty', emptyText));
      return container;
    }
    const marked = rangesToSet(ranges);
    const view = el('div', 'code-view');
    if (onlyRanges) {
      const wanted = Array.from(marked).filter((line) => line >= 1 && line <= lines.length).sort((a, b) => a - b);
      if (!wanted.length) {
        container.appendChild(el('div', 'code-empty', '无行级证据；请切换到「全文」人工比对。'));
        return container;
      }
      let previous = null;
      for (const lineNumber of wanted) {
        if (previous !== null && lineNumber > previous + 1) {
          const ellipsis = el('div', 'code-line ellipsis');
          ellipsis.appendChild(el('span', 'ln', '⋯'));
          ellipsis.appendChild(el('span', 'lc', `省略 ${lineNumber - previous - 1} 行`));
          view.appendChild(ellipsis);
        }
        const row = el('div', 'code-line hl');
        row.appendChild(el('span', 'ln', String(lineNumber)));
        row.appendChild(el('span', 'lc', lines[lineNumber - 1]));
        view.appendChild(row);
        previous = lineNumber;
      }
    } else {
      for (let index = 0; index < lines.length; index += 1) {
        const lineNumber = index + 1;
        const row = el('div', `code-line${marked.has(lineNumber) ? ' hl' : ''}`);
        row.appendChild(el('span', 'ln', String(lineNumber)));
        row.appendChild(el('span', 'lc', lines[index]));
        view.appendChild(row);
      }
    }
    container.appendChild(view);
    return container;
  }

  function codePane(sideLabel, submission, code, ranges, onlyRanges) {
    const summary = submission?.summary || {};
    const detail = submission?.detail || {};
    const pane = el('section', 'code-pane');
    const head = el('div', 'code-pane-head');
    const name = summary.display_name || summary.username || `提交 ${submission?.rid || ''}`;
    const who = el('span', 'who', `${sideLabel} · ${name}`);
    head.appendChild(who);
    head.appendChild(languageBadgeElement(detail.language || summary.language || '', ''));
    head.appendChild(el('span', `badge tiny ${statusClass(summary.status_text || summary.status)}`, summary.status_text || summary.status || '状态未知'));
    head.appendChild(el('span', 'meta', `RID ${submission?.rid || ''} · ${fmtScore(summary.score, 0)} 分 · ${fmtTs(summary.submit_ts)}`));
    const copy = el('button', 'btn ghost small', '复制');
    copy.type = 'button';
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(String(code ?? ''));
        toast('代码已复制到剪贴板。', 'success', 2400);
      } catch (_) {
        toast('浏览器拒绝剪贴板访问，请手动选择复制。', 'warn');
      }
    });
    head.appendChild(copy);
    pane.appendChild(head);
    pane.appendChild(buildCodeView(code, ranges, onlyRanges));
    const legend = el('div', 'code-legend');
    legend.appendChild(el('span', 'legend-swatch'));
    legend.appendChild(el('span', '', ranges.length ? '高亮行为分析给出的相似行范围' : '无行级证据，请人工比对'));
    pane.appendChild(legend);
    return pane;
  }

  function renderEvidenceList(pair, container) {
    const evidence = toArray(pair.evidence);
    if (!evidence.length) {
      container.innerHTML = `<p class="hint">${esc('无行级证据。已尝试在前端按行比对生成高亮；若仍为空，请使用全文人工比对。')}</p>`;
      return;
    }
    const list = el('div', 'evidence-list');
    evidence.slice(0, 12).forEach((item) => {
      const wrapper = el('div', 'evidence-item');
      const head = el('div', 'evidence-head');
      head.innerHTML = `<span>A ${esc(item.a_start)}–${esc(item.a_end)} 行 ↔ B ${esc(item.b_start)}–${esc(item.b_end)} 行</span><span>${fmtNumber(item.lines)} 行</span>`;
      const pre = el('pre', '', item.snippet || '（空片段）');
      wrapper.appendChild(head);
      wrapper.appendChild(pre);
      list.appendChild(wrapper);
    });
    container.appendChild(list);
    if (evidence.length > 12) {
      container.appendChild(el('p', 'hint', `仅显示前 12 段证据，共 ${evidence.length} 段。`));
    }
  }

  /* ----------------------------------------------------------------- drawer */
  function openDrawerShell(title, subtitle) {
    const drawer = $('#drawer');
    const overlay = $('#drawer-overlay');
    state.drawer.lastFocus = document.activeElement;
    $('#drawer-title').textContent = title;
    $('#drawer-subtitle').textContent = subtitle || '';
    $('#drawer-body').innerHTML = '';
    drawer.hidden = false;
    overlay.hidden = false;
    document.body.classList.add('drawer-open');
    $('#drawer-close').focus({ preventScroll: true });
  }

  function closeDrawer() {
    const drawer = $('#drawer');
    const overlay = $('#drawer-overlay');
    if (!drawer || drawer.hidden) return;
    drawer.hidden = true;
    overlay.hidden = true;
    document.body.classList.remove('drawer-open');
    $('#drawer-body').innerHTML = '';
    state.drawer = { mode: null, pair: null, lastFocus: state.drawer.lastFocus };
    try { state.drawer.lastFocus?.focus({ preventScroll: true }); } catch (_) { /* ignore */ }
  }

  function drawerMessage(text, type = 'info') {
    const body = $('#drawer-body');
    body.innerHTML = '';
    body.appendChild(el('div', `notice ${type}`, text));
  }

  async function openPairDrawer(pair) {
    if (!pair) return;
    const snapshot = currentSnapshot();
    if (!snapshot) return;
    const problemTitle = state.analysis?.per_problem?.[pair.problem] ? pair.problem : pair.problem;
    openDrawerShell(`代码比对 · ${problemTitle}`, `A ${pair.username_a || pair.rid_a} ↔ B ${pair.username_b || pair.rid_b} · 综合分 ${fmtScore(pair.score)}`);
    state.drawer = { mode: 'pair', pair, snapshot, lastFocus: state.drawer.lastFocus };
    drawerMessage('正在读取双方完整源码与证据…');
    let payloadA = null;
    let payloadB = null;
    const [resultA, resultB] = await Promise.allSettled([
      api(`/api/submissions/${enc(pair.rid_a)}?domain=${enc(snapshot.domain)}&hid=${enc(snapshot.hid)}`),
      api(`/api/submissions/${enc(pair.rid_b)}?domain=${enc(snapshot.domain)}&hid=${enc(snapshot.hid)}`),
    ]);
    if (resultA.status === 'fulfilled') payloadA = resultA.value;
    if (resultB.status === 'fulfilled') payloadB = resultB.value;
    if (!payloadA && !payloadB) {
      drawerMessage(`读取提交失败：${errorMessage(resultA.reason || resultB.reason)}`, 'danger');
      return;
    }
    if (!payloadA) drawerMessage(`A 侧源码读取失败：${errorMessage(resultA.reason)}`, 'warn');
    if (!payloadB) drawerMessage(`B 侧源码读取失败：${errorMessage(resultB.reason)}`, 'warn');
    renderPairDrawer(pair, payloadA, payloadB);
  }

  function renderPairDrawer(pair, payloadA, payloadB) {
    const body = $('#drawer-body');
    body.innerHTML = '';
    const codeA = payloadA?.detail?.code || '';
    const codeB = payloadB?.detail?.code || '';
    const analysisRanges = evidenceRanges(pair);
    let diffRanges = null;
    let evidenceSource = 'analysis';
    if (!analysisRanges.a.length && !analysisRanges.b.length) {
      diffRanges = computeLineDiffRanges(codeA, codeB);
      evidenceSource = diffRanges ? 'client' : 'none';
    }
    const ranges = analysisRanges.a.length || analysisRanges.b.length
      ? analysisRanges
      : (diffRanges ? { a: diffRanges.a, b: diffRanges.b } : { a: [], b: [] });

    const metrics = document.createElement('div');
    metrics.className = 'drawer-metrics';
    const metricData = [
      ['综合分', fmtScore(pair.score), scoreClass(pair.score)],
      ['包含度', fmtPercent(pair.containment), ''],
      ['Jaccard', fmtPercent(pair.jaccard), ''],
      ['AST 相似度', fmtPercent(pair.ast_cosine), ''],
      ['共享指纹', fmtNumber(pair.shared), ''],
      ['精确复制', pair.exact ? '是' : '否', pair.exact ? 'high' : ''],
    ];
    metricData.forEach(([label, value, cls]) => {
      const card = el('div', 'metric-card');
      card.appendChild(el('div', 'k', label));
      const valueNode = el('div', `v ${cls}`, value);
      card.appendChild(valueNode);
      metrics.appendChild(card);
    });
    body.appendChild(metrics);

    const layout = el('div', 'drawer-layout');
    const main = el('div', 'drawer-main');
    const side = el('div', 'drawer-side');
    layout.appendChild(main);
    layout.appendChild(side);
    body.appendChild(layout);

    /* main: code */
    const codeSection = el('section', 'drawer-section');
    const toolbar = el('div', 'code-toolbar');
    toolbar.appendChild(el('h3', '', '并排源码'));
    const segmented = el('div', 'segmented');
    const fullButton = el('button', 'active', '全文');
    fullButton.type = 'button';
    const similarButton = el('button', '', '只看相似片段');
    similarButton.type = 'button';
    segmented.appendChild(fullButton);
    segmented.appendChild(similarButton);
    toolbar.appendChild(segmented);
    const sourceNote = el('span', 'hint',
      evidenceSource === 'analysis'
        ? `行级证据：${toArray(pair.evidence).length} 段`
        : evidenceSource === 'client'
          ? '分析未提供行级证据，已在前端按行比对生成高亮（仅供参考）'
          : '分析未提供行级证据；可切换全文人工比对');
    toolbar.appendChild(sourceNote);
    codeSection.appendChild(toolbar);

    const codeGrid = el('div', 'code-grid');
    codeSection.appendChild(codeGrid);
    main.appendChild(codeSection);

    const renderCodes = (onlyRanges) => {
      codeGrid.innerHTML = '';
      codeGrid.appendChild(codePane('A', payloadA, codeA, ranges.a, onlyRanges));
      codeGrid.appendChild(codePane('B', payloadB, codeB, ranges.b, onlyRanges));
    };
    renderCodes(false);
    fullButton.addEventListener('click', () => {
      fullButton.classList.add('active');
      similarButton.classList.remove('active');
      renderCodes(false);
    });
    similarButton.addEventListener('click', () => {
      similarButton.classList.add('active');
      fullButton.classList.remove('active');
      renderCodes(true);
    });

    /* main: evidence snippets */
    const evidenceSection = el('section', 'drawer-section');
    evidenceSection.appendChild(el('h3', '', '证据片段'));
    const evidenceBox = el('div');
    renderEvidenceList(pair, evidenceBox);
    evidenceSection.appendChild(evidenceBox);
    main.appendChild(evidenceSection);

    /* side: reasons + decision + AI */
    const reasonCard = el('div', 'side-card');
    reasonCard.appendChild(el('h4', '', '判定依据'));
    const reasons = toArray(pair.reasons);
    if (reasons.length) {
      const list = el('ul', 'reason-list');
      reasons.forEach((reason) => list.appendChild(el('li', '', typeof reason === 'string' ? reason : JSON.stringify(reason))));
      reasonCard.appendChild(list);
    } else {
      reasonCard.appendChild(el('p', 'hint', `共享 ${fmtNumber(pair.shared)} 个指纹；相似度 ${fmtScore(pair.score)}。`));
    }
    const metricList = el('dl', 'metric-list');
    [
      ['题目', pair.problem || '—'],
      ['综合分', fmtScore(pair.score)],
      ['包含度', fmtPercent(pair.containment)],
      ['Jaccard', fmtPercent(pair.jaccard)],
      ['AST', fmtPercent(pair.ast_cosine)],
      ['方法', pair.method || 'winnowing+ast'],
      ['同用户', pair.same_user ? '是' : '否'],
    ].forEach(([key, value]) => {
      metricList.appendChild(el('dt', '', key));
      metricList.appendChild(el('dd', '', value));
    });
    reasonCard.appendChild(el('h4', '', '指标'));
    reasonCard.appendChild(metricList);
    side.appendChild(reasonCard);

    const decisionCard = el('div', 'side-card');
    decisionCard.appendChild(el('h4', '', '人工确认'));
    decisionCard.appendChild(el('p', 'hint', '仅保存在当前浏览器 localStorage，不写入服务器；用于教师备课与复核。'));
    const decisionWrap = el('div', 'decision-buttons');
    const note = el('textarea', 'decision-note');
    note.placeholder = '可选：复核备注';
    const meta = el('div', 'decision-meta');
    const buttons = {};
    const decisions = [
      ['copied', '确认抄袭', 'copied'],
      ['independent', '独立完成', 'independent'],
      ['pending', '待复核', 'pending'],
    ];
    const current = pairDecision(pair);
    if (current?.note) note.value = current.note;
    const refreshDecisionUI = () => {
      const decision = pairDecision(pair);
      Object.entries(buttons).forEach(([key, button]) => button.classList.toggle('active', decision?.decision === key));
      meta.textContent = decision
        ? `已标记「${DECISION_LABELS[decision.decision] || decision.decision}」 · ${fmtDate(decision.ts)}`
        : '尚未标记';
      const clear = decisionCard.querySelector('[data-clear-decision]');
      if (clear) clear.classList.toggle('hidden', !decision);
    };
    decisions.forEach(([value, label, cls]) => {
      const button = el('button', `btn ghost small ${cls}`, label);
      button.type = 'button';
      button.addEventListener('click', () => savePairDecision(pair, value, note.value.trim()));
      buttons[value] = button;
      decisionWrap.appendChild(button);
    });
    decisionCard.appendChild(decisionWrap);
    decisionCard.appendChild(note);
    const decisionActions = el('div', 'review-actions');
    const saveButton = el('button', 'btn primary small', '保存备注');
    saveButton.type = 'button';
    saveButton.addEventListener('click', () => {
      const decision = pairDecision(pair);
      savePairDecision(pair, decision?.decision || 'pending', note.value.trim());
    });
    const clearButton = el('button', 'btn ghost small hidden', '清除判定');
    clearButton.type = 'button';
    clearButton.dataset.clearDecision = '1';
    clearButton.addEventListener('click', () => savePairDecision(pair, null, ''));
    decisionActions.appendChild(saveButton);
    decisionActions.appendChild(clearButton);
    decisionCard.appendChild(decisionActions);
    decisionCard.appendChild(meta);
    side.appendChild(decisionCard);
    refreshDecisionUI();

    const aiCard = el('div', 'side-card');
    aiCard.appendChild(el('h4', '', 'AI 复核（可选）'));
    aiCard.appendChild(el('p', 'hint', '把该代码对交给已配置的 AI 做语义复核；AI 建议不代替人工判定。'));
    const aiButton = el('button', 'btn ghost small', 'AI 复核该对');
    aiButton.type = 'button';
    const aiResult = el('div', 'ai-result');
    aiButton.addEventListener('click', () => runAiReview(pair, aiButton, aiResult));
    aiCard.appendChild(aiButton);
    aiCard.appendChild(aiResult);
    side.appendChild(aiCard);

    // drawer subtitle: include problem title if available
    const problemKey = pair.problem || '';
    $('#drawer-title').textContent = `代码比对 · ${problemKey || '未知题目'}`;
  }

  function savePairDecision(pair, decision, noteText) {
    try {
      const decisions = JSON.parse(localStorage.getItem('ojs.decisions') || '{}');
      const key = pairKey(pair);
      if (!decision) delete decisions[key];
      else decisions[key] = { decision, note: noteText || '', ts: Date.now() };
      localStorage.setItem('ojs.decisions', JSON.stringify(decisions));
    } catch (error) {
      toast(`保存人工判定失败：${errorMessage(error)}`, 'error');
      return;
    }
    renderPairs(state.pairsResponse);
    if (state.drawer.mode === 'pair' && state.drawer.pair) {
      const buttons = $$('.decision-buttons .btn');
      buttons.forEach((button) => {
        const label = button.textContent.trim();
        const map = { '确认抄袭': 'copied', '独立完成': 'independent', '待复核': 'pending' };
        button.classList.toggle('active', decision === map[label]);
      });
      const meta = $('.decision-meta');
      if (meta) {
        const current = pairDecision(pair);
        meta.textContent = current ? `已标记「${DECISION_LABELS[current.decision] || current.decision}」 · ${fmtDate(current.ts)}` : '尚未标记';
      }
      const clear = $('[data-clear-decision]');
      if (clear) clear.classList.toggle('hidden', !decision);
    }
    toast(decision ? `已标记为「${DECISION_LABELS[decision] || decision}」（仅本地）` : '已清除本地人工判定。', 'success', 3000);
  }

  async function runAiReview(pair, button, container) {
    const snapshot = currentSnapshot();
    if (!snapshot) return;
    button.disabled = true;
    button.classList.add('loading');
    button.textContent = 'AI 复核中…';
    container.innerHTML = '<p class="hint">正在等待 AI 返回（可能较慢）…</p>';
    try {
      const data = await api('/api/ai/review', {
        method: 'POST',
        body: { domain: snapshot.domain, hid: snapshot.hid, rid_a: pair.rid_a, rid_b: pair.rid_b },
      });
      renderAiResult(container, data);
    } catch (error) {
      container.innerHTML = '';
      const notice = el('div', 'notice danger');
      const text = error.data && typeof error.data === 'object' ? error.data : {};
      notice.innerHTML = `<div><strong>${esc(text.error || 'AI 复核失败')}</strong><p>${esc(text.detail || errorMessage(error))}</p>${text.hint ? `<p>${esc(text.hint)}</p>` : ''}</div>`;
      container.appendChild(notice);
    } finally {
      button.disabled = false;
      button.classList.remove('loading');
      button.textContent = '再次 AI 复核';
    }
  }

  function renderAiResult(container, data) {
    container.innerHTML = '';
    const verdict = data.verdict || data.data || {};
    const verdictValue = verdict.verdict || '';
    const confidence = verdict.confidence != null ? fmtPercent(verdict.confidence) : '—';
    const summary = verdict.summary || data.text || '';
    const evidence = toArray(verdict.evidence);
    if (verdictValue) {
      const head = el('div', 'verdict', `${verdictValue} · 置信度 ${confidence}`);
      head.className = `verdict ${String(verdictValue).toLowerCase().includes('plag') ? 'high' : ''}`;
      container.appendChild(head);
    }
    if (summary) container.appendChild(el('p', '', summary));
    if (evidence.length) {
      const list = el('ul');
      evidence.forEach((item) => list.appendChild(el('li', '', String(item))));
      container.appendChild(list);
    }
    const meta = [data.provider, data.model].filter(Boolean).join(' / ');
    if (meta) container.appendChild(el('p', 'muted small', `模型：${meta}`));
    if (data.error) container.appendChild(el('p', 'warn-text', `错误：${data.error}`));
    container.appendChild(el('p', 'hint', 'AI 结论仅作参考，请结合源码与证据人工判定。'));
    if (!verdictValue && !summary && !data.error) {
      container.appendChild(el('pre', '', JSON.stringify(data, null, 2)));
    }
  }

  /* ------------------------------------------------------- one submission */
  async function openSubmissionDrawer(rid, snapshot) {
    if (!rid) return;
    const target = snapshot || currentSnapshot();
    if (!target) {
      toast('请先选择快照。', 'warn');
      return;
    }
    openDrawerShell('提交详情', `RID ${rid}`);
    state.drawer = { mode: 'submission', pair: null, snapshot: target, lastFocus: state.drawer.lastFocus };
    drawerMessage('正在读取提交代码与详情…');
    try {
      const data = await api(`/api/submissions/${enc(rid)}?domain=${enc(target.domain)}&hid=${enc(target.hid)}`);
      renderSubmissionDrawer(rid, data);
    } catch (error) {
      drawerMessage(`读取提交失败：${errorMessage(error)}`, 'danger');
    }
  }

  function renderSubmissionDrawer(rid, data) {
    const body = $('#drawer-body');
    body.innerHTML = '';
    const summary = data.summary || {};
    const detail = data.detail || {};
    const problem = data.problem || {};
    const code = detail.code || '';
    const language = detail.language || summary.language || data.language || '';
    $('#drawer-title').textContent = `提交详情 · ${problem.key || summary.problem_key || rid}`;
    $('#drawer-subtitle').textContent =
      `${summary.display_name || summary.username || ''} · ${summary.status_text || summary.status || ''} · ` +
      `${data.language || language || ''} · ${fmtTs(summary.submit_ts)}`;

    const metrics = document.createElement('div');
    metrics.className = 'drawer-metrics';
    const items = [
      ['状态', summary.status_text || summary.status || '—'],
      ['得分', summary.score ?? '—'],
      ['语言', languageMeta(language).label],
      ['用时', summary.time_ms != null ? `${summary.time_ms} ms` : '—'],
      ['内存', summary.memory || '—'],
      ['提交时间', fmtTs(summary.submit_ts)],
    ];
    items.forEach(([key, value]) => {
      const card = el('div', 'metric-card');
      card.appendChild(el('div', 'k', key));
      card.appendChild(el('div', 'v', String(value)));
      metrics.appendChild(card);
    });
    body.appendChild(metrics);

    const layout = el('div', 'drawer-layout');
    const main = el('div', 'drawer-main');
    const side = el('div', 'drawer-side');
    layout.appendChild(main);
    layout.appendChild(side);
    body.appendChild(layout);

    const codeSection = el('section', 'drawer-section');
    const toolbar = el('div', 'code-toolbar');
    toolbar.appendChild(el('h3', '', '完整源码'));
    toolbar.appendChild(el('span', 'hint', '行号仅供定位；代码使用纯文本渲染。'));
    codeSection.appendChild(toolbar);
    const grid = el('div', 'code-grid single');
    grid.appendChild(codePane('提交', data, code, [], false));
    codeSection.appendChild(grid);
    main.appendChild(codeSection);

    if (toArray(detail.tests).length) {
      const section = el('section', 'drawer-section');
      section.appendChild(el('h3', '', '评测点'));
      const wrap = el('div', 'table-wrap');
      wrap.innerHTML = `<table class="case-table"><thead><tr><th>#</th><th>状态</th><th>得分</th><th>用时</th><th>内存</th><th>信息</th></tr></thead><tbody>${
        toArray(detail.tests).map((test, index) => `<tr><td>${index + 1}</td><td>${statusBadge(test.status)}</td><td>${esc(test.score ?? '')}</td><td>${esc(test.time_ms ?? '')} ms</td><td>${esc(test.memory ?? '')}</td><td class="muted small">${esc(test.message || '')}</td></tr>`).join('')
      }</tbody></table>`;
      section.appendChild(wrap);
      main.appendChild(section);
    }
    if (detail.compile_error || detail.judge_text) {
      const section = el('section', 'drawer-section');
      section.appendChild(el('h3', '', '编译 / 评测输出'));
      section.appendChild(el('pre', 'output', detail.compile_error || detail.judge_text || ''));
      main.appendChild(section);
    }

    const problemCard = el('div', 'side-card');
    problemCard.appendChild(el('h4', '', '题目'));
    problemCard.innerHTML += `<p><strong>${esc(problem.key || summary.problem_key || '')}</strong> ${esc(problem.title || '')}</p>
      <p class="muted small">${esc(problem.points ? `${problem.points} 分` : '')} ${esc(problem.time_limit || '')} ${esc(problem.memory_limit || '')}</p>`;
    side.appendChild(problemCard);

    const infoCard = el('div', 'side-card');
    infoCard.appendChild(el('h4', '', '提交信息'));
    const dl = el('dl', 'metric-list');
    [
      ['用户', summary.username || ''],
      ['显示名', summary.display_name || ''],
      ['UID', summary.uid ?? ''],
      ['题目', summary.problem_key || ''],
      ['状态', summary.status_text || summary.status || ''],
      ['得分', summary.score ?? ''],
      ['语言', language || ''],
    ].forEach(([key, value]) => {
      dl.appendChild(el('dt', '', key));
      dl.appendChild(el('dd', '', String(value ?? '')));
    });
    infoCard.appendChild(dl);
    const judgeButton = el('button', 'btn ghost small', '在评测页打开');
    judgeButton.type = 'button';
    judgeButton.addEventListener('click', () => {
      closeDrawer();
      switchTab('judge');
      const input = $('#judge-rid');
      if (input) input.value = rid;
    });
    infoCard.appendChild(judgeButton);
    side.appendChild(infoCard);

    const analysis = data.analysis || {};
    if (analysis.anomaly) {
      const anomalyCard = el('div', 'side-card');
      anomalyCard.appendChild(el('h4', '', '异常信号'));
      const list = el('ul', 'reason-list');
      toArray(analysis.anomaly.reasons).forEach((reason) => {
        if (typeof reason === 'string') list.appendChild(el('li', '', reason));
        else list.appendChild(el('li', '', `${REASON_LABELS[reason.code] || reason.code || '信号'}：${reason.detail || ''}`));
      });
      anomalyCard.appendChild(list);
      side.appendChild(anomalyCard);
    }
    const related = toArray(analysis.related_pairs);
    if (related.length) {
      const relatedCard = el('div', 'side-card');
      relatedCard.appendChild(el('h4', '', '相关代码对'));
      related.forEach((pair) => {
        const button = el('button', 'btn ghost small', `${pair.problem || ''} · ${pair.username_a || pair.rid_a} ↔ ${pair.username_b || pair.rid_b} · ${fmtScore(pair.score)}`);
        button.type = 'button';
        button.style.width = '100%';
        button.style.marginTop = '5px';
        button.addEventListener('click', () => openPairDrawer(pair));
        relatedCard.appendChild(button);
      });
      side.appendChild(relatedCard);
    }
  }


  /* ---------------------------------------------------------------- judge */
  async function runJudge(event) {
    event.preventDefault();
    const snapshot = currentSnapshot();
    if (!snapshot) {
      toast('请先选择快照。', 'warn');
      return;
    }
    const rid = $('#judge-rid').value.trim();
    if (!rid) {
      toast('请填写提交 RID。', 'warn');
      return;
    }
    const button = $('#btn-judge');
    setBusy(button, true, '评测中…');
    const payload = {
      domain: snapshot.domain,
      hid: snapshot.hid,
      rid,
      language: $('#judge-language').value,
      time_limit_ms: Number($('#judge-time').value) || 5000,
      memory_limit_mb: Number($('#judge-memory').value) || 256,
    };
    const container = $('#judge-result');
    container.innerHTML = '<p class="hint">正在本地评测（编译 + 运行测试点）…</p>';
    try {
      const data = await api('/api/judge/run', { method: 'POST', body: payload });
      state.judgeResult = data;
      renderJudgeResult(data);
      toast('本地评测完成。', 'success');
    } catch (error) {
      container.innerHTML = '';
      const notice = el('div', `notice ${error.status === 501 ? 'info' : 'danger'}`);
      const data = error.data && typeof error.data === 'object' ? error.data : {};
      notice.innerHTML = `<div><strong>${esc(data.error || '本地评测失败')}</strong><p>${esc(data.detail || errorMessage(error))}</p>${data.hint ? `<p>${esc(data.hint)}</p>` : ''}</div>`;
      container.appendChild(notice);
      if (error.status === 501) {
        $('#judge-availability').textContent = '评测模块不可用';
        $('#judge-availability').className = 'badge warn';
      }
      $('#judge-result-badge').textContent = '失败';
      $('#judge-result-badge').className = 'badge danger';
    } finally {
      setBusy(button, false);
    }
  }

  function renderJudgeResult(data) {
    const container = $('#judge-result');
    const badge = $('#judge-result-badge');
    if (!container) return;
    const result = data.result || {};
    const cases = toArray(result.cases);
    const language = data.language || data.language_raw || '';
    const status = result.status || '—';
    badge.textContent = status;
    badge.className = `badge ${statusClass(status)}`;
    $('#judge-availability').textContent = '评测模块可用';
    $('#judge-availability').className = 'badge ok';
    container.innerHTML = '';
    const header = el('div', 'result-header');
    header.appendChild(el('span', `badge ${statusClass(status)}`, status));
    header.appendChild(languageBadgeElement(language));
    header.appendChild(el('span', 'result-sub', `RID ${data.rid || ''} · ${data.problem || ''}`));
    if (data.matches_oj === true) header.appendChild(el('span', 'badge ok', '与 OJ 一致'));
    if (data.matches_oj === false) header.appendChild(el('span', 'badge warn', '与 OJ 不一致'));
    container.appendChild(header);

    const scoreLine = el('div', 'result-score', `${fmtScore(result.score, 1)}`);
    scoreLine.appendChild(el('small', '', ' 分'));
    container.appendChild(scoreLine);
    container.appendChild(el('p', 'result-sub',
      `本地用 ${fmtNumber(result.elapsed_ms)} ms · OJ 状态 ${data.oj?.status || '—'} · 语言 ${languageMeta(language).label}`));

    if (cases.length) {
      const section = el('div', 'result-block');
      section.appendChild(el('h4', '', '测试点'));
      const wrap = el('div', 'table-wrap');
      wrap.innerHTML = `<table class="case-table"><thead><tr><th>测试点</th><th>状态</th><th>用时</th><th>内存</th><th>信息</th></tr></thead><tbody>${
        cases.map((test) => `<tr><td>${esc(test.name || '')}</td><td>${statusBadge(test.status)}</td><td>${esc(test.time_ms ?? '')} ms</td><td>${esc(test.memory_kb ?? '')} KB</td><td class="muted small">${esc(test.message || '')}</td></tr>`).join('')
      }</tbody></table>`;
      section.appendChild(wrap);
      container.appendChild(section);
    }
    const outputs = [
      ['标准输出', result.stdout],
      ['标准错误', result.stderr],
      ['编译输出', result.compile_output],
    ].filter(([, value]) => value);
    if (outputs.length) {
      const section = el('div', 'result-block');
      section.appendChild(el('h4', '', '运行输出'));
      outputs.forEach(([label, value]) => {
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = label;
        details.appendChild(summary);
        details.appendChild(el('pre', 'output', value));
        section.appendChild(details);
      });
      container.appendChild(section);
    }
    const actions = el('div', 'review-actions');
    const sourceButton = el('button', 'btn ghost small', '查看提交源码');
    sourceButton.type = 'button';
    sourceButton.addEventListener('click', () => openSubmissionDrawer(data.rid, currentSnapshot()));
    actions.appendChild(sourceButton);
    container.appendChild(actions);
    container.appendChild(el('p', 'hint', '本地评测只读快照测试数据；OJ 侧状态不会被修改。'));
  }

  /* ------------------------------------------------------------ batch judge */
  function currentBatchJob() {
    const jobs = state.jobs.filter((job) => job.kind === 'judge_batch');
    if (!jobs.length) return null;
    if (state.batchJobId) {
      const explicit = jobs.find((job) => job.id === state.batchJobId);
      if (explicit) return explicit;
    }
    const snapshot = currentSnapshot();
    if (snapshot) {
      const matching = jobs.find((job) =>
        job.result && job.result.domain === snapshot.domain && job.result.hid === snapshot.hid);
      if (matching) return matching;
    }
    return jobs[0];
  }

  function setBatchButtonsDisabled(disabled) {
    for (const id of ['#btn-batch-problem', '#btn-batch-all', '#btn-batch-limit']) {
      const button = $(id);
      if (button) button.disabled = disabled;
    }
  }

  function renderBatchJob() {
    const wrap = $('#batch-progress-wrap');
    const resultBox = $('#batch-result');
    const badge = $('#batch-status-badge');
    if (!wrap || !resultBox || !badge) return;
    const job = currentBatchJob();
    if (!job) {
      wrap.classList.add('hidden');
      resultBox.classList.add('hidden');
      badge.textContent = '尚未运行';
      badge.className = 'badge muted';
      setBatchButtonsDisabled(false);
      return;
    }
    state.batchJob = job;
    wrap.classList.remove('hidden');
    const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
    const bar = $('#batch-progress-bar');
    if (bar) bar.style.width = `${progress}%`;
    $('#batch-progress-label').textContent = `${JOB_STATUS[job.status] || job.status} · ${progress}%`;
    $('#batch-job-meta').textContent = job.message || '';
    const log = $('#batch-log');
    if (log) {
      const lines = toArray(job.log);
      log.textContent = lines.length ? lines.join('\n') : '等待日志…';
      log.scrollTop = log.scrollHeight;
    }
    badge.textContent = JOB_STATUS[job.status] || job.status;
    badge.className = `badge ${job.status === 'succeeded' ? 'ok' : job.status === 'failed' ? 'danger' : ['queued', 'running'].includes(job.status) ? 'warn' : 'muted'}`;
    setBatchButtonsDisabled(['queued', 'running'].includes(job.status));
    if (job.status === 'succeeded' && job.result) {
      const snapshot = currentSnapshot();
      if (snapshot && job.result.domain && (job.result.domain !== snapshot.domain || job.result.hid !== snapshot.hid)) {
        resultBox.classList.add('hidden');
        return;
      }
      const marker = `${job.id}:${job.finished_at || ''}`;
      if (state.batchRendered !== marker) {
        state.batchRendered = marker;
        renderBatchResult(job.result);
      }
    } else if (job.status === 'failed') {
      state.batchRendered = '';
      resultBox.classList.remove('hidden');
      $('#batch-summary').innerHTML = '';
      $('#batch-rows').innerHTML = emptyRow(10, job.error || '批量评测失败');
      $('#batch-result-meta').textContent = '';
      $('#batch-json-link').classList.add('hidden');
      $('#batch-csv-link').classList.add('hidden');
      $('#batch-output-path').textContent = '';
    } else {
      resultBox.classList.add('hidden');
    }
  }

  function renderBatchResult(result) {
    state.batchResult = result;
    const box = $('#batch-result');
    if (!box || !result) return;
    box.classList.remove('hidden');
    const tiles = [
      ['评测提交', fmtNumber(result.evaluated)],
      ['本地通过', fmtNumber(result.local_accepted)],
      ['OJ 通过', fmtNumber(result.oj_accepted)],
      ['一致率', fmtPercent(result.agreement_rate)],
      ['不一致', fmtNumber(result.disagreement)],
      ['错误', fmtNumber(result.errors)],
    ];
    $('#batch-summary').innerHTML = tiles.map(([label, value]) => `
      <div class="stat-tile batch-summary-tile">
        <div class="num">${esc(value)}</div><div class="label">${esc(label)}</div>
      </div>`).join('');
    const scope = result.problem ? `题目 ${result.problem}` : '全部题目';
    const mode = result.all ? '全部提交' : `前 ${fmtNumber(result.limit)} 条`;
    const compared = result.compared ? ` · 与 OJ 可比较 ${fmtNumber(result.compared)} 条` : '';
    $('#batch-result-meta').textContent = `${scope} · ${mode} · workers=${fmtNumber(result.workers || 1)}${compared} · 生成于 ${fmtDate(result.generated_at)}`;
    const jsonLink = $('#batch-json-link');
    const csvLink = $('#batch-csv-link');
    if (jsonLink) {
      jsonLink.href = result.output_url || '#';
      jsonLink.classList.toggle('hidden', !result.output_url);
    }
    if (csvLink) {
      csvLink.href = result.csv_url || '#';
      csvLink.classList.toggle('hidden', !result.csv_url);
    }
    $('#batch-output-path').textContent = result.output_path ? `输出：${result.output_path}` : '';
    renderBatchRows();
  }

  function renderBatchRows() {
    const body = $('#batch-rows');
    if (!body) return;
    const result = state.batchResult;
    if (!result) {
      body.innerHTML = emptyRow(10, '暂无批量评测结果。');
      return;
    }
    const mismatchOnly = Boolean($('#batch-mismatch')?.checked);
    const allRows = toArray(result.rows);
    const rows = allRows.filter((row) => !mismatchOnly || !row.matched);
    const cap = 2000;
    const shown = rows.slice(0, cap);
    if (!shown.length) {
      body.innerHTML = emptyRow(10, mismatchOnly ? '没有不一致的记录。' : '暂无批量评测结果。');
      return;
    }
    body.innerHTML = shown.map((row) => {
      const local = row.local_status || '—';
      const oj = row.oj_status || '—';
      const matched = Boolean(row.matched);
      const lang = row.language || '';
      return `<tr class="${matched ? '' : 'batch-mismatch'}">
        <td><button type="button" class="badge tiny info" data-batch-rid="${esc(row.rid)}" title="查看提交详情">${esc(row.rid)}</button></td>
        <td>${esc(row.problem || '')}</td>
        <td><div class="person"><strong>${esc(row.username || `UID ${row.uid || ''}`)}</strong><span>uid ${esc(row.uid ?? '')}</span></div></td>
        <td>${languageBadge(lang)}</td>
        <td>${statusBadge(local)}</td>
        <td>${statusBadge(oj)}</td>
        <td>${matched ? '<span class="badge ok tiny">一致</span>' : '<span class="badge warn tiny">不一致</span>'}</td>
        <td>${esc(row.score ?? 0)}</td>
        <td>${fmtNumber(row.passed)} / ${fmtNumber(row.total)}</td>
        <td>${fmtNumber(row.elapsed_ms)} ms</td>
      </tr>`;
    }).join('');
    if (rows.length > cap) {
      body.insertAdjacentHTML('beforeend', emptyRow(10, `仅显示前 ${cap} 行（共 ${rows.length} 行）；完整结果请下载 CSV。`));
    }
    body.onclick = (event) => {
      const button = event.target.closest('button[data-batch-rid]');
      if (!button) return;
      openSubmissionDrawer(button.dataset.batchRid, currentSnapshot());
    };
  }

  async function doBatchJudge(mode) {
    const snapshot = currentSnapshot();
    if (!snapshot) {
      toast('请先选择本地快照。', 'warn');
      return;
    }
    const problem = mode === 'all' ? '' : ($('#batch-problem')?.value || '');
    if (mode === 'problem' && !problem) {
      toast('请先在“题目”下拉框里选择题目。', 'warn');
      return;
    }
    const body = {
      domain: snapshot.domain,
      hid: snapshot.hid,
      problem,
      all: mode !== 'limit',
      accepted_only: Boolean($('#batch-accepted-only')?.checked),
      language: $('#batch-language')?.value || '',
      workers: Number($('#batch-workers')?.value) || 1,
    };
    if (mode === 'limit') {
      body.limit = Math.max(1, Number($('#batch-limit')?.value) || 50);
    }
    setBatchButtonsDisabled(true);
    try {
      const job = await api('/api/judge/batch', { method: 'POST', body });
      state.batchJobId = job.id;
      state.batchRendered = '';
      upsertJob(job);
      renderBatchJob();
      toast('批量评测任务已开始，可继续浏览其他页面；进度会在评测页更新。', 'success');
      pollJobs();
      const badge = $('#batch-status-badge');
      if (badge) {
        badge.textContent = '排队中';
        badge.className = 'badge warn';
      }
    } catch (error) {
      setBatchButtonsDisabled(false);
      const data = error.data && typeof error.data === 'object' ? error.data : {};
      toast(`批量评测启动失败：${data.detail || data.error || errorMessage(error)}`, 'error', 7000);
    }
  }

  /* ---------------------------------------------------------------- project */
  const BINARY_EXTENSIONS = /\.(zip|7z|rar|png|jpe?g|gif|webp|bmp|pdf|jar|exe|dll|so|dylib|class|pyc|woff2?|ttf|mp3|mp4|mov|avi)$/i;

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result || '');
        const comma = result.indexOf(',');
        resolve(comma >= 0 ? result.slice(comma + 1) : result);
      };
      reader.onerror = () => reject(reader.error || new Error('读取文件失败'));
      reader.readAsDataURL(file);
    });
  }

  function renderProjectFileList() {
    const container = $('#project-file-list');
    if (!container) return;
    container.innerHTML = '';
    if (!state.projectFiles.length) {
      container.innerHTML = '<span class="muted small">尚未选择文件。</span>';
      return;
    }
    state.projectFiles.forEach((file, index) => {
      const chip = el('span', 'badge info');
      chip.innerHTML = `${esc(file.name)} · ${fmtBytes(file.size)} ${languageBadge('', file.name)}`;
      const remove = el('button', 'badge tiny danger', '移除');
      remove.type = 'button';
      remove.style.marginLeft = '4px';
      remove.addEventListener('click', () => {
        state.projectFiles.splice(index, 1);
        renderProjectFileList();
      });
      chip.appendChild(remove);
      container.appendChild(chip);
    });
  }

  async function collectProjectFiles() {
    const files = [];
    let total = 0;
    for (const file of state.projectFiles) {
      if (file.size > 8 * 1024 * 1024) throw new Error(`文件 ${file.name} 超过 8 MB`);
      total += file.size;
      if (total > 24 * 1024 * 1024) throw new Error('文件总大小超过 24 MB');
      const entry = { name: file.name, size: file.size, mime: file.type || '' };
      if (BINARY_EXTENSIONS.test(file.name) || !/\.(py|c|h|cpp|cc|cxx|hpp|md|txt|json|js|html|css|java|kt|cs|go|rs|sh|yaml|yml)$/i.test(file.name)) {
        entry.content_base64 = await fileToBase64(file);
      } else {
        entry.text = await file.text();
      }
      files.push(entry);
    }
    return files;
  }

  function projectReviewKey(title, student) {
    return `${title || ''}::${student || ''}`.toLowerCase();
  }

  function getProjectReview(key) {
    try {
      const reviews = JSON.parse(localStorage.getItem('ojs.projectReviews') || '{}');
      return reviews[key] || null;
    } catch (_) {
      return null;
    }
  }

  function saveProjectReview(key, note) {
    try {
      const reviews = JSON.parse(localStorage.getItem('ojs.projectReviews') || '{}');
      reviews[key] = { reviewed: true, note: note || '', ts: Date.now() };
      localStorage.setItem('ojs.projectReviews', JSON.stringify(reviews));
      return reviews[key];
    } catch (_) {
      return null;
    }
  }

  async function doProjectEvaluate(event) {
    event.preventDefault();
    const title = $('#project-title').value.trim();
    const student = $('#project-student').value.trim();
    const description = $('#project-description').value.trim();
    const rubric = $('#project-rubric').value.trim();
    if (!description && !rubric && !state.projectFiles.length) {
      toast('请至少填写作业说明、评分量规或上传作品文件。', 'warn');
      return;
    }
    const button = $('#btn-project-evaluate');
    setBusy(button, true, '准备文件并评测…');
    const container = $('#project-result');
    const badge = $('#project-result-badge');
    badge.textContent = '评测中…';
    badge.className = 'badge warn';
    container.innerHTML = '<p class="hint">正在整理文件并请求 AI 评测，请稍候…</p>';
    try {
      const files = await collectProjectFiles();
      const snapshot = currentSnapshot();
      const payload = {
        domain: snapshot?.domain || '',
        hid: snapshot?.hid || '',
        title,
        student,
        description,
        rubric,
        files,
      };
      const data = await api('/api/ai/evaluate-project', { method: 'POST', body: payload });
      state.projectResult = { data, title, student };
      renderProjectResult(state.projectResult);
      toast('AI 作品评测完成，请人工复核分数。', 'success');
    } catch (error) {
      container.innerHTML = '';
      const data = error.data && typeof error.data === 'object' ? error.data : {};
      const notice = el('div', `notice ${error.status === 501 ? 'info' : 'danger'}`);
      notice.innerHTML = `<div><strong>${esc(data.error || '作品评测失败')}</strong>
        <p>${esc(data.detail || errorMessage(error))}</p>
        ${data.hint ? `<p>${esc(data.hint)}</p>` : ''}
        ${data.received ? `<p class="muted small">已收到：${esc(data.received.files)} 个文件 · ${esc(data.received.description_chars)} 字说明 · ${esc(data.received.rubric_chars)} 字量规</p>` : ''}
      </div>`;
      container.appendChild(notice);
      badge.textContent = error.status === 501 ? '后端未就绪' : '失败';
      badge.className = error.status === 501 ? 'badge warn' : 'badge danger';
    } finally {
      setBusy(button, false);
    }
  }

  function renderProjectResult(result) {
    const container = $('#project-result');
    const badge = $('#project-result-badge');
    if (!container) return;
    if (!result) {
      container.innerHTML = '<p class="hint">填写作业说明与量规、选择作品文件后开始评测。</p>';
      return;
    }
    const data = result.data || {};
    const evaluation = data.evaluation || data.data || {};
    container.innerHTML = '';
    if (data.error) {
      const notice = el('div', 'notice danger');
      notice.innerHTML = `<div><strong>${esc(data.error)}</strong><p>${esc(data.detail || '')}</p></div>`;
      container.appendChild(notice);
      badge.textContent = '失败';
      badge.className = 'badge danger';
      return;
    }
    const score = evaluation.score ?? data.score;
    const confidence = evaluation.confidence ?? data.confidence;
    badge.textContent = 'AI 建议 · 待人工复核';
    badge.className = 'badge warn';
    const header = el('div', 'result-header');
    const scoreLine = el('div', 'result-score', score != null ? fmtScore(score, 1) : '—');
    scoreLine.appendChild(el('small', '', ' / 100'));
    header.appendChild(scoreLine);
    header.appendChild(el('span', 'badge warn', 'AI 建议分'));
    header.appendChild(el('span', 'badge info', `置信度 ${confidence != null ? fmtPercent(confidence) : '—'}`));
    if (evaluation.needs_human_review) header.appendChild(el('span', 'badge danger', '需人工复核'));
    container.appendChild(header);
    container.appendChild(el('p', 'result-sub',
      `${esc(result.title || '')}${result.student ? ` · ${esc(result.student)}` : ''} · 模型：${esc(data.provider || '—')} / ${esc(data.model || '—')}`));

    const items = toArray(evaluation.rubric_items || data.rubric_items);
    if (items.length) {
      const section = el('div', 'result-block');
      section.appendChild(el('h4', '', '量规逐项'));
      const wrap = el('div', 'table-wrap');
      wrap.innerHTML = `<table class="rubric-table"><thead><tr><th>评分项</th><th>得分</th><th>满分</th><th>评语</th></tr></thead><tbody>${
        items.map((item) => `<tr><td>${esc(item.criterion || '')}</td><td>${esc(item.points ?? '—')}</td><td>${esc(item.max_points ?? '—')}</td><td class="muted">${esc(item.comment || '')}</td></tr>`).join('')
      }</tbody></table>`;
      section.appendChild(wrap);
      container.appendChild(section);
    }
    const strengths = toArray(evaluation.strengths || data.strengths);
    const weaknesses = toArray(evaluation.weaknesses || data.weaknesses);
    if (strengths.length || weaknesses.length) {
      const grid = el('div', 'grid-2');
      if (strengths.length) {
        const card = el('div', 'result-block');
        card.appendChild(el('h4', '', '优点'));
        const list = el('ul', 'bullet-list');
        strengths.forEach((item) => list.appendChild(el('li', '', String(item))));
        card.appendChild(list);
        grid.appendChild(card);
      }
      if (weaknesses.length) {
        const card = el('div', 'result-block');
        card.appendChild(el('h4', '', '不足与风险'));
        const list = el('ul', 'bullet-list');
        weaknesses.forEach((item) => list.appendChild(el('li', '', String(item))));
        card.appendChild(list);
        grid.appendChild(card);
      }
      container.appendChild(grid);
    }
    const evidence = toArray(evaluation.evidence || data.evidence);
    if (evidence.length) {
      const section = el('div', 'result-block');
      section.appendChild(el('h4', '', '证据'));
      const list = el('ul', 'bullet-list');
      evidence.forEach((item) => list.appendChild(el('li', '', String(item))));
      section.appendChild(list);
      container.appendChild(section);
    }
    if (data.text) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = '模型原始输出';
      details.appendChild(summary);
      details.appendChild(el('pre', 'output', data.text));
      container.appendChild(details);
    }

    const reviewKey = projectReviewKey(result.title, result.student);
    const review = getProjectReview(reviewKey);
    const reviewPanel = el('div', 'review-panel');
    reviewPanel.innerHTML = `<div class="card-head compact"><h4>人工复核</h4><span id="project-review-status" class="badge ${review?.reviewed ? 'ok' : 'muted'}">${review?.reviewed ? `已复核 · ${fmtDate(review.ts)}` : '尚未复核'}</span></div>`;
    const button = el('button', 'btn primary small', '人工复核');
    button.type = 'button';
    const panel = el('div', 'hidden');
    const textarea = el('textarea');
    textarea.placeholder = '复核意见 / 调整后的分数说明（仅保存在本浏览器）';
    textarea.value = review?.note || '';
    const save = el('button', 'btn ghost small', '标记已人工复核');
    save.type = 'button';
    const actions = el('div', 'review-actions');
    actions.appendChild(save);
    panel.appendChild(textarea);
    panel.appendChild(actions);
    button.addEventListener('click', () => panel.classList.toggle('hidden'));
    save.addEventListener('click', () => {
      const saved = saveProjectReview(reviewKey, textarea.value.trim());
      const status = $('#project-review-status');
      if (saved && status) {
        status.textContent = `已复核 · ${fmtDate(saved.ts)}`;
        status.className = 'badge ok';
      }
      toast('已记录人工复核（仅本地浏览器）。', 'success');
    });
    reviewPanel.appendChild(button);
    reviewPanel.appendChild(panel);
    container.appendChild(reviewPanel);
    container.appendChild(el('p', 'hint', 'AI 评分是建议值，最终成绩以教师人工复核为准；本页面不会写回 OJ。'));
  }

  /* --------------------------------------------------------------- bootstrap */
  function bindEvents() {
    $('#snapshot-select')?.addEventListener('change', (event) => selectSnapshot(event.target.value));
    $('#btn-refresh')?.addEventListener('click', async () => {
      const button = $('#btn-refresh');
      setBusy(button, true, '刷新中…');
      try {
        await Promise.all([loadHealth(), loadSession()]);
        await loadSnapshots({ force: true });
        toast('已刷新本地快照。', 'success', 2200);
      } catch (error) {
        toast(`刷新失败：${errorMessage(error)}`, 'error');
      } finally {
        setBusy(button, false);
      }
    });
    $('#btn-analyze')?.addEventListener('click', doAnalyze);
    $('#btn-analyze-panel')?.addEventListener('click', doAnalyze);
    $('#btn-run-analysis')?.addEventListener('click', doAnalyze);

    $('#login-form')?.addEventListener('submit', doLogin);
    $('#btn-logout')?.addEventListener('click', doLogout);
    $('#btn-homeworks-refresh')?.addEventListener('click', refreshHomeworks);
    $('#acquire-form')?.addEventListener('submit', (event) => {
      event.preventDefault();
      doAcquire(false);
    });
    $('#btn-repair')?.addEventListener('click', () => doAcquire(true));

    $('#btn-pairs-refresh')?.addEventListener('click', () => loadPairs(true).catch((error) => toast(errorMessage(error), 'error')));
    $('#pairs-min-score')?.addEventListener('change', () => {
      $('#pairs-min-score').dataset.touched = '1';
      loadPairs(true).catch((error) => toast(errorMessage(error), 'error'));
    });
    $('#pairs-problem')?.addEventListener('change', () => loadPairs(true).catch((error) => toast(errorMessage(error), 'error')));
    $('#pairs-limit')?.addEventListener('change', () => loadPairs(true).catch((error) => toast(errorMessage(error), 'error')));
    $('#btn-anomalies-refresh')?.addEventListener('click', () => loadAnomalies(true).catch((error) => toast(errorMessage(error), 'error')));
    $('#anomalies-min-score')?.addEventListener('change', () => loadAnomalies(true).catch((error) => toast(errorMessage(error), 'error')));

    $('#project-files')?.addEventListener('change', (event) => {
      const selected = Array.from(event.target.files || []);
      state.projectFiles = selected;
      renderProjectFileList();
    });
    $('#project-form')?.addEventListener('submit', doProjectEvaluate);
    $('#project-form')?.addEventListener('reset', () => {
      window.setTimeout(() => {
        state.projectFiles = [];
        renderProjectFileList();
      }, 0);
    });

    $('#judge-form')?.addEventListener('submit', runJudge);
    $('#btn-batch-problem')?.addEventListener('click', () => doBatchJudge('problem'));
    $('#btn-batch-all')?.addEventListener('click', () => doBatchJudge('all'));
    $('#btn-batch-limit')?.addEventListener('click', () => doBatchJudge('limit'));
    $('#batch-mismatch')?.addEventListener('change', () => renderBatchRows());
    $('#btn-clear-log')?.addEventListener('click', () => {
      state.logHidden = true;
      renderJobLog();
    });

    $('#drawer-close')?.addEventListener('click', closeDrawer);
    $('#drawer-overlay')?.addEventListener('click', closeDrawer);

    $$('[data-goto-tab]').forEach((button) => {
      button.addEventListener('click', () => switchTab(button.dataset.gotoTab));
    });
    $$('[data-copy-target]').forEach((button) => {
      button.addEventListener('click', async () => {
        const target = document.getElementById(button.dataset.copyTarget);
        if (!target) return;
        try {
          await navigator.clipboard.writeText(target.textContent || '');
          toast('命令已复制。', 'success', 2400);
        } catch (_) {
          toast('浏览器拒绝剪贴板访问，请手动选择复制。', 'warn');
        }
      });
    });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) pollJobs();
    });
  }

  async function bootstrap() {
    setupTheme();
    setupTabs();
    bindEvents();
    const hash = window.location.hash.replace('#', '');
    if (['overview', 'acquire', 'pairs', 'anomalies', 'final', 'project', 'judge'].includes(hash)) {
      switchTab(hash, { load: false });
    }
    await Promise.allSettled([loadHealth(), loadSnapshots(), loadSession()]);
    await loadJobs();
    switchTab(state.tab, { load: false });
    await ensureTabData(state.tab, false).catch((error) => toast(errorMessage(error), 'error'));
    window.setInterval(pollJobs, 2500);
    renderProjectFileList();
  }

  document.addEventListener('DOMContentLoaded', () => {
    bootstrap().catch((error) => {
      console.error(error);
      toast(`页面初始化失败：${errorMessage(error)}`, 'error', 8000);
    });
  });
})();
