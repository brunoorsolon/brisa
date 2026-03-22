/* ── Theme ──────────────────────────────────────────────── */
const THEME_KEY = 'brisa-theme';

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY) || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  updateThemeButton(saved);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem(THEME_KEY, next);
  updateThemeButton(next);
}

function updateThemeButton(theme) {
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;
  btn.textContent = theme === 'dark' ? '☀ Light' : '☾ Dark';
}

/* ── Navigation ─────────────────────────────────────────── */
function setActiveNav() {
  const path = window.location.pathname.replace(/\/$/, '') || '/';
  document.querySelectorAll('.nav-item').forEach(el => {
    const href = el.getAttribute('href')?.replace(/\/$/, '') || '';
    el.classList.toggle('active', href === path);
  });
}

/* ── Toast ──────────────────────────────────────────────── */
let toastTimer = null;

function showToast(msg, type = 'ok') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = `show toast-${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ''; }, 3000);
}

/* ── API helpers ────────────────────────────────────────── */
async function api(method, path, body = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== null) opts.body = JSON.stringify(body);
  const res = await fetch(`/api${path}`, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(Array.isArray(err.detail) ? err.detail.join('; ') : err.detail);
  }
  return res.json();
}

/* ── Value flash animation ──────────────────────────────── */
function flashValue(el, newText) {
  if (!el) return;
  if (el.textContent === newText) return;
  el.textContent = newText;
  el.classList.remove('flash');
  void el.offsetWidth; // reflow
  el.classList.add('flash');
  setTimeout(() => el.classList.remove('flash'), 600);
}

/* ── Format helpers ─────────────────────────────────────── */
function fmtTemp(val) {
  return val != null ? val.toFixed(1) : '—';
}

function fmtRpm(val) {
  return val != null ? Math.round(val).toString() : '—';
}

function fmtPercent(val) {
  return val != null ? val.toString() : '—';
}

function relativeTime(ts) {
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

/* ── Sidebar HTML (injected into each page) ─────────────── */
const SIDEBAR_HTML = `
<div class="sidebar-logo">
  <div style="display:flex; align-items:center; gap:10px;">
    <img src="/logo.png" alt="Brisa" style="width:48px; height:48px; object-fit:contain; flex-shrink:0;" />
    <div>
      <div class="wordmark">bri<span>sa</span></div>
      <div class="version">v0.2.0</div>
    </div>
  </div>
</div>
<nav class="nav">
  <a class="nav-item" href="/">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
      <rect x="1" y="1" width="6" height="6" rx="1"/>
      <rect x="9" y="1" width="6" height="6" rx="1"/>
      <rect x="1" y="9" width="6" height="6" rx="1"/>
      <rect x="9" y="9" width="6" height="6" rx="1"/>
    </svg>
    Dashboard
  </a>
  <a class="nav-item" href="/devices.html">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
      <circle cx="8" cy="8" r="6"/>
      <circle cx="8" cy="8" r="2"/>
      <line x1="8" y1="2" x2="8" y2="4"/>
      <line x1="8" y1="12" x2="8" y2="14"/>
      <line x1="2" y1="8" x2="4" y2="8"/>
      <line x1="12" y1="8" x2="14" y2="8"/>
    </svg>
    Sensors & Fans
  </a>
  <a class="nav-item" href="/curves.html">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
      <polyline points="1,13 4,9 7,10 10,5 15,2"/>
    </svg>
    Curves
  </a>
  <a class="nav-item" href="/fanconfig.html">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
      <circle cx="8" cy="8" r="2.5"/>
      <path d="M8 1 C8 1 10 3 10 5 C10 6.5 9 7.5 8 8"/>
      <path d="M15 8 C15 8 13 10 11 10 C9.5 10 8.5 9 8 8"/>
      <path d="M8 15 C8 15 6 13 6 11 C6 9.5 7 8.5 8 8"/>
      <path d="M1 8 C1 8 3 6 5 6 C6.5 6 7.5 7 8 8"/>
    </svg>
    Fan Config
  </a>
  <a class="nav-item" href="/history.html">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
      <polyline points="1,12 5,7 8,9 12,4 15,6"/>
      <line x1="1" y1="15" x2="15" y2="15"/>
    </svg>
    History
  </a>
  <a class="nav-item" href="/settings.html">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
      <circle cx="8" cy="8" r="2.5"/>
      <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41"/>
    </svg>
    Settings
  </a>
</nav>
<div class="sidebar-footer">
  <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()">☀ Light</button>
  <a href="https://github.com/brunoorsolon/brisa" target="_blank" rel="noopener" title="GitHub" style="color:var(--text-dim); opacity:0.5; transition:opacity 0.2s;" onmouseenter="this.style.opacity='1'" onmouseleave="this.style.opacity='0.5'">
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
  </a>
</div>
`;

function initPage() {
  // Inject sidebar
  const sidebar = document.getElementById('sidebar');
  if (sidebar) sidebar.innerHTML = SIDEBAR_HTML;

  initTheme();
  setActiveNav();
}

document.addEventListener('DOMContentLoaded', initPage);