/* ============================================================
   Claude Session Hub — Client-Side Application
   ============================================================ */

const SessionHub = (() => {
  // --- State ---
  const state = {
    sessions: [],
    filteredSessions: [],
    projects: [],
    stats: { total: 0, active: 0, tokens: 0, projects: 0 },
    currentView: 'list',       // 'list' | 'detail'
    currentSessionId: null,
    currentPage: 0,
    hasMoreMessages: true,
    loadingMessages: false,
    filters: {
      status: 'all',
      projects: new Set(),
      length: 'all',
      sort: 'date_desc',
      search: '',
      hideSubagents: true,
    },
    searchResults: null,
    sseRetryDelay: 1000,
    projectColors: {},
    selectMode: false,
    selectedIds: new Set(),
    trash: [],
    contextMenuSessionId: null,
    capabilities: null,
    capabilityFilter: null,
    settings: {
      titleMode: 'first',     // 'first' | 'last'
      filterMode: 'composable', // 'composable' | 'single'
    },
  };

  // Load persisted settings
  const SETTINGS_KEY = 'session-hub-settings';
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY));
    if (saved) Object.assign(state.settings, saved);
  } catch {}

  function saveSettings() {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
  }

  // Palette for project pills
  const PROJECT_COLORS = [
    '#4fc3f7', '#ab47bc', '#66bb6a', '#ffa726', '#ef5350',
    '#26c6da', '#ec407a', '#8d6e63', '#78909c', '#d4e157',
    '#7e57c2', '#ffca28', '#5c6bc0', '#29b6f6', '#ff7043',
  ];

  // --- DOM refs ---
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const dom = {};
  function cacheDom() {
    dom.sidebar = $('#sidebar');
    dom.hamburger = $('#hamburger-btn');
    dom.searchInput = $('#search-input');
    dom.searchClear = $('#search-clear');
    dom.statusFilters = $('#status-filters');
    dom.projectFilters = $('#project-filters');
    dom.lengthFilters = $('#length-filters');
    dom.searchHistory = $('#search-history');
    dom.sortSelect = $('#sort-select');
    dom.sessionList = $('#session-list');
    dom.emptyState = $('#empty-state');
    dom.emptyText = $('#empty-text');
    dom.listView = $('#list-view');
    dom.detailView = $('#detail-view');
    dom.listLoading = $('#list-loading');
    dom.messagesLoading = $('#messages-loading');
    dom.conversation = $('#conversation');
    dom.detailTitle = $('#detail-title');
    dom.detailMeta = $('#detail-meta');
    dom.backBtn = $('#back-btn');
    dom.resumeBtn = $('#resume-btn');
    dom.copyResumeBtn = $('#copy-resume-btn');
    dom.reindexBtn = $('#reindex-btn');
    dom.statTotal = $('#stat-total');
    dom.statActive = $('#stat-active');
    dom.statTokens = $('#stat-tokens');
    dom.statProjects = $('#stat-projects');
    dom.toastContainer = $('#toast-container');
    dom.content = $('#content');
    dom.contextMenu = $('#context-menu');
    dom.confirmDialog = $('#confirm-dialog');
    dom.confirmTitle = $('#confirm-title');
    dom.confirmMessage = $('#confirm-message');
    dom.confirmCancel = $('#confirm-cancel');
    dom.confirmOk = $('#confirm-ok');
    dom.renameDialog = $('#rename-dialog');
    dom.renameInput = $('#rename-input');
    dom.renameCancel = $('#rename-cancel');
    dom.renameSave = $('#rename-save');
    dom.cleanupDialog = $('#cleanup-dialog');
    dom.cleanupOptions = $('#cleanup-options');
    dom.cleanupCancel = $('#cleanup-cancel');
    dom.cleanupConfirm = $('#cleanup-confirm');
    dom.trashDialog = $('#trash-dialog');
    dom.trashList = $('#trash-list');
    dom.trashClose = $('#trash-close');
    dom.trashEmpty = $('#trash-empty');
    dom.bulkBar = $('#bulk-bar');
    dom.bulkCount = $('#bulk-count');
    dom.bulkStarBtn = $('#bulk-star-btn');
    dom.bulkArchiveBtn = $('#bulk-archive-btn');
    dom.bulkDeleteBtn = $('#bulk-delete-btn');
    dom.bulkClearBtn = $('#bulk-clear-btn');
    dom.selectModeBtn = $('#select-mode-btn');
    dom.cleanupBtn = $('#cleanup-btn');
    dom.trashBtn = $('#trash-btn');
    dom.detailStarBtn = $('#detail-star-btn');
    dom.detailArchiveBtn = $('#detail-archive-btn');
    dom.detailDeleteBtn = $('#detail-delete-btn');
    dom.detailExportBtn = $('#detail-export-btn');
    dom.analyticsView = $('#analytics-view');
    dom.analyticsBtn = $('#analytics-btn');
    dom.analyticsPeriod = $('#analytics-period-select');
    dom.resumeStatus = $('#resume-status');
    dom.capabilitiesBar = $('#capabilities-bar');
    dom.capabilitiesSummary = $('#capabilities-summary-text');
    dom.capabilitiesDetails = $('#capabilities-details');
    dom.capabilitiesToggle = $('#capabilities-toggle');
    dom.capabilitiesFilterIndicator = $('#capabilities-filter-indicator');
    dom.capabilitiesFilterText = $('#capabilities-filter-text');
    dom.capabilitiesFilterClear = $('#capabilities-filter-clear');
  }

  // --- API Client ---
  const api = {
    async get(url) {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`GET ${url}: ${res.status}`);
      return res.json();
    },
    async post(url, body) {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) throw new Error(`POST ${url}: ${res.status}`);
      return res.json();
    },
    async patch(url, body) {
      const res = await fetch(url, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) throw new Error(`PATCH ${url}: ${res.status}`);
      return res.json();
    },
    async delete(url) {
      const res = await fetch(url, { method: 'DELETE' });
      if (!res.ok) throw new Error(`DELETE ${url}: ${res.status}`);
      return res.json();
    },
  };

  // --- Utility functions ---

  function formatRelativeTime(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now - date;
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);
    const diffWeek = Math.floor(diffDay / 7);

    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    if (diffWeek < 5) return `${diffWeek}w ago`;
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  function formatNumber(n) {
    if (n == null) return '--';
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
    return String(n);
  }

  function formatBytes(bytes) {
    if (bytes == null) return '';
    if (bytes >= 1_048_576) return (bytes / 1_048_576).toFixed(1) + ' MB';
    if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return bytes + ' B';
  }

  function escapeHtml(str) {
    const el = document.createElement('span');
    el.textContent = str;
    return el.innerHTML;
  }

  function debounce(fn, ms) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  function getProjectColor(name) {
    if (!name) return PROJECT_COLORS[0];
    if (!state.projectColors[name]) {
      const idx = Object.keys(state.projectColors).length % PROJECT_COLORS.length;
      state.projectColors[name] = PROJECT_COLORS[idx];
    }
    return state.projectColors[name];
  }

  function truncate(str, len = 100) {
    if (!str) return '';
    return str.length > len ? str.slice(0, len) + '...' : str;
  }

  // --- Minimal Markdown Renderer ---
  function renderMarkdown(text) {
    if (!text) return '';
    let html = escapeHtml(text);

    // Code blocks (``` ... ```)
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      return `<pre><code class="language-${lang}">${code.trim()}</code></pre>`;
    });

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

    // Bold & italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Unordered lists
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, (match) => `<ul>${match}</ul>`);

    // Paragraphs: convert double newlines to <p> tags, single newlines to <br>
    html = html.replace(/\n{2,}/g, '</p><p>');
    // Avoid <br> inside <pre>
    html = html.replace(/(?<!<\/code>)\n(?!<)/g, '<br>');

    // Wrap in paragraph if not starting with a block element
    if (!/^<(h[1-3]|pre|ul|ol|p)/.test(html)) {
      html = '<p>' + html + '</p>';
    }

    return html;
  }

  // --- Rendering ---

  function renderStatusBadge(status, session) {
    const s = status || 'stale';
    // Build explanatory tooltip
    let tooltip = '';
    if (session && session.status_reason) {
      tooltip = session.status_reason;
    } else {
      tooltip = {
        active: 'A Claude process is running for this session.',
        idle: 'No live process — session is resumable.',
        stale: 'Old or abandoned session.',
      }[s] || '';
    }
    // Show confidence suffix on active
    let label = s;
    if (s === 'active' && session && session.status_confidence === 'running') {
      label = 'running';
    }
    const esc = (t) => t.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    return `<span class="status-badge ${s}" title="${esc(tooltip)}"><span class="badge-dot"></span>${label}</span>`;
  }

  function renderSessionCard(session, snippet) {
    const color = getProjectColor(session.project);
    const card = document.createElement('div');
    card.className = 'session-card';
    if (session.starred) card.classList.add('starred');
    card.dataset.sessionId = session.id;

    const isSelected = state.selectedIds.has(session.id);
    const untitledFallback = `Session ${(session.id || '').substring(0, 8)} · ${formatRelativeTime(session.created_at)}`;
    const baseTitle = state.settings.titleMode === 'last'
      ? (session.last_message_preview || session.title || untitledFallback)
      : (session.title || untitledFallback);
    const displayTitle = session.label || session.custom_label || baseTitle;
    const subtitle = (session.label || session.custom_label) && session.title ? session.title : null;
    const starHtml = session.starred ? '<span class="star-icon">&#11088;</span>' : '';
    const subagentHtml = session.is_subagent ? '<span class="subagent-badge">subagent</span>' : '';
    // Build title tooltip: full first user message + meta summary
    const fullMessage = session.title || displayTitle;
    const ageStr = formatRelativeTime(session.updated_at || session.created_at);
    const tooltipLines = [];
    tooltipLines.push(fullMessage);
    tooltipLines.push('');
    tooltipLines.push(`${session.message_count ?? 0} messages · ${ageStr}`);
    if (session.model) tooltipLines.push(`Model: ${session.model}`);
    if (session.project) tooltipLines.push(`Project: ${session.project}`);
    if (session.cost_usd != null && session.cost_usd > 0.01) tooltipLines.push(`Cost: $${session.cost_usd.toFixed(2)}`);
    const titleTooltip = tooltipLines.join('\n').replace(/"/g, '&quot;');

    card.innerHTML = `
      ${state.selectMode ? `<div class="session-card-checkbox ${isSelected ? 'checked' : ''}" data-select-id="${session.id}"></div>` : ''}
      <div class="session-card-body">
        <div class="session-card-title-row">
          ${starHtml}
          ${subagentHtml}
          <div class="session-card-title" title="${escapeHtml(titleTooltip)}">${escapeHtml(truncate(displayTitle, 120))}</div>
        </div>
        ${subtitle ? `<div class="session-card-subtitle">${escapeHtml(truncate(subtitle, 100))}</div>` : ''}
        ${session.project ? `
          <span class="session-card-project">
            <span class="project-dot" style="background:${color}"></span>
            ${escapeHtml(session.project)}
          </span>
        ` : ''}
        <div class="session-card-meta">
          <span>${session.message_count ?? 0} messages</span>
          ${session.model ? `<span>${escapeHtml(session.model)}</span>` : ''}
          ${session.total_tokens != null ? `<span>${formatNumber(session.total_tokens)} tokens</span>` : ''}
          ${session.file_size != null ? `<span>${formatBytes(session.file_size)}</span>` : ''}
        </div>
        ${snippet ? `<div class="search-snippet">${snippet}</div>` : ''}
      </div>
      <div class="session-card-right">
        <span class="session-card-time">${formatRelativeTime(session.updated_at || session.created_at)}</span>
        ${renderStatusBadge(session.status, session)}
        <button class="btn btn-secondary btn-sm card-resume-btn" data-resume="${session.id}" title="Resume session" onclick="event.stopPropagation()">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M4 2l10 6-10 6V2z"/></svg>
          Resume
        </button>
      </div>
    `;

    card.addEventListener('click', (e) => {
      if (state.selectMode) {
        e.preventDefault();
        toggleSelection(session.id);
        return;
      }
      showSessionDetail(session.id);
    });

    card.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      showContextMenu(e, session);
    });

    const resumeBtn = card.querySelector('.card-resume-btn');
    resumeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      resumeSession(session.id);
    });

    return card;
  }

  function renderFilterBanner() {
    // Show a banner above the list when filters are active
    let existing = document.getElementById('filter-banner');
    if (existing) existing.remove();

    const parts = [];
    if (state.filters.status !== 'all') {
      parts.push(`Status: <strong>${state.filters.status}</strong>`);
    }
    if (state.filters.projects.size > 0) {
      const names = [...state.filters.projects].join(', ');
      parts.push(`Projects: <strong>${escapeHtml(names)}</strong>`);
    }
    if (state.filters.length !== 'all') {
      const lengthLabels = { tiny: 'Tiny', short: 'Short', medium: 'Medium', long: 'Long' };
      parts.push(`Length: <strong>${lengthLabels[state.filters.length] || state.filters.length}</strong>`);
    }
    if (state.searchResults) {
      parts.push(`Search: <strong>${escapeHtml(state.filters.search)}</strong>`);
    }

    if (parts.length === 0) return;

    const count = state.searchResults ? (state._searchTotal || state.searchResults.length) : state.filteredSessions.length;
    const banner = document.createElement('div');
    banner.id = 'filter-banner';
    banner.className = 'filter-banner';
    banner.innerHTML = `
      <span class="filter-banner-text">${parts.join(' &middot; ')} &mdash; ${count} results</span>
      <button class="filter-banner-clear" onclick="document.dispatchEvent(new CustomEvent('clear-all-filters'))">Clear all</button>
    `;
    dom.sessionList.parentNode.insertBefore(banner, dom.sessionList);
  }

  function renderSessionList() {
    const list = dom.sessionList;
    list.innerHTML = '';

    const sessions = state.searchResults || state.filteredSessions;

    renderFilterBanner();

    if (sessions.length === 0) {
      dom.emptyState.classList.remove('hidden');
      dom.emptyText.textContent = state.filters.search
        ? 'No sessions match your search'
        : 'No sessions found';
      return;
    }
    dom.emptyState.classList.add('hidden');

    // Group by project
    const grouped = new Map();
    for (const s of sessions) {
      const proj = s.project || 'Unknown Project';
      if (!grouped.has(proj)) grouped.set(proj, []);
      grouped.get(proj).push(s);
    }

    // Track collapsed state per project (persists across re-renders)
    if (!state._collapsedGroups) state._collapsedGroups = new Set();

    for (const [project, items] of grouped) {
      const color = getProjectColor(project);
      const isCollapsed = state._collapsedGroups.has(project);

      const header = document.createElement('div');
      header.className = 'session-group-header';
      const chevronChar = isCollapsed ? '&#9654;' : '&#9660;';
      header.innerHTML = `
        <span class="session-group-chevron ${isCollapsed ? '' : 'open'}">${chevronChar}</span>
        <span class="session-group-pill" style="background:${color}22;color:${color}">
          <span style="width:8px;height:8px;border-radius:2px;background:${color};display:inline-block"></span>
          ${escapeHtml(project)} (${items.length})
        </span>
        <span class="session-group-line"></span>
      `;
      header.addEventListener('click', () => {
        const nowCollapsed = !state._collapsedGroups.has(project);
        if (nowCollapsed) {
          state._collapsedGroups.add(project);
        } else {
          state._collapsedGroups.delete(project);
        }
        const chevron = header.querySelector('.session-group-chevron');
        const container = header.nextElementSibling;
        if (container && container.classList.contains('session-group-items')) {
          container.classList.toggle('collapsed');
          chevron.classList.toggle('open');
          // Swap arrow character: ▶ (right) when collapsed, ▼ (down) when expanded
          chevron.innerHTML = nowCollapsed ? '&#9654;' : '&#9660;';
        }
      });
      list.appendChild(header);

      // Wrap items in a container for collapsing
      const itemsContainer = document.createElement('div');
      itemsContainer.className = `session-group-items ${isCollapsed ? 'collapsed' : ''}`;
      for (const session of items) {
        const snippet = session._searchSnippet || null;
        itemsContainer.appendChild(renderSessionCard(session, snippet));
      }
      list.appendChild(itemsContainer);
    }
  }

  function renderProjectFilters() {
    const container = dom.projectFilters;
    container.innerHTML = '';

    for (const p of state.projects) {
      const color = getProjectColor(p.name);
      const label = document.createElement('label');
      label.className = 'project-filter';
      label.innerHTML = `
        <input type="checkbox" value="${escapeHtml(p.name)}" ${state.filters.projects.has(p.name) ? 'checked' : ''}>
        <span class="project-pill" style="background:${color}"></span>
        <span class="project-name">${escapeHtml(p.name)}</span>
        <span class="project-count">${p.count}</span>
      `;
      label.querySelector('input').addEventListener('change', (e) => {
        if (state.settings.filterMode === 'single') {
          resetOtherFilters('projects');
        }
        if (e.target.checked) {
          state.filters.projects.add(p.name);
        } else {
          state.filters.projects.delete(p.name);
        }
        applyFilters();
      });
      container.appendChild(label);
    }
  }

  function renderStats() {
    dom.statTotal.textContent = formatNumber(state.stats.total);
    dom.statActive.textContent = formatNumber(state.stats.active);
    dom.statTokens.textContent = formatNumber(state.stats.tokens);
    dom.statProjects.textContent = formatNumber(state.stats.projects);

    // Helper: classify message count into length bucket
    function getLengthBucket(mc) {
      if (mc >= 1 && mc <= 5) return 'tiny';
      if (mc >= 6 && mc <= 50) return 'short';
      if (mc >= 51 && mc <= 200) return 'medium';
      if (mc > 200) return 'long';
      return null;
    }

    // Helper: apply all filters EXCEPT the one named in `exclude`
    function getFilteredSessions(exclude) {
      let sessions = [...state.sessions];
      if (state.filters.hideSubagents) {
        sessions = sessions.filter(s => !s.is_subagent);
      }
      if (exclude !== 'status' && state.filters.status !== 'all') {
        if (state.filters.status === 'starred') {
          sessions = sessions.filter(s => s.starred);
        } else {
          sessions = sessions.filter(s => s.status === state.filters.status);
        }
      }
      if (exclude !== 'projects' && state.filters.projects.size > 0) {
        sessions = sessions.filter(s => state.filters.projects.has(s.project));
      }
      if (exclude !== 'length' && state.filters.length !== 'all') {
        sessions = sessions.filter(s => {
          const mc = s.message_count || 0;
          switch(state.filters.length) {
            case 'tiny': return mc >= 1 && mc <= 5;
            case 'short': return mc >= 6 && mc <= 50;
            case 'medium': return mc >= 51 && mc <= 200;
            case 'long': return mc > 200;
            default: return true;
          }
        });
      }
      return sessions;
    }

    // Status counts (exclude status filter so we see all statuses)
    const statusBase = getFilteredSessions('status');
    const statusCounts = { all: 0, active: 0, idle: 0, stale: 0, starred: 0 };
    for (const s of statusBase) {
      statusCounts.all++;
      const st = s.status || 'stale';
      if (statusCounts[st] !== undefined) statusCounts[st]++;
      if (s.starred) statusCounts.starred++;
    }
    const countAll = $('#count-all');
    const countActive = $('#count-active');
    const countIdle = $('#count-idle');
    const countStale = $('#count-stale');
    const countStarred = $('#count-starred');
    if (countAll) countAll.textContent = statusCounts.all;
    if (countActive) countActive.textContent = statusCounts.active;
    if (countIdle) countIdle.textContent = statusCounts.idle;
    if (countStale) countStale.textContent = statusCounts.stale;
    if (countStarred) countStarred.textContent = statusCounts.starred;
    const countSubagents = $('#count-subagents');
    if (countSubagents) countSubagents.textContent = state.sessions.filter(s => s.is_subagent).length;

    // Length counts (exclude length filter so we see all buckets)
    const lengthBase = getFilteredSessions('length');
    const lengthCounts = { all: 0, tiny: 0, short: 0, medium: 0, long: 0 };
    for (const s of lengthBase) {
      lengthCounts.all++;
      const bucket = getLengthBucket(s.message_count || 0);
      if (bucket && lengthCounts[bucket] !== undefined) lengthCounts[bucket]++;
    }
    const clAll = $('#count-length-all');
    const clTiny = $('#count-length-tiny');
    const clShort = $('#count-length-short');
    const clMedium = $('#count-length-medium');
    const clLong = $('#count-length-long');
    if (clAll) clAll.textContent = lengthCounts.all;
    if (clTiny) clTiny.textContent = lengthCounts.tiny;
    if (clShort) clShort.textContent = lengthCounts.short;
    if (clMedium) clMedium.textContent = lengthCounts.medium;
    if (clLong) clLong.textContent = lengthCounts.long;

    // Project counts (exclude projects filter so we see all projects)
    const projectBase = getFilteredSessions('projects');
    const projCountMap = new Map();
    for (const s of projectBase) {
      const p = s.project || 'Unknown Project';
      projCountMap.set(p, (projCountMap.get(p) || 0) + 1);
    }
    dom.projectFilters.querySelectorAll('.project-filter').forEach(label => {
      const input = label.querySelector('input');
      const countEl = label.querySelector('.project-count');
      if (input && countEl) {
        countEl.textContent = projCountMap.get(input.value) || 0;
      }
    });
  }

  // --- Filtering & Sorting ---

  // In single filter mode, reset other filter groups when one changes
  function resetOtherFilters(except) {
    if (state.settings.filterMode !== 'single') return;
    if (except !== 'status') {
      state.filters.status = 'all';
      const r = dom.statusFilters && dom.statusFilters.querySelector('input[value="all"]');
      if (r) r.checked = true;
    }
    if (except !== 'length') {
      state.filters.length = 'all';
      const r = dom.lengthFilters && dom.lengthFilters.querySelector('input[value="all"]');
      if (r) r.checked = true;
    }
    if (except !== 'projects') {
      state.filters.projects.clear();
      if (dom.projectFilters) dom.projectFilters.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    }
  }

  function applyFilters() {
    // If we're in analytics view and a filter was activated, switch to list view
    if (state.currentView === 'analytics') {
      showListView();
    }

    let sessions = [...state.sessions];

    // Subagent filter
    if (state.filters.hideSubagents) {
      sessions = sessions.filter(s => !s.is_subagent);
    }

    // Status filter
    if (state.filters.status !== 'all') {
      if (state.filters.status === 'starred') {
        sessions = sessions.filter(s => s.starred);
      } else {
        sessions = sessions.filter(s => s.status === state.filters.status);
      }
    }

    // Project filter
    if (state.filters.projects.size > 0) {
      sessions = sessions.filter(s => state.filters.projects.has(s.project));
    }

    // Length filter
    if (state.filters.length !== 'all') {
      sessions = sessions.filter(s => {
        const mc = s.message_count || 0;
        switch(state.filters.length) {
          case 'tiny': return mc >= 1 && mc <= 5;
          case 'short': return mc >= 6 && mc <= 50;
          case 'medium': return mc >= 51 && mc <= 200;
          case 'long': return mc > 200;
          default: return true;
        }
      });
    }

    // Sort — starred sessions always first
    const [field, dir] = state.filters.sort.split('_');
    const mult = dir === 'desc' ? -1 : 1;
    sessions.sort((a, b) => {
      // Starred sessions come first regardless of sort
      const starA = a.starred ? 1 : 0;
      const starB = b.starred ? 1 : 0;
      if (starA !== starB) return starB - starA;

      let va, vb;
      switch (field) {
        case 'date':
          va = new Date(a.updated_at || a.created_at || 0).getTime();
          vb = new Date(b.updated_at || b.created_at || 0).getTime();
          break;
        case 'messages':
          va = a.message_count || 0;
          vb = b.message_count || 0;
          break;
        case 'tokens':
          va = a.total_tokens || 0;
          vb = b.total_tokens || 0;
          break;
        case 'size':
          va = a.file_size || 0;
          vb = b.file_size || 0;
          break;
        default:
          va = 0; vb = 0;
      }
      return (va - vb) * mult;
    });

    state.filteredSessions = sessions;
    state.searchResults = null;
    renderStats();
    renderSessionList();
  }

  // --- Search ---

  // --- Search History ---

  const SEARCH_HISTORY_KEY = 'session-hub-search-history';
  const SEARCH_HISTORY_MAX = 10;

  function getSearchHistory() {
    try {
      return JSON.parse(localStorage.getItem(SEARCH_HISTORY_KEY)) || [];
    } catch { return []; }
  }

  function saveSearchHistory(query) {
    const trimmed = query.trim();
    if (!trimmed) return;
    let history = getSearchHistory();
    // Dedup: remove existing entry
    history = history.filter(h => h !== trimmed);
    // Most recent first
    history.unshift(trimmed);
    // Max 10
    if (history.length > SEARCH_HISTORY_MAX) history = history.slice(0, SEARCH_HISTORY_MAX);
    localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(history));
  }

  function clearSearchHistory() {
    localStorage.removeItem(SEARCH_HISTORY_KEY);
    hideSearchHistory();
  }

  function showSearchHistory() {
    const history = getSearchHistory();
    if (history.length === 0) {
      hideSearchHistory();
      return;
    }
    dom.searchHistory.innerHTML = '';
    for (const item of history) {
      const el = document.createElement('div');
      el.className = 'search-history-item';
      el.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" opacity="0.4"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 12.5A5.5 5.5 0 1 1 8 2.5a5.5 5.5 0 0 1 0 11zM8.5 4H7v5l4.25 2.55.75-1.23L8.5 8.25V4z"/></svg>${escapeHtml(item)}`;
      el.addEventListener('mousedown', (e) => {
        e.preventDefault(); // prevent blur from firing before click
        dom.searchInput.value = item;
        state.filters.search = item;
        hideSearchHistory();
        performSearch(item);
      });
      dom.searchHistory.appendChild(el);
    }
    const clearEl = document.createElement('div');
    clearEl.className = 'search-history-clear';
    clearEl.textContent = 'Clear history';
    clearEl.addEventListener('mousedown', (e) => {
      e.preventDefault();
      clearSearchHistory();
    });
    dom.searchHistory.appendChild(clearEl);
    dom.searchHistory.classList.remove('hidden');
  }

  function hideSearchHistory() {
    if (dom.searchHistory) dom.searchHistory.classList.add('hidden');
  }

  async function performSearch(query) {
    if (!query.trim()) {
      state.searchResults = null;
      dom.searchClear.classList.add('hidden');
      renderSessionList();
      return;
    }
    dom.searchClear.classList.remove('hidden');
    try {
      const data = await api.get(`/api/search?q=${encodeURIComponent(query)}`);
      // data.results is an array of { session, snippet }
      const results = (data.results || []).map(r => {
        const s = r.session || r;
        // Clean up long paths in snippets
        let snippet = r.snippet || null;
        if (snippet) {
          // Replace long /Users/.../path sequences (may contain spaces, parens, @)
          snippet = snippet.replace(/\/Users\/[^\n<]*?(?=\s{2}|<|\n|$)/g, match => {
            // Only shorten if it looks like a real path (has multiple segments)
            const parts = match.replace(/\s+$/, '').split('/').filter(Boolean);
            if (parts.length > 4) {
              return '.../' + parts.slice(-2).join('/');
            }
            return match;
          });
        }
        s._searchSnippet = snippet;
        return s;
      });
      state.searchResults = results;
      // Show "100+" if capped
      if (data.total >= 100) {
        state._searchTotal = '100+';
      } else {
        state._searchTotal = String(data.total);
      }
      renderSessionList();
    } catch (err) {
      console.error('Search failed:', err);
      toast('Search failed', 'error');
    }
  }

  const debouncedSearch = debounce(performSearch, 300);

  // --- Session Detail ---

  async function showSessionDetail(sessionId) {
    state.currentView = 'detail';
    state.currentSessionId = sessionId;
    state.currentPage = 0;
    state.hasMoreMessages = true;

    dom.listView.classList.add('hidden');
    dom.analyticsView.classList.add('hidden');
    dom.analyticsBtn.classList.remove('active');
    dom.detailView.classList.remove('hidden');
    dom.conversation.innerHTML = '';

    // Find session info from state or fetch it
    let session = state.sessions.find(s => s.id === sessionId);
    if (!session) {
      try {
        session = await api.get(`/api/sessions/${sessionId}`);
      } catch {
        toast('Failed to load session', 'error');
        showListView();
        return;
      }
    }

    const detailFallback = `Session ${(sessionId || '').substring(0, 8)} · ${formatRelativeTime(session.created_at)}`;
    const displayTitle = session.custom_label || session.title || detailFallback;
    dom.detailTitle.textContent = truncate(displayTitle, 200);
    dom.detailTitle.title = displayTitle;
    const color = getProjectColor(session.project);
    const tokenDetail = session.cache_read_tokens
      ? ` (${formatNumber(session.cache_read_tokens)} cached)`
      : '';
    dom.detailMeta.innerHTML = `
      ${session.project ? `<span class="session-card-project"><span class="project-dot" style="background:${color}"></span>${escapeHtml(session.project)}</span>` : ''}
      ${renderStatusBadge(session.status, session)}
      <span>${formatRelativeTime(session.updated_at || session.created_at)}</span>
      <span>${session.message_count ?? 0} messages</span>
      ${session.model ? `<span>${escapeHtml(session.model)}</span>` : ''}
      ${session.total_tokens != null ? `<span title="Input + Output + Cache">${formatNumber(session.total_tokens)} tokens${tokenDetail}</span>` : ''}
      ${session.cost_usd != null && session.cost_usd > 0.01 ? `<span class="detail-cost" title="Cache-aware cost estimate">$${session.cost_usd.toFixed(2)}</span>` : ''}
    `;

    // Update star button
    updateDetailStarButton(session);

    // Set up resume button
    dom.resumeBtn.onclick = () => resumeSession(sessionId);
    dom.copyResumeBtn.onclick = () => copyResumeCommand(sessionId);
    dom.detailArchiveBtn.onclick = () => archiveSession(sessionId);
    dom.detailDeleteBtn.onclick = () => confirmDeleteSession(sessionId);
    dom.detailExportBtn.onclick = () => exportSession(sessionId);
    dom.detailStarBtn.onclick = () => toggleStar(sessionId);

    // Inline title editing
    dom.detailTitle.onclick = () => {
      if (dom.detailTitle.classList.contains('editing')) return;
      const fullTitle = session.label || session.custom_label || session.title || '';
      dom.detailTitle.textContent = fullTitle;
      dom.detailTitle.contentEditable = true;
      dom.detailTitle.classList.add('editing');
      dom.detailTitle.focus();

      // Select all text
      const range = document.createRange();
      range.selectNodeContents(dom.detailTitle);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);

      const save = async () => {
        dom.detailTitle.contentEditable = false;
        dom.detailTitle.classList.remove('editing');
        const newName = dom.detailTitle.textContent.trim();
        if (!newName || newName === fullTitle) {
          // Revert
          dom.detailTitle.textContent = truncate(fullTitle || 'Untitled session', 200);
          dom.detailTitle.title = fullTitle;
          return;
        }
        try {
          await api.patch(`/api/sessions/${sessionId}/label`, { label: newName });
          const idx = state.sessions.findIndex(s => s.id === sessionId);
          if (idx !== -1) {
            state.sessions[idx].label = newName;
            state.sessions[idx].title = newName;
          }
          session.label = newName;
          session.title = newName;
          dom.detailTitle.textContent = truncate(newName, 200);
          dom.detailTitle.title = newName;
          applyFilters();
          toast('Session renamed', 'success');
        } catch (err) {
          console.error('Rename failed:', err);
          dom.detailTitle.textContent = truncate(fullTitle, 200);
          toast('Failed to rename', 'error');
        }
      };

      dom.detailTitle.onblur = save;
      dom.detailTitle.onkeydown = (e) => {
        if (e.key === 'Enter') { e.preventDefault(); dom.detailTitle.blur(); }
        if (e.key === 'Escape') {
          dom.detailTitle.textContent = fullTitle;
          dom.detailTitle.contentEditable = false;
          dom.detailTitle.classList.remove('editing');
          dom.detailTitle.onblur = null;
        }
      };
    };

    // Load capabilities (passes session so we can build About block)
    loadCapabilities(sessionId, session);

    // Load first page of messages
    await loadMessages(sessionId, 0);
  }

  async function loadMessages(sessionId, page) {
    if (state.loadingMessages || !state.hasMoreMessages) return;
    state.loadingMessages = true;
    dom.messagesLoading.classList.remove('hidden');

    try {
      const data = await api.get(`/api/sessions/${sessionId}/messages?page=${page}&limit=50`);
      const messages = data.messages || [];

      if (messages.length < 50) {
        state.hasMoreMessages = false;
      }
      state.currentPage = page;

      for (const msg of messages) {
        dom.conversation.appendChild(renderMessage(msg));
      }
    } catch (err) {
      console.error('Failed to load messages:', err);
      if (page === 0) {
        dom.conversation.innerHTML = '<div class="empty-state"><p class="empty-text">Failed to load conversation</p></div>';
      }
    } finally {
      state.loadingMessages = false;
      dom.messagesLoading.classList.add('hidden');
    }
  }

  function renderMessage(msg) {
    const role = msg.role || 'system';
    const el = document.createElement('div');
    el.className = `message ${role}`;
    if (msg.uuid) el.dataset.uuid = msg.uuid;

    let content = '';

    // Role label
    content += `<div class="message-role">${escapeHtml(role)}</div>`;

    // Text content
    if (msg.text) {
      content += `<div class="message-content">${renderMarkdown(msg.text)}</div>`;
    }

    // Tool use blocks
    if (msg.tool_calls && msg.tool_calls.length > 0) {
      for (const tc of msg.tool_calls) {
        content += renderToolCall(tc);
      }
    }

    // Tool result
    if (msg.tool_result) {
      content += renderToolResult(msg.tool_result);
    }

    el.innerHTML = content;

    // Wire up collapsible tool calls and results
    el.querySelectorAll('.tool-call-header, .tool-result-header').forEach(header => {
      header.addEventListener('click', () => {
        const body = header.nextElementSibling;
        const chevron = header.querySelector('.chevron');
        body.classList.toggle('open');
        if (chevron) chevron.classList.toggle('open');
      });
    });

    return el;
  }

  function renderToolCall(tc) {
    const name = tc.name || tc.tool_name || 'unknown';
    const input = tc.input || tc.arguments || tc.params || {};
    const inputStr = typeof input === 'string' ? input : JSON.stringify(input, null, 2);
    return `
      <div class="tool-call">
        <div class="tool-call-header">
          <svg class="chevron" width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
            <path d="M4 2l5 4-5 4z"/>
          </svg>
          Show tool call: <span class="tool-call-name">${escapeHtml(name)}</span>
        </div>
        <div class="tool-call-body"><pre><code>${escapeHtml(inputStr)}</code></pre></div>
      </div>
    `;
  }

  function renderToolResult(result) {
    const text = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
    return `
      <div class="tool-result">
        <div class="tool-result-header">
          <svg class="chevron" width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
            <path d="M4 2l5 4-5 4z"/>
          </svg>
          Show tool result
        </div>
        <div class="tool-result-body"><pre><code>${escapeHtml(text)}</code></pre></div>
      </div>
    `;
  }

  function updateDetailStarButton(session) {
    if (dom.detailStarBtn) {
      dom.detailStarBtn.textContent = session.starred ? '\u2605' : '\u2606';
      dom.detailStarBtn.title = session.starred ? 'Unstar' : 'Star';
      dom.detailStarBtn.classList.toggle('starred', !!session.starred);
    }
  }

  // --- Context Menu ---

  function showContextMenu(e, session) {
    state.contextMenuSessionId = session.id;
    const menu = dom.contextMenu;
    const isStarred = session.starred;

    menu.innerHTML = '';
    const items = [
      { label: '\u25B6 Resume in Terminal', action: () => resumeSession(session.id) },
      { label: '\uD83D\uDCCB Copy Resume Command', action: () => copyResumeCommand(session.id) },
      { separator: true },
      { label: isStarred ? '\u2B50 Unstar' : '\u2B50 Star', action: () => toggleStar(session.id) },
      { label: '\u270F\uFE0F Rename...', action: () => showRenameDialog(session.id) },
      { label: '\uD83D\uDCE6 Archive', action: () => archiveSession(session.id) },
      { separator: true },
      { label: '\uD83D\uDCC4 Export as Markdown', action: () => exportSession(session.id) },
      { separator: true },
      { label: '\uD83D\uDDD1\uFE0F Delete', action: () => confirmDeleteSession(session.id), danger: true },
    ];

    for (const item of items) {
      if (item.separator) {
        const sep = document.createElement('div');
        sep.className = 'context-menu-separator';
        menu.appendChild(sep);
        continue;
      }
      const el = document.createElement('div');
      el.className = 'context-menu-item' + (item.danger ? ' danger' : '');
      el.textContent = item.label;
      el.addEventListener('click', () => {
        hideContextMenu();
        item.action();
      });
      menu.appendChild(el);
    }

    // Position menu at mouse, keeping it on screen
    menu.classList.remove('hidden');
    const rect = menu.getBoundingClientRect();
    let x = e.clientX;
    let y = e.clientY;
    if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 4;
    if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 4;
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
  }

  function hideContextMenu() {
    dom.contextMenu.classList.add('hidden');
    state.contextMenuSessionId = null;
  }

  // --- Confirm Dialog ---

  function showConfirmDialog(title, message, okLabel, onConfirm) {
    dom.confirmTitle.textContent = title;
    dom.confirmMessage.textContent = message;
    dom.confirmOk.textContent = okLabel || 'Delete';
    dom.confirmDialog.classList.remove('hidden');

    const cleanup = () => {
      dom.confirmDialog.classList.add('hidden');
      dom.confirmOk.onclick = null;
      dom.confirmCancel.onclick = null;
    };

    dom.confirmOk.onclick = () => { cleanup(); onConfirm(); };
    dom.confirmCancel.onclick = cleanup;
    dom.confirmDialog.querySelector('.modal-backdrop').onclick = cleanup;
  }

  // --- Rename Dialog ---

  function showRenameDialog(sessionId) {
    const session = state.sessions.find(s => s.id === sessionId);
    if (!session) return;

    dom.renameInput.value = session.custom_label || session.title || '';
    dom.renameDialog.classList.remove('hidden');
    dom.renameInput.focus();
    dom.renameInput.select();

    const cleanup = () => {
      dom.renameDialog.classList.add('hidden');
      dom.renameSave.onclick = null;
      dom.renameCancel.onclick = null;
    };

    dom.renameSave.onclick = async () => {
      const newName = dom.renameInput.value.trim();
      cleanup();
      if (!newName) return;
      try {
        await api.patch(`/api/sessions/${sessionId}/label`, { label: newName });
        const idx = state.sessions.findIndex(s => s.id === sessionId);
        if (idx !== -1) {
          state.sessions[idx].label = newName;
          state.sessions[idx].title = newName;
        }
        applyFilters();
        if (state.currentSessionId === sessionId) {
          dom.detailTitle.textContent = truncate(newName, 200);
          dom.detailTitle.title = newName;
        }
        toast('Session renamed', 'success');
      } catch (err) {
        console.error('Rename failed:', err);
        toast('Failed to rename session', 'error');
      }
    };
    dom.renameCancel.onclick = cleanup;
    dom.renameDialog.querySelector('.modal-backdrop').onclick = cleanup;

    dom.renameInput.onkeydown = (e) => {
      if (e.key === 'Enter') dom.renameSave.onclick();
      if (e.key === 'Escape') cleanup();
    };
  }

  // --- Session Actions ---

  async function toggleStar(sessionId) {
    const session = state.sessions.find(s => s.id === sessionId);
    if (!session) return;
    const newStarred = !session.starred;
    try {
      const endpoint = newStarred ? 'star' : 'unstar';
      await api.post(`/api/sessions/${sessionId}/${endpoint}`);
      session.starred = newStarred;
      applyFilters();
      if (state.currentSessionId === sessionId) {
        updateDetailStarButton(session);
      }
      toast(newStarred ? 'Session starred' : 'Session unstarred', 'success');
    } catch (err) {
      console.error('Star toggle failed:', err);
      toast('Failed to update star', 'error');
    }
  }

  async function archiveSession(sessionId) {
    try {
      await api.post(`/api/sessions/${sessionId}/archive`);
      const idx = state.sessions.findIndex(s => s.id === sessionId);
      if (idx !== -1) state.sessions.splice(idx, 1);
      applyFilters();
      renderStats();
      if (state.currentSessionId === sessionId) showListView();
      toast('Session archived', 'success');
    } catch (err) {
      console.error('Archive failed:', err);
      toast('Failed to archive session', 'error');
    }
  }

  function confirmDeleteSession(sessionId) {
    const session = state.sessions.find(s => s.id === sessionId);
    const title = session ? truncate(session.custom_label || session.title || 'Untitled session', 60) : 'this session';
    showConfirmDialog(
      'Delete Session',
      `Are you sure you want to delete "${title}"? It will be moved to trash.`,
      'Delete',
      () => deleteSession(sessionId)
    );
  }

  async function deleteSession(sessionId) {
    try {
      await api.delete(`/api/sessions/${sessionId}`);
      const idx = state.sessions.findIndex(s => s.id === sessionId);
      if (idx !== -1) {
        const removed = state.sessions.splice(idx, 1)[0];
        state.trash.push(removed);
      }
      applyFilters();
      renderStats();
      if (state.currentSessionId === sessionId) showListView();
      toast('Session moved to trash', 'success');
    } catch (err) {
      console.error('Delete failed:', err);
      toast('Failed to delete session', 'error');
    }
  }

  async function exportSession(sessionId) {
    try {
      const data = await api.get(`/api/sessions/${sessionId}/export`);
      const content = data.markdown || data.content || JSON.stringify(data, null, 2);
      const blob = new Blob([content], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `session-${sessionId.slice(0, 8)}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast('Session exported', 'success');
    } catch (err) {
      console.error('Export failed:', err);
      toast('Failed to export session', 'error');
    }
  }

  // --- Select Mode / Bulk Actions ---

  function toggleSelectMode() {
    state.selectMode = !state.selectMode;
    state.selectedIds.clear();
    dom.selectModeBtn.classList.toggle('active', state.selectMode);
    dom.selectModeBtn.textContent = state.selectMode ? 'Cancel Select' : 'Select';
    updateBulkBar();
    renderSessionList();
  }

  function toggleSelection(sessionId) {
    if (state.selectedIds.has(sessionId)) {
      state.selectedIds.delete(sessionId);
    } else {
      state.selectedIds.add(sessionId);
    }
    updateBulkBar();

    // Update the checkbox visually without full re-render
    const card = dom.sessionList.querySelector(`[data-session-id="${sessionId}"]`);
    if (card) {
      const cb = card.querySelector('.session-card-checkbox');
      if (cb) cb.classList.toggle('checked', state.selectedIds.has(sessionId));
    }
  }

  function updateBulkBar() {
    const count = state.selectedIds.size;
    if (state.selectMode && count > 0) {
      dom.bulkBar.classList.remove('hidden');
      dom.bulkCount.textContent = count;
    } else {
      dom.bulkBar.classList.add('hidden');
    }
  }

  async function bulkStar() {
    const ids = [...state.selectedIds];
    try {
      await api.post('/api/sessions/bulk/star', { session_ids: ids });
      for (const id of ids) {
        const session = state.sessions.find(s => s.id === id);
        if (session) session.starred = true;
      }
    } catch (err) {
      console.error('Bulk star failed:', err);
    }
    const done = ids.length;
    state.selectedIds.clear();
    updateBulkBar();
    applyFilters();
    toast(`Starred ${done} session${done !== 1 ? 's' : ''}`, 'success');
  }

  async function bulkArchive() {
    const ids = [...state.selectedIds];
    showConfirmDialog(
      'Archive Sessions',
      `Are you sure you want to archive ${ids.length} session${ids.length !== 1 ? 's' : ''}?`,
      'Archive',
      async () => {
        try {
          const res = await api.post('/api/sessions/bulk/archive', { session_ids: ids });
          const done = res.count || ids.length;
          // Remove archived from local state
          state.sessions = state.sessions.filter(s => !ids.includes(s.id));
          state.selectedIds.clear();
          updateBulkBar();
          applyFilters();
          renderStats();
          toast(`Archived ${done} session${done !== 1 ? 's' : ''}`, 'success');
        } catch (err) {
          console.error('Bulk archive failed:', err);
          toast('Failed to archive sessions', 'error');
        }
      }
    );
  }

  async function bulkDelete() {
    const ids = [...state.selectedIds];
    showConfirmDialog(
      'Delete Sessions',
      `Are you sure you want to delete ${ids.length} session${ids.length !== 1 ? 's' : ''}? They will be moved to trash.`,
      'Delete',
      async () => {
        try {
          const res = await api.post('/api/sessions/bulk/delete', { session_ids: ids });
          const done = res.count || ids.length;
          state.sessions = state.sessions.filter(s => !ids.includes(s.id));
          state.selectedIds.clear();
          updateBulkBar();
          applyFilters();
          renderStats();
          toast(`Deleted ${done} session${done !== 1 ? 's' : ''}`, 'success');
        } catch (err) {
          console.error('Bulk delete failed:', err);
          toast('Failed to delete sessions', 'error');
        }
      }
    );
  }

  // --- Cleanup ---

  async function showCleanupDialog() {
    const sessions = state.sessions;
    const empty = sessions.filter(s => (s.message_count || 0) === 0);
    const tiny = sessions.filter(s => (s.message_count || 0) > 0 && (s.message_count || 0) <= 5);
    const thirtyDaysAgo = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    const stale = sessions.filter(s => {
      const d = new Date(s.updated_at || s.created_at || 0);
      return d < thirtyDaysAgo && !s.starred;
    });

    dom.cleanupOptions.innerHTML = `
      <label class="cleanup-option">
        <input type="checkbox" value="empty" ${empty.length === 0 ? 'disabled' : ''}>
        <span>Empty sessions (0 messages)</span>
        <span class="cleanup-count">${empty.length} sessions</span>
      </label>
      <label class="cleanup-option">
        <input type="checkbox" value="tiny" ${tiny.length === 0 ? 'disabled' : ''}>
        <span>Tiny sessions (1-5 messages)</span>
        <span class="cleanup-count">${tiny.length} sessions</span>
      </label>
      <label class="cleanup-option">
        <input type="checkbox" value="stale" ${stale.length === 0 ? 'disabled' : ''}>
        <span>Stale sessions (&gt;30 days, not starred)</span>
        <span class="cleanup-count">${stale.length} sessions</span>
      </label>
    `;

    dom.cleanupDialog.classList.remove('hidden');

    const cleanup = () => {
      dom.cleanupDialog.classList.add('hidden');
      dom.cleanupConfirm.onclick = null;
      dom.cleanupCancel.onclick = null;
    };

    dom.cleanupCancel.onclick = cleanup;
    dom.cleanupDialog.querySelector('.modal-backdrop').onclick = cleanup;

    dom.cleanupConfirm.onclick = async () => {
      const checked = [...dom.cleanupOptions.querySelectorAll('input:checked')].map(i => i.value);
      cleanup();
      if (checked.length === 0) return;

      let toDelete = [];
      if (checked.includes('empty')) toDelete.push(...empty);
      if (checked.includes('tiny')) toDelete.push(...tiny);
      if (checked.includes('stale')) toDelete.push(...stale);

      // Deduplicate
      const idSet = new Set();
      toDelete = toDelete.filter(s => { if (idSet.has(s.id)) return false; idSet.add(s.id); return true; });

      showConfirmDialog(
        'Confirm Cleanup',
        `This will delete ${toDelete.length} session${toDelete.length !== 1 ? 's' : ''}. They will be moved to trash.`,
        'Clean Up',
        async () => {
          let done = 0;
          for (const s of toDelete) {
            try {
              await api.delete(`/api/sessions/${s.id}`);
              const idx = state.sessions.findIndex(ss => ss.id === s.id);
              if (idx !== -1) {
                state.trash.push(state.sessions.splice(idx, 1)[0]);
                done++;
              }
            } catch (err) {
              console.error('Cleanup delete failed for', s.id, err);
            }
          }
          applyFilters();
          renderStats();
          toast(`Cleaned up ${done} session${done !== 1 ? 's' : ''}`, 'success');
        }
      );
    };
  }

  // --- Trash ---

  async function showTrashDialog() {
    // Try to load trash from API, fall back to local state
    try {
      const data = await api.get('/api/trash');
      state.trash = data.items || data.sessions || data || state.trash;
    } catch {
      // Use local trash state
    }

    renderTrashList();
    dom.trashDialog.classList.remove('hidden');

    const closeTrash = () => {
      dom.trashDialog.classList.add('hidden');
    };

    dom.trashClose.onclick = closeTrash;
    dom.trashDialog.querySelector('.modal-backdrop').onclick = closeTrash;

    dom.trashEmpty.onclick = () => {
      if (state.trash.length === 0) return;
      showConfirmDialog(
        'Empty Trash',
        `Permanently delete ${state.trash.length} session${state.trash.length !== 1 ? 's' : ''}? This cannot be undone.`,
        'Empty Trash',
        async () => {
          try {
            await api.post('/api/trash/empty');
            state.trash = [];
            renderTrashList();
            toast('Trash emptied', 'success');
          } catch (err) {
            console.error('Empty trash failed:', err);
            toast('Failed to empty trash', 'error');
          }
        }
      );
    };
  }

  function renderTrashList() {
    if (!state.trash || state.trash.length === 0) {
      dom.trashList.innerHTML = '<div class="trash-empty-state">Trash is empty</div>';
      return;
    }

    dom.trashList.innerHTML = '';
    for (const entry of state.trash) {
      // Trash items from API have: filename, size, date_moved
      const filename = entry.filename || entry.name || 'unknown';
      const displayName = filename.replace(/\.jsonl$/, '').replace(/_/g, ' ');
      const size = formatBytes(entry.size || 0);
      const date = entry.date_moved ? formatRelativeTime(entry.date_moved) : '';

      const item = document.createElement('div');
      item.className = 'trash-item';
      item.innerHTML = `
        <div class="trash-item-info">
          <span class="trash-item-title">${escapeHtml(truncate(displayName, 50))}</span>
          <span class="trash-item-meta">${size} &middot; ${date}</span>
        </div>
        <button class="btn btn-secondary btn-sm">Restore</button>
      `;
      item.querySelector('button').addEventListener('click', async () => {
        try {
          await api.post(`/api/trash/${encodeURIComponent(filename)}/restore`);
          const idx = state.trash.findIndex(t => t.filename === filename);
          if (idx !== -1) state.trash.splice(idx, 1);
          renderTrashList();
          toast('Session restored', 'success');
          // Reindex to pick up the restored file
          setTimeout(() => api.post('/api/reindex'), 500);
        } catch (err) {
          console.error('Restore failed:', err);
          toast('Failed to restore session', 'error');
        }
      });
      dom.trashList.appendChild(item);
    }
  }

  function showListView() {
    state.currentView = 'list';
    state.currentSessionId = null;
    dom.detailView.classList.add('hidden');
    dom.analyticsView.classList.add('hidden');
    dom.analyticsBtn.classList.remove('active');
    dom.listView.classList.remove('hidden');
    clearCapabilityFilter();
    state.capabilities = null;
    if (dom.capabilitiesBar) dom.capabilitiesBar.classList.add('hidden');
    if (dom.capabilitiesDetails) {
      dom.capabilitiesDetails.classList.add('hidden');
      if (dom.capabilitiesToggle) dom.capabilitiesToggle.textContent = 'Show all ▼';
    }
  }

  // --- Capabilities Bar ---

  async function loadCapabilities(sessionId, session) {
    try {
      const caps = await api.get(`/api/sessions/${sessionId}/capabilities`);
      state.capabilities = caps;
      renderCapabilities(caps, session);
    } catch (err) {
      console.warn('Failed to load capabilities:', err);
      dom.capabilitiesBar.classList.add('hidden');
    }
  }

  function renderCapabilities(caps, session) {
    const counts = {
      tools: Object.keys(caps.tools || {}).length,
      skills: Object.keys(caps.skills || {}).length,
      agents: Object.keys(caps.agents || {}).length,
      mcp: Object.keys(caps.mcp_servers || {}).length,
      slash: Object.keys(caps.slash_commands || {}).length,
      plugins: (caps.plugins || []).length,
    };
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const hasAbout = session && (session.title || session.label);
    if (total === 0 && !hasAbout) {
      dom.capabilitiesBar.classList.add('hidden');
      return;
    }
    dom.capabilitiesBar.classList.remove('hidden');

    const summary = [];
    if (counts.tools) summary.push(`${counts.tools} tool${counts.tools !== 1 ? 's' : ''}`);
    if (counts.skills) summary.push(`${counts.skills} skill${counts.skills !== 1 ? 's' : ''}`);
    if (counts.slash) summary.push(`${counts.slash} command${counts.slash !== 1 ? 's' : ''}`);
    if (counts.agents) summary.push(`${counts.agents} agent${counts.agents !== 1 ? 's' : ''}`);
    if (counts.mcp) summary.push(`${counts.mcp} MCP server${counts.mcp !== 1 ? 's' : ''}`);
    if (counts.plugins) summary.push(`${counts.plugins} plugin${counts.plugins !== 1 ? 's' : ''}`);
    dom.capabilitiesSummary.textContent = summary.length ? 'Used: ' + summary.join(' · ') : 'Session overview';

    // Build About block first
    let html = '';
    if (session) {
      const firstMsg = session.first_user_message || session.title || session.label || '';
      const aboutLines = [];
      if (firstMsg) aboutLines.push(`<div class="about-first-message">${escapeHtml(firstMsg)}</div>`);
      const facts = [];
      if (session.message_count != null) facts.push(`<strong>${session.message_count}</strong> messages`);
      if (session.model) facts.push(escapeHtml(session.model));
      if (session.created_at && session.updated_at) {
        const start = new Date(session.created_at);
        const end = new Date(session.updated_at);
        const durMs = end - start;
        const durMin = Math.round(durMs / 60000);
        if (durMin > 0) {
          const durStr = durMin < 60 ? `${durMin}m` : `${Math.round(durMin / 60 * 10) / 10}h`;
          facts.push(`${durStr} duration`);
        }
      }
      if (session.cost_usd != null && session.cost_usd > 0.01) {
        facts.push(`<strong>$${session.cost_usd.toFixed(2)}</strong> cost`);
      }
      if (session.cache_read_tokens) {
        facts.push(`${formatNumber(session.cache_read_tokens)} cached tokens`);
      }
      if (facts.length) {
        aboutLines.push(`<div class="about-facts">${facts.join(' · ')}</div>`);
      }
      if (aboutLines.length) {
        html += `<div class="cap-group cap-about">
          <span class="cap-group-label">📝 About</span>
          <div class="cap-pills" style="flex-direction:column;align-items:flex-start">
            ${aboutLines.join('')}
          </div>
        </div>`;
      }
    }

    const groups = [
      { icon: '🛠', label: 'Tools', data: caps.tools || {}, type: 'tool', filterable: true },
      { icon: '⚡', label: 'Commands', data: caps.slash_commands || {}, type: 'slash', filterable: false },
      { icon: '🎯', label: 'Skills', data: caps.skills || {}, type: 'skill', filterable: true },
      { icon: '🤖', label: 'Agents', data: caps.agents || {}, type: 'agent', filterable: true },
      { icon: '🔌', label: 'MCP', data: caps.mcp_servers || {}, type: 'mcp', filterable: false },
    ];

    for (const g of groups) {
      const entries = Object.entries(g.data);
      if (entries.length === 0) continue;
      entries.sort((a, b) => b[1] - a[1]);
      const pills = entries.map(([name, count]) => {
        const filterable = g.filterable;
        return `<span class="cap-pill${filterable ? '' : ' non-filterable'}" data-cap-type="${g.type}" data-cap-name="${escapeHtml(name)}" title="${escapeHtml(name)}">
          ${escapeHtml(name)}
          ${count > 1 ? `<span class="cap-pill-count">×${count}</span>` : ''}
        </span>`;
      }).join('');
      html += `<div class="cap-group">
        <span class="cap-group-label">${g.icon} ${g.label}</span>
        <div class="cap-pills">${pills}</div>
      </div>`;
    }

    if ((caps.plugins || []).length > 0) {
      const pluginPills = caps.plugins.map(p =>
        `<span class="cap-pill non-filterable" title="${escapeHtml(p)}">${escapeHtml(p)}</span>`
      ).join('');
      html += `<div class="cap-group">
        <span class="cap-group-label">📦 Plugins</span>
        <div class="cap-pills">${pluginPills}</div>
      </div>`;
    }

    dom.capabilitiesDetails.innerHTML = html;

    dom.capabilitiesDetails.querySelectorAll('.cap-pill:not(.non-filterable)').forEach(pill => {
      pill.addEventListener('click', () => {
        const type = pill.dataset.capType;
        const name = pill.dataset.capName;
        applyCapabilityFilter(type, name, pill);
      });
    });
  }

  function applyCapabilityFilter(type, name, pillEl) {
    if (state.capabilityFilter && state.capabilityFilter.type === type && state.capabilityFilter.name === name) {
      clearCapabilityFilter();
      return;
    }

    state.capabilityFilter = { type, name };

    dom.capabilitiesDetails.querySelectorAll('.cap-pill.active').forEach(p => p.classList.remove('active'));
    pillEl.classList.add('active');

    const uuids = new Set();
    const caps = state.capabilities;
    if (type === 'tool' && caps.tool_uuids && caps.tool_uuids[name]) {
      caps.tool_uuids[name].forEach(u => uuids.add(u));
    }
    if (type === 'skill' && caps.tool_uuids && caps.tool_uuids['Skill']) {
      caps.tool_uuids['Skill'].forEach(u => uuids.add(u));
    }
    if (type === 'agent' && caps.tool_uuids && caps.tool_uuids['Agent']) {
      caps.tool_uuids['Agent'].forEach(u => uuids.add(u));
    }

    dom.conversation.classList.add('filtering');
    dom.conversation.querySelectorAll('.message').forEach(msg => {
      const uuid = msg.dataset.uuid;
      if (uuids.has(uuid)) {
        msg.classList.add('cap-match');
      } else {
        msg.classList.remove('cap-match');
      }
    });

    dom.capabilitiesFilterText.textContent = `Showing messages that used: ${name}`;
    dom.capabilitiesFilterIndicator.classList.remove('hidden');

    const firstMatch = dom.conversation.querySelector('.message.cap-match');
    if (firstMatch) firstMatch.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function clearCapabilityFilter() {
    state.capabilityFilter = null;
    if (dom.capabilitiesDetails) {
      dom.capabilitiesDetails.querySelectorAll('.cap-pill.active').forEach(p => p.classList.remove('active'));
    }
    if (dom.conversation) {
      dom.conversation.classList.remove('filtering');
      dom.conversation.querySelectorAll('.message.cap-match').forEach(m => m.classList.remove('cap-match'));
    }
    if (dom.capabilitiesFilterIndicator) {
      dom.capabilitiesFilterIndicator.classList.add('hidden');
    }
  }

  // --- Analytics ---

  function showAnalyticsView() {
    state.currentView = 'analytics';
    dom.listView.classList.add('hidden');
    dom.detailView.classList.add('hidden');
    dom.analyticsView.classList.remove('hidden');
    dom.analyticsBtn.classList.add('active');
    loadAnalytics();
  }

  async function loadAnalytics() {
    const days = dom.analyticsPeriod ? dom.analyticsPeriod.value : 30;
    try {
      const data = await api.get(`/api/analytics?days=${days}`);
      renderAnalyticsSummary(data.summary, data.cost_estimate);
      renderDailyActivityChart(data.daily_activity);
      renderModelDistribution(data.model_distribution);
      renderSessionLengthChart(data.session_length_distribution);
      renderProjectBreakdown(data.project_breakdown);
      renderHourlyActivity(data.hourly_activity);
      renderCostEstimate(data.cost_estimate);
    } catch (err) {
      console.error('Analytics load failed:', err);
      toast('Failed to load analytics', 'error');
    }
  }

  function renderAnalyticsSummary(s, cost) {
    const el = document.getElementById('analytics-summary');
    if (!el) return;
    const cards = [
      { value: formatNumber(s.total_sessions), label: 'Total Sessions' },
      { value: formatNumber(s.total_messages), label: 'Total Messages' },
      { value: formatNumber(s.total_tokens), label: 'Total Tokens' },
      { value: Math.round(s.avg_messages_per_session || 0), label: 'Avg Msgs/Session' },
      { value: s.active_sessions || 0, label: 'Active Now' },
      { value: `$${(cost && cost.total_usd ? cost.total_usd.toFixed(2) : '0.00')}`, label: 'Est. Cost' },
    ];
    el.innerHTML = cards.map(c => `
      <div class="summary-card">
        <div class="summary-card-value">${c.value}</div>
        <div class="summary-card-label">${c.label}</div>
      </div>
    `).join('');
  }

  function renderDailyActivityChart(data) {
    const container = document.getElementById('chart-daily-activity');
    if (!container || !data || data.length === 0) return;
    const W = 800, H = 200, P = { t: 10, r: 10, b: 30, l: 50 };
    const cW = W - P.l - P.r, cH = H - P.t - P.b;
    const maxM = Math.max(...data.map(d => d.messages), 1);
    const bW = Math.max(cW / data.length - 2, 3);

    let svg = `<svg viewBox="0 0 ${W} ${H}" class="chart-svg">`;
    for (let i = 0; i <= 4; i++) {
      const y = P.t + cH - (i / 4) * cH;
      svg += `<text x="${P.l - 5}" y="${y + 3}" text-anchor="end" class="chart-label">${formatNumber(Math.round(maxM * i / 4))}</text>`;
      svg += `<line x1="${P.l}" y1="${y}" x2="${W - P.r}" y2="${y}" class="chart-grid"/>`;
    }
    data.forEach((d, i) => {
      const x = P.l + (i * cW / data.length) + 1;
      const bH = (d.messages / maxM) * cH;
      const y = P.t + cH - bH;
      svg += `<rect x="${x}" y="${y}" width="${bW}" height="${bH}" class="chart-bar"><title>${d.date}: ${d.messages} msgs, ${d.sessions} sessions</title></rect>`;
      if (i % Math.ceil(data.length / 8) === 0) {
        svg += `<text x="${x + bW / 2}" y="${H - 5}" text-anchor="middle" class="chart-label">${d.date.slice(5)}</text>`;
      }
    });
    svg += '</svg>';
    container.innerHTML = svg;
  }

  function renderModelDistribution(models) {
    const container = document.getElementById('chart-model-distribution');
    if (!container || !models || models.length === 0) return;
    const colors = ['#4fc3f7', '#66bb6a', '#ab47bc', '#ffa726', '#ef5350', '#78909c'];

    // Use a table-style layout: donut on left, legend on right
    const S = 130, cx = S / 2, cy = S / 2, R = 52, IR = 30;
    let svg = `<svg viewBox="0 0 ${S} ${S}" width="${S}" height="${S}">`;
    let startA = 0;
    models.forEach((m, i) => {
      const a = (m.percentage / 100) * 360;
      if (a < 0.5) return;
      const endA = startA + a;
      const sR = (startA - 90) * Math.PI / 180, eR = (endA - 90) * Math.PI / 180;
      const la = a > 180 ? 1 : 0;
      svg += `<path d="M${cx + R * Math.cos(sR)},${cy + R * Math.sin(sR)} A${R},${R} 0 ${la},1 ${cx + R * Math.cos(eR)},${cy + R * Math.sin(eR)} L${cx + IR * Math.cos(eR)},${cy + IR * Math.sin(eR)} A${IR},${IR} 0 ${la},0 ${cx + IR * Math.cos(sR)},${cy + IR * Math.sin(sR)} Z" fill="${colors[i % colors.length]}"><title>${escapeHtml(m.model)}: ${formatNumber(m.tokens)} tokens (${m.percentage.toFixed(1)}%)</title></path>`;
      startA = endA;
    });
    svg += '</svg>';

    let legendRows = '';
    models.forEach((m, i) => {
      const name = m.model.replace(/^claude-/, '').replace(/-\d{8,}.*$/, '').replace(/-(\d+-\d+)$/, ' $1');
      const tokens = formatNumber(m.tokens);
      legendRows += `<tr>
        <td><span class="legend-dot" style="background:${colors[i % colors.length]};display:inline-block"></span></td>
        <td style="color:var(--text-secondary);font-size:13px;padding:4px 8px">${escapeHtml(name)}</td>
        <td style="color:var(--text-dim);font-size:12px;text-align:right;white-space:nowrap">${m.percentage.toFixed(1)}%</td>
        <td style="color:var(--text-dim);font-size:12px;text-align:right;padding-left:8px;white-space:nowrap">${tokens}</td>
      </tr>`;
    });

    container.innerHTML = `<div style="display:flex;align-items:center;gap:20px">
      <div style="flex-shrink:0">${svg}</div>
      <table style="border-collapse:collapse">${legendRows}</table>
    </div>`;
  }

  function renderSessionLengthChart(dist) {
    const container = document.getElementById('chart-session-length');
    if (!container || !dist) return;
    const items = [
      { label: dist.tiny.label, count: dist.tiny.count, color: '#78909c' },
      { label: dist.short.label, count: dist.short.count, color: '#42a5f5' },
      { label: dist.medium.label, count: dist.medium.count, color: '#66bb6a' },
      { label: dist.long.label, count: dist.long.count, color: '#ffa726' },
    ];
    const max = Math.max(...items.map(i => i.count), 1);
    let html = '<div class="hbar-chart">';
    items.forEach(item => {
      const pct = (item.count / max) * 100;
      html += `<div class="hbar-row">
        <span class="hbar-label">${item.label}</span>
        <div class="hbar-bar-container"><div class="hbar-bar" style="width:${pct}%;background:${item.color}"></div></div>
        <span class="hbar-value">${item.count}</span>
      </div>`;
    });
    html += '</div>';
    container.innerHTML = html;
  }

  function renderProjectBreakdown(projects) {
    const container = document.getElementById('chart-project-breakdown');
    if (!container || !projects || projects.length === 0) return;
    const max = Math.max(...projects.map(p => p.tokens), 1);
    const colors = ['#4fc3f7', '#ab47bc', '#66bb6a', '#ffa726', '#ef5350', '#26c6da', '#ec407a', '#8d6e63', '#78909c', '#d4e157'];
    let html = '<div class="hbar-chart">';
    projects.forEach((p, i) => {
      const pct = (p.tokens / max) * 100;
      html += `<div class="hbar-row">
        <span class="hbar-label" title="${escapeHtml(p.project_path || '')}">${escapeHtml(p.project)}</span>
        <div class="hbar-bar-container"><div class="hbar-bar" style="width:${pct}%;background:${colors[i % colors.length]}"></div></div>
        <span class="hbar-value">${formatNumber(p.tokens)}</span>
      </div>`;
    });
    html += '</div>';
    container.innerHTML = html;
  }

  function renderHourlyActivity(data) {
    const container = document.getElementById('chart-hourly-activity');
    if (!container || !data || data.length === 0) return;
    const W = 300, H = 160, P = { t: 10, r: 10, b: 25, l: 35 };
    const cW = W - P.l - P.r, cH = H - P.t - P.b;
    const maxS = Math.max(...data.map(d => d.sessions), 1);
    const bW = cW / 24 - 1;
    const peakHour = data.reduce((p, c) => c.sessions > p.sessions ? c : p, data[0]).hour;

    let svg = `<svg viewBox="0 0 ${W} ${H}" class="chart-svg">`;
    for (let i = 0; i <= 3; i++) {
      const y = P.t + cH - (i / 3) * cH;
      svg += `<text x="${P.l - 4}" y="${y + 3}" text-anchor="end" class="chart-label">${Math.round(maxS * i / 3)}</text>`;
      svg += `<line x1="${P.l}" y1="${y}" x2="${W - P.r}" y2="${y}" class="chart-grid"/>`;
    }
    data.forEach(d => {
      const x = P.l + (d.hour * (cW / 24)) + 0.5;
      const bH = (d.sessions / maxS) * cH;
      const y = P.t + cH - bH;
      const cls = d.hour === peakHour ? 'chart-bar-secondary' : 'chart-bar';
      svg += `<rect x="${x}" y="${y}" width="${bW}" height="${bH}" class="${cls}"><title>${d.hour}:00 — ${d.sessions} sessions</title></rect>`;
      if (d.hour % 4 === 0) {
        svg += `<text x="${x + bW / 2}" y="${H - 5}" text-anchor="middle" class="chart-label">${d.hour}h</text>`;
      }
    });
    svg += '</svg>';
    container.innerHTML = svg;
  }

  function renderCostEstimate(cost) {
    const container = document.getElementById('chart-cost-estimate');
    if (!container || !cost) return;
    let html = `<div class="cost-total">$${cost.total_usd.toFixed(2)}</div>`;
    if (cost.cache_savings_usd && cost.cache_savings_usd > 0.01) {
      html += `<div class="cost-savings">Saved <strong>$${cost.cache_savings_usd.toFixed(2)}</strong> via prompt caching</div>`;
    }
    html += `<div class="cost-note">${escapeHtml(cost.note || 'Estimated based on API pricing')}</div>`;
    html += '<div class="cost-breakdown">';
    (cost.by_model || []).forEach(m => {
      const name = m.model.replace(/^claude-/, '').replace(/-\d{8,}.*$/, '').replace(/-(\d+-\d+)$/, ' $1');
      html += `<div class="cost-row"><span class="cost-model">${escapeHtml(name)}</span><span class="cost-amount">$${m.cost_usd.toFixed(2)}</span></div>`;
    });
    html += '</div>';
    container.innerHTML = html;
  }

  // --- Actions ---

  async function resumeSession(sessionId) {
    // Show inline status on card resume button if present
    const cardResumeBtn = dom.sessionList.querySelector(`[data-resume="${sessionId}"]`);
    let cardStatusEl = null;
    if (cardResumeBtn) {
      cardStatusEl = document.createElement('span');
      cardStatusEl.className = 'card-resume-status';
      cardStatusEl.textContent = 'Opening...';
      cardResumeBtn.parentNode.appendChild(cardStatusEl);
    }

    try {
      await api.post(`/api/sessions/${sessionId}/resume`);
      const session = state.sessions.find(s => s.id === sessionId);
      const extra = (session && session.is_subagent) ? ' (resuming parent session)' : '';
      const msg = 'Opening in Terminal \u2014 large sessions may take 30-60s to load' + extra;

      // Show inline status in detail view
      if (dom.resumeStatus) {
        dom.resumeStatus.textContent = msg;
        dom.resumeStatus.className = 'resume-status success';
        dom.resumeStatus.classList.remove('hidden');
        setTimeout(() => dom.resumeStatus.classList.add('hidden'), 5000);
      }

      // Update card status
      if (cardStatusEl) {
        cardStatusEl.textContent = 'Opened!';
        setTimeout(() => cardStatusEl.remove(), 4000);
      }
    } catch (err) {
      console.error('Resume failed:', err);
      const errMsg = 'Failed to open terminal. Copy the command instead.';

      // Show inline status in detail view
      if (dom.resumeStatus) {
        dom.resumeStatus.textContent = errMsg;
        dom.resumeStatus.className = 'resume-status error';
        dom.resumeStatus.classList.remove('hidden');
        setTimeout(() => dom.resumeStatus.classList.add('hidden'), 5000);
      }

      // Update card status
      if (cardStatusEl) {
        cardStatusEl.textContent = 'Failed';
        cardStatusEl.style.color = 'var(--red)';
        setTimeout(() => cardStatusEl.remove(), 4000);
      }
    }
  }

  function copyResumeCommand(sessionId) {
    // For subagent sessions, copy the parent session ID
    const session = state.sessions.find(s => s.id === sessionId);
    const resumeId = (session && session.is_subagent && session.parent_session_id)
      ? session.parent_session_id
      : sessionId;
    const cmd = `claude -r ${resumeId}`;
    const suffix = (resumeId !== sessionId) ? ' (parent session)' : '';
    navigator.clipboard.writeText(cmd).then(() => {
      toast('Copied: ' + cmd + suffix, 'success');
    }).catch(() => {
      const input = document.createElement('input');
      input.value = cmd;
      document.body.appendChild(input);
      input.select();
      document.execCommand('copy');
      document.body.removeChild(input);
      toast('Copied: ' + cmd + suffix, 'success');
    });
  }

  async function reindex() {
    dom.reindexBtn.disabled = true;
    dom.reindexBtn.classList.add('spinning');
    try {
      await api.post('/api/reindex');
      toast('Reindex started', 'info');
      // Reload sessions after a brief pause
      setTimeout(() => loadSessions(), 1000);
    } catch (err) {
      console.error('Reindex failed:', err);
      toast('Reindex failed', 'error');
    } finally {
      dom.reindexBtn.disabled = false;
      dom.reindexBtn.classList.remove('spinning');
    }
  }

  // --- Toast ---

  function toast(message, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    dom.toastContainer.appendChild(el);

    setTimeout(() => {
      el.classList.add('fade-out');
      el.addEventListener('animationend', () => el.remove());
    }, 3000);
  }

  // --- SSE (Server-Sent Events) ---

  function connectSSE() {
    let retryDelay = 1000;

    function connect() {
      const es = new EventSource('/api/events');

      es.onopen = () => {
        retryDelay = 1000;
      };

      es.addEventListener('session_update', (e) => {
        try {
          const data = JSON.parse(e.data);
          updateSessionInPlace(data);
        } catch {}
      });

      es.addEventListener('new_session', (e) => {
        try {
          const data = JSON.parse(e.data);
          state.sessions.unshift(data);
          applyFilters();
          toast('New session detected', 'info');
        } catch {}
      });

      es.addEventListener('reindex_complete', () => {
        refreshSessionsSilently();
      });

      es.onerror = () => {
        es.close();
        setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 30000);
      };
    }

    connect();
  }

  function updateSessionInPlace(updated) {
    const idx = state.sessions.findIndex(s => s.id === updated.id);
    if (idx !== -1) {
      state.sessions[idx] = { ...state.sessions[idx], ...updated };
    }
    applyFilters();

    // If we're viewing this session's detail, update header
    if (state.currentSessionId === updated.id) {
      showSessionDetail(updated.id);
    }
  }

  // --- Data Loading ---

  async function loadSessions() {
    dom.listLoading.classList.remove('hidden');
    dom.emptyState.classList.add('hidden');
    try {
      const data = await api.get('/api/sessions');
      state.sessions = data.sessions || data || [];
      state.stats = data.stats || {
        total: state.sessions.length,
        active: state.sessions.filter(s => s.status === 'active').length,
        tokens: state.sessions.reduce((sum, s) => sum + (s.total_tokens || 0), 0),
        projects: new Set(state.sessions.map(s => s.project).filter(Boolean)).size,
      };

      // Extract projects
      const projMap = new Map();
      for (const s of state.sessions) {
        const p = s.project || 'Unknown Project';
        projMap.set(p, (projMap.get(p) || 0) + 1);
      }
      state.projects = [...projMap.entries()]
        .map(([name, count]) => ({ name, count }))
        .sort((a, b) => b.count - a.count);

      renderStats();
      renderProjectFilters();
      applyFilters();
    } catch (err) {
      console.error('Failed to load sessions:', err);
      dom.emptyState.classList.remove('hidden');
      dom.emptyText.textContent = 'Failed to load sessions';
    } finally {
      dom.listLoading.classList.add('hidden');
    }
  }

  // --- Silent Refresh (preserves search/filter state) ---

  async function refreshSessionsSilently() {
    try {
      const data = await api.get('/api/sessions');
      const newSessions = data.sessions || data || [];
      state.sessions = newSessions;
      state.stats = data.stats || state.stats;

      // Update stats display
      renderStats();

      // If user is NOT in a search, re-apply filters to show updated data
      // If user IS in a search, leave searchResults alone — don't wipe their results
      if (!state.searchResults) {
        applyFilters();
      }
    } catch (err) {
      // Silent — don't show error toasts for background refreshes
      console.warn('Silent refresh failed:', err);
    }
  }

  // --- Infinite Scroll ---

  function setupInfiniteScroll() {
    dom.content.addEventListener('scroll', () => {
      if (state.currentView !== 'detail') return;
      if (!state.hasMoreMessages || state.loadingMessages) return;

      const { scrollTop, scrollHeight, clientHeight } = dom.content;
      if (scrollHeight - scrollTop - clientHeight < 200) {
        loadMessages(state.currentSessionId, state.currentPage + 1);
      }
    });
  }

  // --- Sidebar Mobile Toggle ---

  let overlay = null;

  function setupSidebarToggle() {
    overlay = document.createElement('div');
    overlay.className = 'sidebar-overlay';
    document.body.appendChild(overlay);

    dom.hamburger.addEventListener('click', () => {
      const isOpen = dom.sidebar.classList.toggle('open');
      overlay.classList.toggle('active', isOpen);
    });

    overlay.addEventListener('click', () => {
      dom.sidebar.classList.remove('open');
      overlay.classList.remove('active');
    });
  }

  // --- Event Binding ---

  function bindEvents() {
    // Search
    dom.searchInput.addEventListener('input', (e) => {
      state.filters.search = e.target.value;
      if (e.target.value.trim()) {
        hideSearchHistory();
      }
      debouncedSearch(e.target.value);
    });
    dom.searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const query = dom.searchInput.value.trim();
        if (query) {
          saveSearchHistory(query);
          hideSearchHistory();
        }
      }
      if (e.key === 'Escape') {
        hideSearchHistory();
        dom.searchInput.value = '';
        state.filters.search = '';
        state.searchResults = null;
        dom.searchClear.classList.add('hidden');
        renderSessionList();
      }
    });
    dom.searchInput.addEventListener('focus', () => {
      if (!dom.searchInput.value.trim()) {
        showSearchHistory();
      }
    });
    dom.searchInput.addEventListener('blur', () => {
      // Small delay so click events on history items register
      setTimeout(() => hideSearchHistory(), 200);
    });
    dom.searchClear.addEventListener('click', () => {
      dom.searchInput.value = '';
      state.filters.search = '';
      state.searchResults = null;
      dom.searchClear.classList.add('hidden');
      renderSessionList();
    });

    // Status filters
    dom.statusFilters.addEventListener('change', (e) => {
      if (e.target.name === 'status') {
        resetOtherFilters('status');
        state.filters.status = e.target.value;
        applyFilters();
      }
    });

    // Subagent toggle
    const hideSubagentsCheckbox = $('#hide-subagents');
    if (hideSubagentsCheckbox) {
      hideSubagentsCheckbox.addEventListener('change', (e) => {
        state.filters.hideSubagents = e.target.checked;
        applyFilters();
      });
    }

    // Length filters
    dom.lengthFilters.addEventListener('change', (e) => {
      if (e.target.name === 'length') {
        resetOtherFilters('length');
        state.filters.length = e.target.value;
        applyFilters();
      }
    });

    // Sort
    dom.sortSelect.addEventListener('change', (e) => {
      state.filters.sort = e.target.value;
      applyFilters();
    });

    // Back button
    dom.backBtn.addEventListener('click', showListView);

    // Capabilities bar toggle & filter clear
    if (dom.capabilitiesToggle) {
      dom.capabilitiesToggle.addEventListener('click', () => {
        const expanded = !dom.capabilitiesDetails.classList.contains('hidden');
        if (expanded) {
          dom.capabilitiesDetails.classList.add('hidden');
          dom.capabilitiesToggle.textContent = 'Show all ▼';
        } else {
          dom.capabilitiesDetails.classList.remove('hidden');
          dom.capabilitiesToggle.textContent = 'Hide ▲';
        }
      });
    }
    if (dom.capabilitiesFilterClear) {
      dom.capabilitiesFilterClear.addEventListener('click', clearCapabilityFilter);
    }

    // Reindex
    dom.reindexBtn.addEventListener('click', reindex);

    // Analytics
    dom.analyticsBtn.addEventListener('click', () => {
      if (state.currentView === 'analytics') showListView();
      else showAnalyticsView();
    });
    if (dom.analyticsPeriod) {
      dom.analyticsPeriod.addEventListener('change', () => {
        if (state.currentView === 'analytics') loadAnalytics();
      });
    }

    // Clear all filters
    document.addEventListener('clear-all-filters', () => {
      state.filters.status = 'all';
      state.filters.projects.clear();
      state.filters.length = 'all';
      state.filters.hideSubagents = true;
      state.filters.search = '';
      state.searchResults = null;
      dom.searchInput.value = '';
      dom.searchClear.classList.add('hidden');
      // Reset status radio buttons
      const allRadio = dom.statusFilters.querySelector('input[value="all"]');
      if (allRadio) allRadio.checked = true;
      // Reset length radio buttons
      const allLengthRadio = dom.lengthFilters.querySelector('input[value="all"]');
      if (allLengthRadio) allLengthRadio.checked = true;
      // Uncheck project checkboxes
      dom.projectFilters.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
      applyFilters();
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      // Escape: close context menu, modals, select mode, or go back
      if (e.key === 'Escape') {
        if (!dom.contextMenu.classList.contains('hidden')) {
          hideContextMenu();
          return;
        }
        if (!dom.confirmDialog.classList.contains('hidden')) {
          dom.confirmDialog.classList.add('hidden');
          return;
        }
        if (!dom.renameDialog.classList.contains('hidden')) {
          dom.renameDialog.classList.add('hidden');
          return;
        }
        if (!dom.cleanupDialog.classList.contains('hidden')) {
          dom.cleanupDialog.classList.add('hidden');
          return;
        }
        if (!dom.trashDialog.classList.contains('hidden')) {
          dom.trashDialog.classList.add('hidden');
          return;
        }
        if (state.selectMode) {
          toggleSelectMode();
          return;
        }
        if (state.currentView === 'detail') {
          showListView();
        }
      }
      // Ctrl/Cmd+K to focus search
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        dom.searchInput.focus();
      }
    });

    // Context menu: close on click outside
    document.addEventListener('click', (e) => {
      if (!dom.contextMenu.classList.contains('hidden') && !dom.contextMenu.contains(e.target)) {
        hideContextMenu();
      }
    });

    // Context menu: close on scroll
    dom.content.addEventListener('scroll', () => {
      if (!dom.contextMenu.classList.contains('hidden')) {
        hideContextMenu();
      }
    });

    // Select mode toggle
    dom.selectModeBtn.addEventListener('click', toggleSelectMode);

    // Bulk actions
    dom.bulkStarBtn.addEventListener('click', bulkStar);
    dom.bulkArchiveBtn.addEventListener('click', bulkArchive);
    dom.bulkDeleteBtn.addEventListener('click', bulkDelete);
    dom.bulkClearBtn.addEventListener('click', () => {
      state.selectedIds.clear();
      updateBulkBar();
      renderSessionList();
    });

    // Cleanup and Trash buttons
    dom.cleanupBtn.addEventListener('click', showCleanupDialog);
    dom.trashBtn.addEventListener('click', showTrashDialog);

    // Settings dialog
    const settingsBtn = $('#settings-btn');
    const settingsDialog = $('#settings-dialog');
    const settingsClose = $('#settings-close');
    const settingsSave = $('#settings-save');
    if (settingsBtn && settingsDialog) {
      settingsBtn.addEventListener('click', () => {
        // Populate current values
        const titleRadio = settingsDialog.querySelector(`input[name="setting-title-mode"][value="${state.settings.titleMode}"]`);
        if (titleRadio) titleRadio.checked = true;
        const filterRadio = settingsDialog.querySelector(`input[name="setting-filter-mode"][value="${state.settings.filterMode}"]`);
        if (filterRadio) filterRadio.checked = true;
        settingsDialog.classList.remove('hidden');
      });
      settingsClose.addEventListener('click', () => settingsDialog.classList.add('hidden'));
      settingsDialog.querySelector('.modal-backdrop').addEventListener('click', () => settingsDialog.classList.add('hidden'));
      settingsSave.addEventListener('click', () => {
        const titleMode = settingsDialog.querySelector('input[name="setting-title-mode"]:checked');
        const filterMode = settingsDialog.querySelector('input[name="setting-filter-mode"]:checked');
        if (titleMode) state.settings.titleMode = titleMode.value;
        if (filterMode) state.settings.filterMode = filterMode.value;
        saveSettings();
        settingsDialog.classList.add('hidden');
        toast('Settings saved', 'success');
        applyFilters(); // re-render with new settings
      });
    }
  }

  // --- Init ---

  function init() {
    cacheDom();
    bindEvents();
    setupSidebarToggle();
    setupInfiniteScroll();
    loadSessions();
    connectSSE();
  }

  // Boot
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Public API for debugging
  return { state, loadSessions, toast };
})();
