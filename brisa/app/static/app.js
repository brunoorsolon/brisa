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
      <div class="version">v0.3.0</div>
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