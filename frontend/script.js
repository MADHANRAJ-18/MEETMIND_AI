const $=(s, r=document)=>r.querySelector(s), $$=(s, r=document)=>[...r.querySelectorAll(s)];

// --- MeetMind backend integration -----------------------------------------
const API_BASE = (typeof window !== 'undefined' && window.CONFIG && window.CONFIG.API_BASE) || 'http://localhost:5000';
const TOKEN_KEY = 'mm_jwt';
let currentUser = null;
let useBackend = false;
let _supabaseSession = null;

// --- Supabase Auth client --------------------------------------------------
// Credentials loaded from config.js (gitignored). See config.example.js
const SUPABASE_URL  = (typeof window !== 'undefined' && window.CONFIG && window.CONFIG.SUPABASE_URL) || '';
const SUPABASE_ANON = (typeof window !== 'undefined' && window.CONFIG && window.CONFIG.SUPABASE_ANON_KEY) || '';
const _supabase     = (typeof supabase !== 'undefined' && SUPABASE_URL && !SUPABASE_URL.includes('YOUR_SUPABASE_PROJECT'))
  ? supabase.createClient(SUPABASE_URL, SUPABASE_ANON)
  : null;

function getToken() {
  // Use Supabase session token if available, fallback to localStorage
  if (_supabaseSession?.access_token) return _supabaseSession.access_token;
  return localStorage.getItem(TOKEN_KEY);
}
function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  _supabaseSession = null;
}

async function apiFetch(path, options = {}) {
  const token = getToken();
  const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function initialsOf(name) {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  const initials = (parts[0]?.[0] || '') + (parts[1]?.[0] || '');
  return initials ? initials.toUpperCase() : name[0].toUpperCase();
}

function hashCode(str) {
  let h = 0;
  const s = String(str);
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

// Converts a backend meeting row (snake_case, RFC3339-ish times) into the
// shape the existing renderers expect (date/time/duration/color/etc.)
function normalizeMeeting(api) {
  const startTime = (api.start_time || '00:00:00').slice(0, 5);
  const endTime = (api.end_time || '00:00:00').slice(0, 5);
  const [sh, sm] = startTime.split(':').map(Number);
  const [eh, em] = endTime.split(':').map(Number);
  let duration = (eh * 60 + em) - (sh * 60 + sm);
  if (!duration || duration <= 0) duration = 30;
  const participants = api.participants || [];
  let status;
  if (api.status === 'cancelled') status = 'Cancelled';
  else if (api.status === 'pending') status = 'Pending';
  else status = 'Accepted';
  return {
    id: api.id,
    title: api.title,
    description: api.description || '',
    date: api.meeting_date,
    time: startTime,
    duration,
    priority: 'Medium',
    participants,
    status,
    color: colors[hashCode(api.id) % colors.length],
    meetingLink: api.meeting_link || null,
    fromApi: true
  };
}
// ---------------------------------------------------------------------------

function setAuthError(message = '') {
  const box = $('#authError');
  if (!box) return;
  box.textContent = message;
  box.classList.toggle('d-none', !message);
}

function setAuthError(msg = '') {
  const el = $('#authError');
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle('d-none', !msg);
}

const appState= {
  page: 'dashboard', selected: [], view: 'month', date: new Date(), filter: 'all', charts: {
  }
};
const colors=['#6d5dfc', '#20c7c7', '#31b77a', '#f5a623', '#ef6372'];
// People, meetings, and notifications now load from real data only - no
// seeded demo records. Meetings come from the backend (GET /api/meetings).
// People/notifications have no backend yet, so they persist locally (only
// what the user actually adds) and start empty for a fresh account.
let people=JSON.parse(localStorage.getItem('mm_people'))||[];
let meetings=[];
let notifications=JSON.parse(localStorage.getItem('mm_notifications'))||[];
let meetingReminderTimer=null;
function peopleContext() {
  return people
    .filter(p => p.name && p.email)
    .map(p => ({ name: p.name, email: p.email }));
}

function formatSlotLabel(slot) {
  return new Date(slot.date + 'T12:00').toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric'
  }) + ' at ' + fmtTime(slot.time);
}

function showConflictDialogAsync(options) {
  return new Promise(resolve => {
    showConflictDialog({
      ...options,
      onPick: slot => resolve({ action: 'pick', slot }),
      onForce: () => resolve({ action: 'force' })
    });
    const overlay = $('#conflictDialogOverlay');
    overlay?.querySelectorAll('[data-conflict-close]').forEach(btn => {
      const original = btn.onclick;
      btn.onclick = () => {
        original && original();
        resolve({ action: 'cancel' });
      };
    });
  });
}
function showConflictDialog(options) {
  const existing = $('#conflictDialogOverlay');
  if (existing) existing.remove();
  const overlay = document.createElement('div');
  overlay.id = 'conflictDialogOverlay';
  overlay.className = 'conflict-dialog-overlay';
  const slots = options.alternatives || [];
  overlay.innerHTML = `
    <div class="conflict-dialog" role="dialog" aria-modal="true" aria-labelledby="conflictDialogTitle">
      <div class="conflict-dialog-head">
        <b><i class="bi bi-exclamation-triangle"></i> <span id="conflictDialogTitle">${safe(options.title || 'Scheduling conflict')}</span></b>
        <button type="button" class="icon-btn" data-conflict-close aria-label="Close"><i class="bi bi-x-lg"></i></button>
      </div>
      <div class="conflict-dialog-body">
        <p>${safe(options.message || 'That time conflicts with an existing calendar event.')}</p>
        <div class="conflict-slot-list">
          ${slots.length ? slots.map((slot, index) => `
            <button type="button" class="conflict-slot" data-slot-index="${index}">
              <i class="bi bi-calendar-check"></i>
              <span>${safe(formatSlotLabel(slot))}</span>
            </button>
          `).join('') : '<small class="muted">No free alternatives were found.</small>'}
        </div>
      </div>
      <div class="conflict-dialog-actions">
        <button type="button" class="btn btn-soft" data-conflict-close>Cancel</button>
        ${options.allowForce ? '<button type="button" class="btn btn-primary" data-conflict-force>Book original time</button>' : ''}
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelectorAll('[data-conflict-close]').forEach(btn => btn.onclick = close);
  overlay.querySelectorAll('[data-slot-index]').forEach(btn => btn.onclick = () => {
    const slot = slots[Number(btn.dataset.slotIndex)];
    close();
    options.onPick && options.onPick(slot);
  });
  const forceBtn = overlay.querySelector('[data-conflict-force]');
  if (forceBtn) forceBtn.onclick = () => {
    close();
    options.onForce && options.onForce();
  };
}
document.addEventListener('DOMContentLoaded', init);
function savePeople() {
  localStorage.setItem('mm_people', JSON.stringify(people))
}

// ---------------------------------------------------------------------------
// Supabase Auth — sign in / session management
// ---------------------------------------------------------------------------

async function syncUserWithBackend(session) {
  /**
   * After Supabase issues a session, call /api/auth/sync so the Flask
   * backend upserts the user row and stores the Google Calendar tokens.
   * Returns the user profile from our backend.
   */
  const body = {
    access_token:          session.access_token,
    google_access_token:   session.provider_token    || '',
    google_refresh_token:  session.provider_refresh_token || '',
    name:  session.user?.user_metadata?.full_name || session.user?.email || '',
    email: session.user?.email || '',
    avatar_url: session.user?.user_metadata?.avatar_url || '',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  };
  const data = await apiFetch('/api/auth/sync', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return data.user;
}

async function tryBackendSession() {
  // Check if Supabase already has an active session (e.g. after page refresh)
  const { data: { session } } = await _supabase.auth.getSession();
  if (!session) return false;
  _supabaseSession = session;
  try {
    const user = await syncUserWithBackend(session);
    currentUser = user;
    useBackend = true;
    return true;
  } catch (err) {
    console.warn('Backend sync failed:', err.message);
    // Still let them in if Supabase session is valid — backend sync may
    // fail on first load if user row doesn't exist yet
    currentUser = {
      id:    session.user.id,
      email: session.user.email,
      name:  session.user.user_metadata?.full_name || session.user.email,
    };
    useBackend = true;
    return true;
  }
}

async function loadMeetingsFromBackend() {
  try {
    const data = await apiFetch('/api/meetings');
    meetings = (data.meetings || []).map(normalizeMeeting);
    renderAll();
    checkMeetingReminders(false);
  } catch (err) {
    toast('Could not load meetings', err.message);
  }
}

async function init() {
  document.documentElement.dataset.bsTheme = localStorage.getItem('mm_theme') || 'light';
  themeIcon();
  bind();
  defaultsForm();
  renderAll();
  startMeetingReminderWatcher();

  // Listen for Supabase auth state changes (handles redirect callback automatically)
  _supabase.auth.onAuthStateChange(async (event, session) => {
    if (event === 'SIGNED_IN' && session) {
      _supabaseSession = session;
      $('#loader').classList.remove('hide');
      try {
        const user = await syncUserWithBackend(session);
        currentUser = user;
        useBackend = true;
      } catch (_) {
        currentUser = {
          id:    session.user.id,
          email: session.user.email,
          name:  session.user.user_metadata?.full_name || session.user.email,
        };
        useBackend = true;
      }
      await loadMeetingsFromBackend();
      $('#loader').classList.add('hide');
      auth(true);
      startParticipantPoller();
    } else if (event === 'SIGNED_OUT') {
      _supabaseSession = null;
      currentUser = null;
      useBackend = false;
      meetings = [];
      notifications = [];
      stopParticipantPoller();
      renderAll();
      auth(false);
      $('#loader').classList.add('hide');
    } else if (event === 'TOKEN_REFRESHED' && session) {
      _supabaseSession = session;
    }
  });

  const [signedIn] = await Promise.all([
    tryBackendSession(),
    new Promise(resolve => setTimeout(resolve, 500))
  ]);

  if (signedIn) {
    await loadMeetingsFromBackend();
    startParticipantPoller();
  }

  $('#loader').classList.add('hide');
  auth(signedIn);
}

function bind() {
  // Auth Tabs
  const tabSignIn = $('#tabSignIn');
  const tabSignUp = $('#tabSignUp');
  const panelSignIn = $('#panelSignIn');
  const panelSignUp = $('#panelSignUp');
  const authError = $('#authError');
  const authSuccess = $('#authSuccess');

  if (tabSignIn && tabSignUp) {
    tabSignIn.onclick = () => {
      tabSignIn.classList.add('active');
      tabSignUp.classList.remove('active');
      panelSignIn.classList.remove('d-none');
      panelSignUp.classList.add('d-none');
      authError.classList.add('d-none');
      authSuccess.classList.add('d-none');
    };
    tabSignUp.onclick = () => {
      tabSignUp.classList.add('active');
      tabSignIn.classList.remove('active');
      panelSignUp.classList.remove('d-none');
      panelSignIn.classList.add('d-none');
      authError.classList.add('d-none');
      authSuccess.classList.add('d-none');
    };
  }

  // Password Visibility Toggles
  $$('.btn-eye-toggle').forEach(btn => {
    btn.onclick = () => {
      const target = $('#' + btn.dataset.target);
      if (target.type === 'password') {
        target.type = 'text';
        btn.innerHTML = '<i class="bi bi-eye-slash"></i>';
      } else {
        target.type = 'password';
        btn.innerHTML = '<i class="bi bi-eye"></i>';
      }
    };
  });

  function handleAuthError(err) {
    if (authError) {
      authError.textContent = err.message || err;
      authError.classList.remove('d-none');
      authSuccess.classList.add('d-none');
    }
  }

  function handleAuthSuccess(msg) {
    if (authSuccess) {
      authSuccess.textContent = msg;
      authSuccess.classList.remove('d-none');
      authError.classList.add('d-none');
    }
  }

  // Email Sign Up
  const emailSignUpForm = $('#emailSignUpForm');
  if (emailSignUpForm) {
    emailSignUpForm.onsubmit = async (e) => {
      e.preventDefault();
      const btn = $('#emailSignUpBtn');
      const originalHtml = btn.innerHTML;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Creating...';
      btn.disabled = true;
      authError.classList.add('d-none');
      authSuccess.classList.add('d-none');

      const name = $('#suName').value.trim();
      const email = $('#suEmail').value.trim();
      const password = $('#suPassword').value;

      const { data, error } = await _supabase.auth.signUp({
        email,
        password,
        options: {
          data: { full_name: name }
        }
      });

      btn.innerHTML = originalHtml;
      btn.disabled = false;

      if (error) {
        handleAuthError(error);
      } else if (data?.user?.identities?.length === 0) {
        // Supabase returns an empty identities array if the user already exists
        // (to prevent email enumeration when confirm email is enabled)
        handleAuthError(new Error("An account with this email already exists."));
      } else {
        // Depending on your Supabase settings, email confirmation might be required
        if (data?.session) {
          // Auto signed in
        } else {
          handleAuthSuccess("Account created! You can now sign in.");
          emailSignUpForm.reset();
          tabSignIn.onclick(); // switch to sign in tab
        }
      }
    };
  }

  // Email Sign In
  const emailSignInForm = $('#emailSignInForm');
  if (emailSignInForm) {
    emailSignInForm.onsubmit = async (e) => {
      e.preventDefault();
      const btn = $('#emailSignInBtn');
      const originalHtml = btn.innerHTML;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Signing in...';
      btn.disabled = true;
      authError.classList.add('d-none');
      authSuccess.classList.add('d-none');

      const email = $('#siEmail').value.trim();
      const password = $('#siPassword').value;

      const { data, error } = await _supabase.auth.signInWithPassword({
        email,
        password,
      });

      btn.innerHTML = originalHtml;
      btn.disabled = false;

      if (error) {
        handleAuthError(error);
      }
      // On success, onAuthStateChange fires automatically
    };
  }

  // Google Sign-In via Supabase Auth
  const handleGoogleSignIn = async (btnId) => {
    const btn = $('#' + btnId);
    if (!btn) return;
    const originalHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Redirecting…';
    btn.disabled = true;
    const { error } = await _supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        scopes: [
          'openid', 'email', 'profile',
          'https://www.googleapis.com/auth/calendar',
          'https://www.googleapis.com/auth/calendar.events',
          'https://www.googleapis.com/auth/gmail.send',
        ].join(' '),
        redirectTo: window.location.origin + window.location.pathname,
        queryParams: { access_type: 'offline', prompt: 'consent' },
      },
    });
    if (error) {
      btn.innerHTML = originalHtml;
      btn.disabled = false;
      handleAuthError(error);
    }
  };

  if ($('#googleSignIn')) $('#googleSignIn').onclick = () => handleGoogleSignIn('googleSignIn');
  if ($('#googleSignUp')) $('#googleSignUp').onclick = () => handleGoogleSignIn('googleSignUp');

  // Logout
  $('#logout').onclick = async () => {
    await _supabase.auth.signOut();
    clearToken();
    currentUser = null;
    useBackend = false;
    _supabaseSession = null;
    appState.page = 'dashboard';
    document.body.classList.remove('collapsed');
    closeSide();
    meetings = [];
    notifications = [];
    stopParticipantPoller();
    renderAll();
    auth(false);
  };
  $$('[data-page]').forEach(x=>x.onclick=e=> {
    e.preventDefault(); go(x.dataset.page)
  });
  $('#collapse').onclick=()=>document.body.classList.toggle('collapsed');
  $('#menu').onclick=()=> {
    $('#sidebar').classList.add('open');
    $('#backdrop').classList.add('show')
  };
  $('#backdrop').onclick=closeSide;
  $('#theme').onclick=toggleTheme;
  $('#globalSearch').onkeydown=e=> {
    if(e.key==='Enter') {
      go('calendar');
      toast('Search results', `Showing meetings related to "${e.target.value||'all meetings'}".`)
    }
  };
  $('#meetingDescription').oninput=e=> {
    $('#charCount').textContent=`${e.target.value.length} / 300`;
    preview()
  };
  ['meetingTitle', 'meetingDate', 'meetingTime', 'meetingDuration', 'meetingPriority'].forEach(id=>$(`#${id}`).oninput=preview);
  $('#pickerSearch').onfocus=()=> {
    $('#pickerList').classList.add('show');
    renderPicker()
  };
  $('#pickerSearch').oninput=renderPicker;
  $('#scheduleForm').onsubmit=schedule;
  $('#clearForm').onclick=()=>setTimeout(()=> {
    appState.selected=[];
    _scheduleBypassNecessity = false;
    const banner = $('#scheduleNecessityBanner');
    if (banner) { banner.classList.add('d-none'); banner.innerHTML = ''; }
    defaultsForm(); renderSelected(); preview()
  }, 0);
  $$('[data-view]').forEach(b=>b.onclick=()=> {
    appState.view=b.dataset.view; $$('[data-view]').forEach(x=>x.classList.toggle('active', x===b)); renderCalendar()
  });
  $('#calPrev').onclick=()=>shiftCal(-1);
  $('#calNext').onclick=()=>shiftCal(1);
  $('#calToday').onclick=()=> {
    appState.date=new Date();
    renderCalendar()
  };
  $('#peopleSearch').oninput=renderPeople;
  $('#statusFilter').onchange=renderPeople;
  $('#personForm').onsubmit=addPerson;
  $('#editMeetingForm').onsubmit=saveMeetingEdit;
  $$('[data-filter]').forEach(b=>b.onclick=()=> {
    $$('[data-filter]').forEach(x=>x.classList.remove('active')); b.classList.add('active'); appState.filter=b.dataset.filter; renderNotifications()
  });
  $('#markRead').onclick=()=> {
    notifications.forEach(n=>n.unread=false);
    saveNotif();
    renderNotifications();
    toast('All caught up', 'Every notification has been marked as read.')
  };
  // Delete confirmation modal
  $('#confirmDeleteBtn').onclick = _confirmDelete;
  // Regenerate email draft button
  $('#regenerateDraft')?.addEventListener('click', () => generateEmailDraft(true));
  // Smart scheduler tabs + optimizer
  bindSmartTabs();
  // Email tag input for direct participant email entry
  bindEmailTagInput();
  bindChatAssistant()
}
function auth(ok) {
  $('#loginPage').classList.toggle('d-none', ok);
  $('#app').classList.toggle('d-none', !ok);
  $('#chatLauncher').classList.toggle('d-none', !ok);
  if(!ok) {
    $('#chatWidget').classList.add('d-none');
    stopParticipantPoller();
  }
  if(ok) {
    applyUserToUi();
    go(appState.page);
    startParticipantPoller();
  }
}
function applyUserToUi() {
  if (!currentUser) return;
  const initials = initialsOf(currentUser.name);
  $('#sideUserName').textContent = currentUser.name || 'Signed in';
  $('#sideUserEmail').textContent = currentUser.email || '';
  $('#sideUserAvatar').textContent = initials;
  $('#headerUserAvatar').textContent = initials;
  $('#dashboardUserName').textContent = (currentUser.name || '').split(' ')[0] || 'there';
}
function go(p) {
  appState.page=p;
  $$('.page').forEach(x=>x.classList.toggle('active', x.id===p+'Page'));
  $$('nav a').forEach(x=>x.classList.toggle('active', x.dataset.page===p));
  closeSide();
  scrollTo( {
    top: 0, behavior: 'smooth'
  });
  if(p==='dashboard')setTimeout(dashChart, 50);
  if(p==='analytics')setTimeout(analyticsCharts, 50);
  if(p==='calendar')renderCalendar();
  if(p==='smart')ensurePageChatGreeted();
  if(p==='timetable')initTimetablePage();
}
function closeSide() {
  $('#sidebar').classList.remove('open');
  $('#backdrop').classList.remove('show')
}
function toggleTheme() {
  let t=document.documentElement.dataset.bsTheme==='dark'?'light': 'dark';
  document.documentElement.dataset.bsTheme=t;
  localStorage.setItem('mm_theme', t);
  themeIcon();
  Object.values(appState.charts).forEach(c=>c.destroy());
  appState.charts= {
  };
  if(appState.page==='dashboard')dashChart();
  if(appState.page==='analytics')analyticsCharts()
}
function themeIcon() {
  $('#theme i')&&($('#theme i').className=document.documentElement.dataset.bsTheme==='dark'?'bi bi-sun': 'bi bi-moon-stars')
}
function meetingStartDate(meeting) {
  if (!meeting || !meeting.date || !meeting.time) return null;
  const date = new Date(`${meeting.date}T${meeting.time}`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function checkMeetingReminders(showToast = false) {
  const now = new Date();
  let added = false;
  meetings.forEach(meeting => {
    if (!meeting || meeting.status !== 'Accepted') return;
    const startsAt = meetingStartDate(meeting);
    if (!startsAt) return;
    const minutesUntil = (startsAt.getTime() - now.getTime()) / 60000;
    if (minutesUntil <= 0 || minutesUntil > 30) return;
    const reminderKey = `meeting-reminder-${meeting.id}`;
    if (notifications.some(n => n.reminderKey === reminderKey)) return;
    notifications.unshift({
      id: Date.now() + Math.floor(Math.random() * 1000),
      reminderKey,
      type: 'reminder',
      unread: true,
      title: 'Meeting starts soon',
      message: `${meeting.title} starts at ${fmtTime(meeting.time)}. You have ${Math.max(1, Math.ceil(minutesUntil))} minutes left.`,
      time: '30-minute reminder'
    });
    added = true;
  });
  if (added) {
    saveNotif();
    renderNotifications();
    if (showToast) toast('Meeting reminder', 'A meeting starts within the next 30 minutes.');
  }
}

function startMeetingReminderWatcher() {
  checkMeetingReminders(false);
  if (meetingReminderTimer) clearInterval(meetingReminderTimer);
  meetingReminderTimer = setInterval(() => checkMeetingReminders(true), 60000);
}
function renderAll() {
  renderDashboardDate();
  renderStats();
  renderMeetings();
  renderTodaysFocus();
  renderInvites();
  renderPicker();
  renderSelected();
  preview();
  renderCalendar();
  renderPeople();
  renderNotifications();
  renderAnalyticsStats()
}
function renderDashboardDate() {
  let now=new Date();
  let label=now.toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric'
  }).toUpperCase();
  $('#dashboardDateKicker')&&($('#dashboardDateKicker').textContent=label);
  $('#todaysFocusDate')&&($('#todaysFocusDate').textContent=now.toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric'
  }))
}
// Computes today's real meeting load: minutes booked vs. a 9am-6pm (9h)
// working window, with no fabricated "focus time" or productivity score.
function renderTodaysFocus() {
  let el=$('#todaysFocus');
  if(!el)return;
  let today=todayIso();
  let todays=meetings.filter(m=>m.date===today);
  let busyMinutes=todays.reduce((sum, m)=>sum+(Number(m.duration)||0), 0);
  let workMinutes=9*60;
  let openMinutes=Math.max(workMinutes-busyMinutes, 0);
  let busyPct=Math.min(Math.round((busyMinutes/workMinutes)*100), 100);
  let fmt=mins=> {
    let h=Math.floor(mins/60), m=mins%60;
    return (h?`${h}h `: '')+(m||!h?`${m}m`: '')
  };
  el.innerHTML=`<div class="focus">
    <div class="focus-ring" style="background:conic-gradient(var(--p) 0 ${busyPct}%, var(--line) ${busyPct}% 100%)">
      <b>${todays.length}<small>meeting${todays.length===1?'': 's'}</small></b>
    </div>
    <div>
      <p><i class="dot purple"></i> Meeting time <b>${todays.length?fmt(busyMinutes): '0m'}</b></p>
      <p><i class="dot orange"></i> Open time <b>${fmt(openMinutes)}</b></p>
    </div>
  </div>`
}
function todayIso() { return localIso(new Date()); }
function renderStats() {
  let today=todayIso();
  let data=[['Total meetings', meetings.length, 'bi-calendar3', '#6d5dfc', 'rgba(109,93,252,.12)'], ['Upcoming meetings', meetings.filter(m=>m.date>=today).length, 'bi-clock-history', '#20aeb2', 'rgba(32,199,199,.12)'], ['Pending invitations', meetings.filter(m=>m.status==='Pending').length, 'bi-envelope-paper', '#e69a19', 'rgba(245,166,35,.14)'], ['Accepted meetings', meetings.filter(m=>m.status==='Accepted').length, 'bi-check2-circle', '#31b77a', 'rgba(49,183,122,.12)']];
  $('#stats').innerHTML=data.map(s=>`<div class="col-sm-6 col-xl-3"><div class="stat"><div class="stat-head"><i class="stat-icon bi ${s[2]}" style="color:${s[3]};background:${s[4]}"></i></div><h2>${s[1]}</h2><p>${s[0]}</p></div></div>`).join('')
}
function renderMeetings() {
  let today=todayIso();
  let upcoming=meetings.filter(m=>m.date>=today).slice(0, 5);
  $('#meetingList').innerHTML=upcoming
    .map(m=>`<div class="meeting-row">
      <div class="meeting-time">
        <b>${fmtTime(m.time)}</b>
        <small>${new Date(m.date+'T12:00').toLocaleDateString('en-US',{weekday:'short'})}</small>
      </div>
      <div class="meeting-info">
        <h4><i class="dot" style="background:${m.color}"></i> ${m.title}</h4>
        <p>${m.duration} min - ${m.priority} priority</p>
        <span class="meeting-status ${m.status==='Pending'?'pending':'accepted'}">${m.status==='Pending'?'Waiting for participant acceptance':'Accepted'}</span>
      </div>
      <div class="meeting-actions">
        <button type="button" class="meeting-action edit" title="Edit meeting" aria-label="Edit ${safe(m.title)}" onclick="openEditMeeting('${m.id}')"><i class="bi bi-pencil"></i></button>
        <button type="button" class="meeting-action delete" title="Delete meeting" aria-label="Delete ${safe(m.title)}" onclick="deleteMeeting('${m.id}')"><i class="bi bi-trash"></i></button>
      </div>
    </div>`)
    .join('') || '<div class="empty-state"><i class="bi bi-calendar2-week"></i><p>No upcoming meetings yet.</p><button class="btn btn-soft btn-sm" onclick="go(\'schedule\')">Schedule one</button></div>'
}
function openEditMeeting(id) {
  const meeting=meetings.find(m=>String(m.id)===String(id));
  if(!meeting)return;
  $('#editMeetingId').value=meeting.id;
  $('#editMeetingTitle').value=meeting.title;
  $('#editMeetingDate').value=meeting.date;
  $('#editMeetingTime').value=meeting.time;
  $('#editMeetingDuration').value=String(meeting.duration);
  $('#editMeetingPriority').value=meeting.priority;
  bootstrap.Modal.getOrCreateInstance($('#editMeetingModal')).show()
}
async function saveMeetingEdit(e) {
  e.preventDefault();
  const id=$('#editMeetingId').value;
  const meeting=meetings.find(m=>String(m.id)===String(id));
  if(!meeting)return;

  const title=$('#editMeetingTitle').value.trim();
  const date=$('#editMeetingDate').value;
  const time=$('#editMeetingTime').value;
  const duration=Number($('#editMeetingDuration').value);
  const priority=$('#editMeetingPriority').value;

  if (meeting.fromApi) {
    const submitBtn=e.target.querySelector('button[type=submit], .modal-footer .btn-primary');
    const originalLabel=submitBtn?submitBtn.innerHTML:null;
    if(submitBtn)submitBtn.innerHTML='<span class="spinner-border spinner-border-sm"></span> Saving...';
    try {
      const data=await apiFetch(`/api/meetings/${meeting.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          title, date, start_time: time, duration_minutes: duration,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
        })
      });
      Object.assign(meeting, normalizeMeeting(data.meeting));
    } catch (err) {
      toast('Could not update meeting', err.message);
      if(submitBtn)submitBtn.innerHTML=originalLabel;
      return;
    }
    if(submitBtn)submitBtn.innerHTML=originalLabel;
  } else {
    meeting.title=title;
    meeting.date=date;
    meeting.time=time;
    meeting.duration=duration;
    meeting.priority=priority;
    saveMeetings();
  }

  renderStats();
  renderMeetings();
  renderInvites();
  renderCalendar();
  bootstrap.Modal.getInstance($('#editMeetingModal')).hide();
  toast('Meeting updated', meeting.title+' has been updated.')
}
let _pendingDeleteId = null;
async function deleteMeeting(id) {
  const meeting = meetings.find(m => String(m.id) === String(id));
  if (!meeting) return;
  _pendingDeleteId = id;
  $('#deleteMeetingName').textContent = `"${meeting.title}" on ${fmtDate(meeting.date || meeting.meeting_date)}`;
  bootstrap.Modal.getOrCreateInstance($('#deleteMeetingModal')).show();
}
async function _confirmDelete() {
  const id = _pendingDeleteId;
  if (!id) return;
  _pendingDeleteId = null;
  const modal = bootstrap.Modal.getInstance($('#deleteMeetingModal'));
  const btn = $('#confirmDeleteBtn');
  const orig = btn.innerHTML;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Deleting...';
  btn.disabled = true;
  const meeting = meetings.find(m => String(m.id) === String(id));
  if (meeting && meeting.fromApi) {
    try {
      await apiFetch(`/api/meetings/${meeting.id}`, { method: 'DELETE' });
    } catch (err) {
      modal && modal.hide();
      btn.innerHTML = orig; btn.disabled = false;
      toast('Could not delete meeting', err.message);
      return;
    }
  }
  meetings = meetings.filter(m => String(m.id) !== String(id));
  if (meeting && !meeting.fromApi) saveMeetings();
  modal && modal.hide();
  btn.innerHTML = orig; btn.disabled = false;
  renderStats(); renderMeetings(); renderInvites(); renderCalendar();
  toast('Meeting deleted', (meeting?.title || 'Meeting') + ' was removed from your schedule.');
}
window.openEditMeeting = openEditMeeting;
window.deleteMeeting = deleteMeeting;
function pushNotification(type, title, message) {
  const id = Date.now();
  notifications.unshift({ id, type, title, message, time: 'Just now', unread: true });
  saveNotif();
  renderNotifications();
}

function renderInvites() {
  let list = meetings.filter(m => m.status === 'Pending');
  const pill = $('#pendingPill');
  if (pill) pill.textContent = list.length ? list.length + ' pending' : '';
  const el = $('#invites');
  if (!el) return;
  el.innerHTML = list.map(m => {
    const statuses = m._participantStatuses || [];
    const statusHtml = statuses.length
      ? statuses.map(s => {
          const icon = s.status === 'accepted' ? 'bi-check-circle-fill text-success'
            : s.status === 'declined' ? 'bi-x-circle-fill text-danger'
            : s.status === 'tentative' ? 'bi-question-circle-fill text-warning'
            : 'bi-hourglass-split text-muted';
          const label = s.status === 'accepted' ? 'Accepted'
            : s.status === 'declined' ? 'Declined'
            : s.status === 'tentative' ? 'Tentative'
            : 'Awaiting';
          return `<span class="participant-status-chip"><i class="bi ${icon}"></i> ${safe(s.displayName || s.email)} <em>${label}</em></span>`;
        }).join('')
      : `<span class="muted small"><i class="bi bi-hourglass-split"></i> Waiting for participant responses</span>`;
    return `<div class="invite" id="invite-${m.id}">
      <div style="flex:1;min-width:0">
        <b>${safe(m.title)}</b>
        <small>${fmtDate(m.date)} at ${fmtTime(m.time)}</small>
        <div class="participant-statuses mt-1">${statusHtml}</div>
      </div>
    </div>`;
  }).join('') || '<p class="muted small p-2">No pending meetings.</p>';
}

// ---------------------------------------------------------------------------
// Participant response poller — checks every 90 seconds whether any
// participant has accepted, declined, or marked tentative. Pushes a
// notification for each status change detected.
// ---------------------------------------------------------------------------
const _knownStatuses = {};   // { meetingId: { email: status } }
let _pollTimer = null;

async function pollParticipantStatuses() {
  const pending = meetings.filter(m => m.status === 'Pending' && m.fromApi);
  if (!pending.length || !useBackend) return;

  for (const meeting of pending) {
    try {
      const data = await apiFetch(`/api/meetings/${meeting.id}/participant-status`);
      const statuses = data.statuses || [];
      meeting._participantStatuses = statuses;

      const isFirstPoll = !(_knownStatuses[meeting.id]);
      const prev = _knownStatuses[meeting.id] || {};
      const nextMap = {};
      let changed = false;

      for (const s of statuses) {
        nextMap[s.email] = s.status;
        const oldStatus = prev[s.email];
        if (!isFirstPoll && oldStatus !== undefined && oldStatus !== s.status) {
          // Status changed — push a notification
          const name = s.displayName || s.email;
          if (s.status === 'accepted') {
            pushNotification('invitation',
              `${name} accepted "${meeting.title}"`,
              `${fmtDate(meeting.date)} at ${fmtTime(meeting.time)}`);
          } else if (s.status === 'declined') {
            pushNotification('cancellation',
              `${name} declined "${meeting.title}"`,
              `${fmtDate(meeting.date)} at ${fmtTime(meeting.time)}`);
          } else if (s.status === 'tentative') {
            pushNotification('reminder',
              `${name} marked "${meeting.title}" as tentative`,
              `${fmtDate(meeting.date)} at ${fmtTime(meeting.time)}`);
          }
          changed = true;
        }
      }

      _knownStatuses[meeting.id] = nextMap;

      // If all participants accepted, mark the meeting as Accepted
      const allAccepted = statuses.length > 0 &&
        statuses.every(s => s.status === 'accepted');
      if (allAccepted && meeting.status === 'Pending') {
        meeting.status = 'Accepted';
        if (!isFirstPoll) {
          pushNotification('invitation',
            `All participants accepted "${meeting.title}"`,
            `Meeting is confirmed for ${fmtDate(meeting.date)} at ${fmtTime(meeting.time)}.`);
        }
        changed = true;
      }

      if (changed) {
        renderStats(); renderMeetings(); renderInvites();
      } else {
        renderInvites(); // still refresh chips in case first load
      }
    } catch (_) {
      // Silently skip - don't disrupt the UI if one poll fails
    }
  }
}

function startParticipantPoller() {
  clearInterval(_pollTimer);
  pollParticipantStatuses();   // run immediately on login / meeting creation
  _pollTimer = setInterval(pollParticipantStatuses, 90000);
}

function stopParticipantPoller() {
  clearInterval(_pollTimer);
  _pollTimer = null;
}
function defaultsForm() {
  let tomorrow=new Date();
  tomorrow.setDate(tomorrow.getDate()+1);
  $('#meetingDate').value=localIso(tomorrow);
  $('#meetingTime').value='10:30';
  $('#meetingDuration').value='30';
  $('#meetingPriority').value='Medium'
}
function renderPicker() {
  let q=$('#pickerSearch').value.toLowerCase();
  $('#pickerList').innerHTML=people.filter(p=>!appState.selected.includes(p.id)&&(p.name+' '+p.email).toLowerCase().includes(q)).slice(0, 8).map(p=>`<div class="pick" onclick="selectPerson(${p.id})"><div><b>${p.name}</b><small>${p.email}</small></div><i class="bi bi-plus-circle text-primary"></i></div>`).join('')
}
window.selectPerson=id=> {
  appState.selected.push(id);
  $('#pickerSearch').value='';
  renderPicker();
  renderSelected();
  preview()
};
window.removePerson=id=> {
  appState.selected=appState.selected.filter(x=>x!==id);
  renderPicker();
  renderSelected();
  preview()
};
function renderSelected() {
  $('#selectedPeople').innerHTML=appState.selected.map(id=> {
    let p=people.find(x=>x.id===id); return `<span class="selected">${p.name}<button type="button" onclick="removePerson(${id})"><i class="bi bi-x"></i></button></span>`
  }).join('')||'<small class="muted">No participants selected yet.</small>'
}
let _draftDebounceTimer = null;
function preview() {
  let t=$('#meetingTitle').value;
  if(!t) {
    $('#preview').innerHTML='<div class="preview-empty"><i class="bi bi-calendar2-plus fs-3 d-block mb-2"></i>Your meeting preview will appear here.</div>';
    $('#emailDraftBlock').style.display='none';
    return
  }
  $('#preview').innerHTML=`<h4>${safe(t)}</h4><div class="preview-line"><i class="bi bi-calendar3"></i>${fmtDate($('#meetingDate').value)} at ${fmtTime($('#meetingTime').value)}</div><div class="preview-line"><i class="bi bi-hourglass"></i>${$('#meetingDuration').value} minutes - ${$('#meetingPriority').value}</div><div class="preview-line"><i class="bi bi-people"></i>${appState.selected.length + getTagEmails().length} participants</div>`;

  const hasParticipants = appState.selected.length > 0 || getTagEmails().length > 0;
  const block = $('#emailDraftBlock');
  if (hasParticipants && t) {
    block.style.display = '';
    clearTimeout(_draftDebounceTimer);
    _draftDebounceTimer = setTimeout(generateEmailDraft, 800);
  } else {
    block.style.display = 'none';
  }
}

let _lastDraftKey = '';
async function generateEmailDraft(force = false) {
  if (!useBackend) return;
  const title = $('#meetingTitle').value.trim();
  const date = $('#meetingDate').value;
  const time = $('#meetingTime').value;
  const duration = $('#meetingDuration').value;
  const description = ($('#meetingDescription')?.value || '').trim();
  const key = `${title}|${date}|${time}|${duration}|${description}`;
  if (!force && key === _lastDraftKey) return;
  _lastDraftKey = key;

  $('#emailDraftLoading').style.display = '';
  $('#emailDraftContent').style.display = 'none';

  try {
    const data = await apiFetch('/api/ai/draft-email', {
      method: 'POST',
      body: JSON.stringify({
        meeting: {
          title,
          date,
          start_time: time,
          duration_minutes: Number(duration),
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
          description,
        }
      })
    });
    $('#emailDraftSubject').value = data.subject || '';
    $('#emailDraftBody').value = data.body || '';
    $('#emailDraftLoading').style.display = 'none';
    $('#emailDraftContent').style.display = '';
  } catch (err) {
    $('#emailDraftLoading').innerHTML = `<i class="bi bi-exclamation-circle text-warning"></i> Could not generate draft: ${err.message}`;
  }
}
let _scheduleBypassNecessity = false;

async function schedule(e) {
  e.preventDefault();

  const participantEmails = [...new Set([
    ...appState.selected.map(id => people.find(p => p.id === id)).filter(Boolean).map(p => p.email),
    ...getTagEmails()
  ])];

  if (!participantEmails.length) {
    toast('Add a participant', 'Add at least one participant email before scheduling.');
    return;
  }

  // --- Necessity check (skip if user already clicked "Book anyway") ---
  if (useBackend && !_scheduleBypassNecessity) {
    const title = $('#meetingTitle').value.trim();
    const description = ($('#meetingDescription')?.value || '').trim();
    const duration = +$('#meetingDuration').value || 30;
    const date = $('#meetingDate').value;
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

    const banner = $('#scheduleNecessityBanner');
    banner.classList.add('d-none');
    banner.innerHTML = '';

    try {
      const check = await apiFetch('/api/ai/check-necessity', {
        method: 'POST',
        body: JSON.stringify({ title, description, duration_minutes: duration, date, timezone })
      });

      const verdict = check.verdict || 'necessary';
      const existingCount = check.existing_count || 0;

      // Overload warning
      if (verdict === 'overload_risk' || existingCount >= 4) {
        showScheduleNecessityBanner(banner, {
          icon: 'bi-exclamation-triangle',
          color: 'overload',
          heading: `Heavy day — ${existingCount} meetings already on ${fmtDate(date)}`,
          reason: check.reason || 'This day is already quite full.',
          suggestion: check.suggestion || 'Consider moving this to a lighter day.',
          verdict,
          participants: participantEmails,
          meetingData: { title, description, duration_minutes: duration, date, start_time: $('#meetingTime').value, timezone },
          onBookAnyway: () => { _scheduleBypassNecessity = true; e.target.requestSubmit(); }
        });
        return;
      }

      // Unnecessary meeting
      if (verdict === 'email_instead' || verdict === 'async_instead') {
        showScheduleNecessityBanner(banner, {
          icon: verdict === 'email_instead' ? 'bi-envelope' : 'bi-camera-video',
          color: 'warning',
          heading: verdict === 'email_instead' ? 'Consider sending an email instead' : 'Consider handling this async',
          reason: check.reason || '',
          suggestion: check.suggestion || '',
          verdict,
          participants: participantEmails,
          meetingData: { title, description, duration_minutes: duration, date, start_time: $('#meetingTime').value, timezone },
          onBookAnyway: () => { _scheduleBypassNecessity = true; e.target.requestSubmit(); }
        });
        return;
      }
    } catch (_) {
      // If necessity check fails, proceed silently — don't block scheduling
    }
  }

  _scheduleBypassNecessity = false;

  if (useBackend) {
    await scheduleViaBackend(e);
    return;
  }

  let m = {
    id: Date.now(), title: $('#meetingTitle').value,
    description: $('#meetingDescription').value,
    date: $('#meetingDate').value, time: $('#meetingTime').value,
    duration: +$('#meetingDuration').value,
    priority: $('#meetingPriority').value,
    participants: [...appState.selected], status: 'Accepted',
    color: colors[meetings.length % 5]
  };
  meetings.unshift(m);
  saveMeetings();
  e.target.reset();
  appState.selected = [];
  defaultsForm();
  renderAll();
  go('calendar');
  toast('Meeting scheduled', `${m.title} is now on your calendar.`);
}

function showScheduleNecessityBanner(banner, opts) {
  const colorClass = opts.color === 'overload' ? 'necessity-banner-overload' : 'necessity-banner-warning';
  banner.className = `schedule-necessity-banner ${colorClass}`;
  banner.classList.remove('d-none');

  banner.innerHTML = `
    <div class="necessity-banner-head">
      <i class="bi ${opts.icon}"></i>
      <b>${opts.heading}</b>
    </div>
    ${opts.reason ? `<p class="necessity-banner-reason">${safe(opts.reason)}</p>` : ''}
    ${opts.suggestion ? `<p class="necessity-banner-suggestion">${safe(opts.suggestion)}</p>` : ''}
    <div class="necessity-banner-actions">
      <button type="button" class="btn btn-soft btn-sm nb-email-btn">
        <i class="bi bi-envelope"></i> Send email instead
      </button>
      <button type="button" class="btn btn-primary btn-sm nb-book-btn">
        <i class="bi bi-calendar-check"></i> Book anyway
      </button>
    </div>
    <div class="necessity-compose d-none nb-compose">
      <div class="necessity-compose-head">
        <span><i class="bi bi-stars"></i> AI-drafted email</span>
        <small class="muted">Edit before sending</small>
      </div>
      <div class="necessity-compose-loading">
        <span class="spinner-border spinner-border-sm"></span> Drafting email...
      </div>
      <div class="necessity-compose-fields d-none">
        <div class="mb-2">
          <label class="form-label necessity-label">To</label>
          <input type="text" class="form-control form-control-sm necessity-to" value="${safe(opts.participants.join(', '))}">
        </div>
        <div class="mb-2">
          <label class="form-label necessity-label">Subject</label>
          <input type="text" class="form-control form-control-sm necessity-subject">
        </div>
        <div class="mb-2">
          <label class="form-label necessity-label">Message</label>
          <textarea class="form-control form-control-sm necessity-body" rows="6"></textarea>
        </div>
        <div class="necessity-send-row">
          <button type="button" class="btn btn-primary btn-sm nb-send-btn">
            <i class="bi bi-send"></i> Send email
          </button>
          <button type="button" class="btn btn-soft btn-sm nb-cancel-compose">Cancel</button>
        </div>
      </div>
    </div>`;

  banner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  // Book anyway
  banner.querySelector('.nb-book-btn').onclick = () => {
    banner.classList.add('d-none');
    opts.onBookAnyway();
  };

  // Send email instead — open compose
  banner.querySelector('.nb-email-btn').onclick = async () => {
    const compose = banner.querySelector('.nb-compose');
    const loading = banner.querySelector('.necessity-compose-loading');
    const fields = banner.querySelector('.necessity-compose-fields');
    compose.classList.remove('d-none');
    banner.querySelector('.necessity-banner-actions').style.display = 'none';

    try {
      const draft = await apiFetch('/api/ai/draft-email', {
        method: 'POST',
        body: JSON.stringify({ meeting: opts.meetingData })
      });
      banner.querySelector('.necessity-subject').value = draft.subject || '';
      banner.querySelector('.necessity-body').value = draft.body || '';
    } catch (_) {
      banner.querySelector('.necessity-subject').value = `Re: ${opts.meetingData.title || 'Update'}`;
      banner.querySelector('.necessity-body').value = `Hi,\n\nJust a quick update regarding ${opts.meetingData.title || 'our topic'}.\n\nBest regards`;
    }

    loading.classList.add('d-none');
    fields.classList.remove('d-none');
    banner.querySelector('.necessity-to').focus();
  };

  // Cancel compose
  banner.querySelector('.nb-cancel-compose').onclick = () => {
    banner.querySelector('.nb-compose').classList.add('d-none');
    banner.querySelector('.necessity-banner-actions').style.display = '';
  };

  // Send
  banner.querySelector('.nb-send-btn').onclick = async () => {
    const sendBtn = banner.querySelector('.nb-send-btn');
    const toVal = banner.querySelector('.necessity-to').value.trim();
    const subject = banner.querySelector('.necessity-subject').value.trim();
    const body = banner.querySelector('.necessity-body').value.trim();
    const recipients = toVal.split(/[,;\s]+/).map(e => e.trim()).filter(e => e.includes('@'));
    if (!recipients.length) { banner.querySelector('.necessity-to').focus(); return; }

    sendBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Sending...';
    sendBtn.disabled = true;

    try {
      await apiFetch('/api/ai/send-skip-email', {
        method: 'POST',
        body: JSON.stringify({ meeting: opts.meetingData, recipients, subject, body })
      });
      banner.innerHTML = `<p class="necessity-resolved" style="margin:0;padding:4px 0">
        <i class="bi bi-check-circle-fill text-success"></i>
        Email sent to ${recipients.join(', ')} — no meeting needed!
      </p>`;
      toast('Email sent', `"${subject}" delivered.`);
    } catch (err) {
      sendBtn.innerHTML = '<i class="bi bi-send"></i> Send email';
      sendBtn.disabled = false;
      toast('Could not send email', err.message);
    }
  };
}
async function scheduleViaBackend(e) {
  const submitBtn = e.target.querySelector('button[type=submit], button.btn-primary');
  const originalLabel = submitBtn ? submitBtn.innerHTML : null;

  // Merge people-picker selections with directly typed emails, deduped
  const pickerEmails = appState.selected
    .map(id => people.find(p => p.id === id))
    .filter(Boolean)
    .map(p => p.email);
  const participantEmails = [...new Set([...pickerEmails, ...getTagEmails()])];

  const basePayload = {
    title: $('#meetingTitle').value,
    description: $('#meetingDescription').value,
    date: $('#meetingDate').value,
    start_time: $('#meetingTime').value,
    duration_minutes: +$('#meetingDuration').value,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    participants: participantEmails,
    email_subject: $('#emailDraftSubject')?.value?.trim() || '',
    email_body: $('#emailDraftBody')?.value?.trim() || '',
  };

  let force = false;
  try {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      if (submitBtn) submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Scheduling...';
      try {
        const data = await apiFetch('/api/meetings', {
          method: 'POST',
          body: JSON.stringify({ ...basePayload, force })
        });
        meetings.unshift(normalizeMeeting(data.meeting));
        e.target.reset();
        appState.selected=[];
        clearTagEmails();
        _lastDraftKey = '';
        $('#emailDraftBlock').style.display = 'none';
        $('#emailDraftContent').style.display = 'none';
        $('#emailDraftLoading').style.display = '';
        defaultsForm();
        renderAll();
        checkMeetingReminders(false);
        startParticipantPoller();
        go('calendar');
        const emailSent = data.meeting.email_status && data.meeting.email_status.sent;
        toast(emailSent ? 'Email invite sent' : 'Meeting created', emailSent ? 'Waiting for participants to accept before it proceeds.' : 'Meeting was created, but email could not be sent from this machine.');
        return;
      } catch (err) {
        if (err.status === 409 && !force) {
          const alts = (err.data && err.data.alternatives) || [];
          const choice = await showConflictDialogAsync({
            title: 'That time is busy',
            message: 'Pick a suggested free slot, or book the original time anyway.',
            alternatives: alts,
            allowForce: true
          });
          if (choice.action === 'force') { force = true; continue; }
          if (choice.action === 'pick' && choice.slot) {
            $('#meetingDate').value = choice.slot.date;
            $('#meetingTime').value = choice.slot.time;
            preview();
            toast('Free slot applied', 'Review the updated time and schedule again.');
            return;
          }
          toast('Not scheduled', 'No changes were made.');
          return;
        }
        toast('Could not schedule meeting', err.message);
        return;
      }
    }
  } finally {
    if (submitBtn) submitBtn.innerHTML = originalLabel;
  }
}
function shiftCal(n) {
  if(appState.view==='month')appState.date.setMonth(appState.date.getMonth()+n);
  else appState.date.setDate(appState.date.getDate()+n*(appState.view==='week'?7: 1));
  renderCalendar()
}
function renderCalendar() {
  appState.view==='month'?renderMonth(): appState.view==='week'?renderWeek(): renderDay()
}
function renderMonth() {
  let y=appState.date.getFullYear(), mo=appState.date.getMonth(), first=new Date(y, mo, 1), start=new Date(y, mo, 1-first.getDay());
  $('#calTitle').textContent=appState.date.toLocaleDateString('en-US', {
    month: 'long', year: 'numeric'
  });
  let h='<div class="calendar-grid">'+['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(x=>`<div class="weekday">${x}</div>`).join('');
  let today=todayIso();
  for(let i=0; i<42; i++) {
    let d=new Date(start);
    d.setDate(start.getDate()+i);
    let iso=localIso(d), ev=meetings.filter(m=>m.date===iso);
    h+=`<div class="cal-day ${d.getMonth()!==mo?'out':''} ${iso===today?'today':''}"><span class="day-num">${d.getDate()}</span>${ev.slice(0,3).map(m=>`<div class="event" style="--event:${m.color}" title="${m.title}"><b>${
      fmtTime(m.time)
    }
    </b> ${
      m.title
    }
    </div>`).join('')}${ev.length>3?`<small>+${
      ev.length-3
    }
    more</small>`:''}</div>`
  }
  $('#calendar').innerHTML=h+'</div>'
}
function renderWeek() {
  let s=new Date(appState.date);
  s.setDate(s.getDate()-s.getDay());
  let days=Array.from( {
    length: 7
  }, (_, i)=> {
    let d=new Date(s); d.setDate(s.getDate()+i); return d
  }), end=days[6];
  $('#calTitle').textContent=`${s.toLocaleDateString('en-US',{month:'short',day:'numeric'})} - ${end.toLocaleDateString('en-US',{month:'short',day:'numeric'})}`;
  let h='<div class="table-responsive"><div class="week-grid"><div></div>'+days.map(d=>`<div><b>${d.toLocaleDateString('en-US',{weekday:'short'})}</b><br>${d.getDate()}</div>`).join('');
  for(let hr=8; hr<=17; hr++) {
    h+=`<div>${fmtTime(String(hr).padStart(2,'0')+':00')}</div>`;
    days.forEach(d=> {
      let e=meetings.find(m=>m.date===localIso(d)&&+m.time.slice(0, 2)===hr); h+=`<div>${e?`<div class="week-event" style="background:${e.color}">${
        e.title
      }
      <br>${
        fmtTime(e.time)
      }
      </div>`:''}</div>`
    })
  }
  $('#calendar').innerHTML=h+'</div></div>'
}
function renderDay() {
  $('#calTitle').textContent=appState.date.toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric'
  });
  let iso=localIso(appState.date), h='';
  for(let hr=8; hr<=18; hr++) {
    let e=meetings.find(m=>m.date===iso&&+m.time.slice(0, 2)===hr);
    h+=`<div class="day-slot"><div>${fmtTime(String(hr).padStart(2,'0')+':00')}</div><div>${e?`<div class="day-event" style="border-color:${e.color}"><b>${
      e.title
    }
    </b><br><small>${
      e.duration
    }
    min - ${
      e.participants.length
    }
    participants</small></div>`:''}</div></div>`
  }
  $('#calendar').innerHTML=h
}
function renderPeople() {
  let q=$('#peopleSearch').value.toLowerCase(), f=$('#statusFilter').value, list=people.filter(p=>(p.name+' '+p.role+' '+p.email).toLowerCase().includes(q)&&(f==='all'||p.status===f));
  $('#peopleCount').textContent=list.length;
  $('#peopleCards').innerHTML=list.map(p=>`<div class="col-sm-6 col-xl-4 col-xxl-3"><div class="person-card"><div class="person-top"><span class="status" style="color:${p.status==='Available'?'var(--green)':p.status==='Busy'?'var(--red)':'var(--orange)'}"><i class="dot ${p.status==='Available'?'green':p.status==='Busy'?'red':'orange'}"></i> ${p.status}</span></div><h3>${p.name}</h3><small>${p.role}</small><a href="mailto:${p.email}"><i class="bi bi-envelope"></i> ${p.email}</a><div class="person-foot"><span><i class="bi bi-globe2"></i> ${p.tz}</span><button class="btn btn-soft" onclick="scheduleWith(${p.id})">Schedule</button></div></div></div>`).join('')||'<div class="panel text-center muted">No participants match your search.</div>'
}
window.scheduleWith=id=> {
  appState.selected=[id];
  renderSelected();
  go('schedule');
  preview()
};
function addPerson(e) {
  e.preventDefault();
  let id=people.length?Math.max(...people.map(p=>p.id))+1: 1;
  people.push( {
    id, name: $('#newName').value, email: $('#newEmail').value, role: $('#newRole').value, status: 'Pending Response', tz: Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC'
  });
  savePeople();
  bootstrap.Modal.getInstance($('#personModal')).hide();
  e.target.reset();
  renderPeople();
  renderPicker();
  toast('Participant added', 'The new participant is ready to schedule.')
}
function renderNotifications() {
  let list=appState.filter==='all'?notifications: notifications.filter(n=>n.type===appState.filter);
  $('#notificationList').innerHTML=list.map(n=>`<div class="notification ${n.unread?'unread':''}"><i class="notif-icon ${n.type} bi ${n.type==='reminder'?'bi-alarm':n.type==='invitation'?'bi-envelope-check':'bi-calendar-x'}"></i><div><h3>${n.title}</h3><p>${n.message}</p><small>${n.time}</small></div><button onclick="deleteNotif(${n.id})"><i class="bi bi-trash"></i></button></div>`).join('')||'<div class="panel text-center muted">No notifications in this category.</div>';
  let c=notifications.filter(n=>n.unread).length;
  $('#navCount').textContent=c;
  $('.alert-dot').style.display=c?'block': 'none'
}
window.deleteNotif=id=> {
  notifications=notifications.filter(n=>n.id!==id);
  saveNotif();
  renderNotifications();
  toast('Notification removed', 'The notification was deleted.')
};
function renderAnalyticsStats() {
  let totalMinutes=meetings.reduce((sum, m)=>sum+(Number(m.duration)||0), 0);
  let hours=(totalMinutes/60).toFixed(1);
  let accepted=meetings.filter(m=>m.status==='Accepted').length;
  let attendanceRate=meetings.length?Math.round((accepted/meetings.length)*100):0;
  let d=[[`${hours}h`, 'Meeting hours', 'bi-clock', '#6d5dfc'], [`${attendanceRate}%`, 'Accepted rate', 'bi-person-check', '#31b77a'], [meetings.length, 'Total meetings', 'bi-calendar3', '#20aeb2'], [people.length, 'People tracked', 'bi-people', '#e69a19']];
  $('#analyticsStats').innerHTML=d.map(x=>`<div class="col-sm-6 col-xl-3"><div class="panel analytic"><i class="stat-icon bi ${x[2]}" style="color:${x[3]};background:${x[3]}18"></i><div><h3>${x[0]}</h3><p>${x[1]}</p></div></div></div>`).join('')
}
function chartOpts() {
  let dark=document.documentElement.dataset.bsTheme==='dark', text=dark?'#9ea8bd': '#788299', grid=dark?'#30364a': '#e8eaf1';
  return {
    responsive: true, maintainAspectRatio: false, plugins: {
      legend: {
        display: false
      }
    }, scales: {
      x: {
        grid: {
          display: false
        }, ticks: {
          color: text
        }, border: {
          display: false
        }
      }, y: {
        grid: {
          color: grid
        }, ticks: {
          color: text
        }, border: {
          display: false
        }
      }
    }
  }
}
// Buckets each meeting's duration (in hours) into its weekday, using real
// meeting dates rather than a fabricated weekly curve.
function hoursByWeekday() {
  let totals=[0, 0, 0, 0, 0, 0, 0];
  meetings.forEach(m=> {
    if(!m.date)return;
    let day=new Date(m.date+'T12:00').getDay();
    totals[day]+=(Number(m.duration)||0)/60
  });
  // Reorder Sun..Sat -> Mon..Sun to match the dashboard label order.
  return [1, 2, 3, 4, 5, 6, 0].map(i=>+totals[i].toFixed(1))
}
function dashChart() {
  if(typeof Chart==='undefined')return;
  if(appState.charts.dash) {
    appState.charts.dash.data.datasets[0].data=hoursByWeekday();
    appState.charts.dash.update();
    return
  }
  appState.charts.dash=new Chart($('#dashChart'), {
    type: 'bar', data: {
      labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], datasets: [ {
        data: hoursByWeekday(), backgroundColor: '#6d5dfc', borderRadius: 7, barThickness: 22
      }]
    }, options: chartOpts()
  })
}
// Last 6 calendar months of real meeting counts (total vs accepted),
// computed from actual meeting dates instead of a fabricated trend line.
function meetingsByMonth() {
  let now=new Date(), labels=[], totals=[], completed=[];
  for(let i=5; i>=0; i--) {
    let d=new Date(now.getFullYear(), now.getMonth()-i, 1);
    let y=d.getFullYear(), mo=d.getMonth();
    labels.push(d.toLocaleDateString('en-US', {
      month: 'short'
    }));
    let inMonth=meetings.filter(m=> {
      if(!m.date)return false;
      let md=new Date(m.date+'T12:00');
      return md.getFullYear()===y&&md.getMonth()===mo
    });
    totals.push(inMonth.length);
    completed.push(inMonth.filter(m=>m.status==='Accepted').length)
  }
  return {
    labels, totals, completed
  }
}
function analyticsCharts() {
  if(typeof Chart==='undefined')return;
  let o=chartOpts();
  let mb=meetingsByMonth();
  let accepted=meetings.filter(m=>m.status==='Accepted').length;
  let other=meetings.length-accepted;
  let priorityCounts=['High', 'Medium', 'Low'].map(p=>meetings.filter(m=>m.priority===p).length);

  if(appState.charts.meetings)appState.charts.meetings.destroy();
  appState.charts.meetings=new Chart($('#meetingsChart'), {
    type: 'line', data: {
      labels: mb.labels, datasets: [ {
        label: 'Total', data: mb.totals, borderColor: '#6d5dfc', backgroundColor: 'rgba(109,93,252,.12)', fill: true, tension: .42
      }, {
        label: 'Accepted', data: mb.completed, borderColor: '#20c7c7', tension: .42
      }]
    }, options: {
      ...o, plugins: {
        legend: {
          display: true, labels: {
            color: '#788299', usePointStyle: true
          }
        }
      }
    }
  });
  if(appState.charts.att)appState.charts.att.destroy();
  appState.charts.att=new Chart($('#attendanceChart'), {
    type: 'doughnut', data: {
      labels: ['Accepted', 'Other'], datasets: [ {
        data: meetings.length?[accepted, other]: [0, 1], backgroundColor: meetings.length?['#6d5dfc', '#e8eaf1']: ['#e8eaf1', '#e8eaf1'], borderWidth: 0, cutout: '76%'
      }]
    }, options: {
      responsive: true, maintainAspectRatio: false
    }
  });
  if(appState.charts.conf)appState.charts.conf.destroy();
  appState.charts.conf=new Chart($('#conflictChart'), {
    type: 'bar', data: {
      labels: ['High', 'Medium', 'Low'], datasets: [ {
        label: 'Meetings by priority', data: priorityCounts, backgroundColor: ['#ef6372', '#f5a623', '#20c7c7'], borderRadius: 7
      }]
    }, options: o
  });
  if(appState.charts.team)appState.charts.team.destroy();
  appState.charts.team=new Chart($('#teamChart'), {
    type: 'radar', data: {
      labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'], datasets: [ {
        label: 'Meeting hours', data: hoursByWeekday().slice(0, 5), borderColor: '#6d5dfc', backgroundColor: 'rgba(109,93,252,.16)'
      }]
    }, options: {
      responsive: true, maintainAspectRatio: false, plugins: {
        legend: {
          display: false
        }
      }
    }
  })
}
function toast(title, text) {
  $('#toastTitle').textContent=title;
  $('#toastText').textContent=text;
  bootstrap.Toast.getOrCreateInstance($('#toast'), {
    delay: 3000
  }).show()
}
function saveMeetings() {
  localStorage.setItem('mm_meetings', JSON.stringify(meetings))
}
function saveNotif() {
  localStorage.setItem('mm_notifications', JSON.stringify(notifications))
}
function fmtTime(t) {
  if(!t)return'Not set';
  let[h, m]=t.split(':').map(Number);
  return`${h%12||12}:${String(m).padStart(2,'0')} ${h>=12?'PM':'AM'}`
}
function fmtDate(d) {
  return d?new Date(d+'T12:00').toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric'
  }): 'Date not set'
}
function localIso(d) {
  return`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
}
function safe(v) {
  return v.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot; ',"'":'&#039;'}[c]))}

// ---------------------------------------------------------------------------
// AI chat assistant - shows free slots, resolves conflicts, creates meetings
// from natural language. Two surfaces (the floating bubble and the Smart
// Scheduler page) each keep their own short conversation, both backed by the
// same /api/ai/chat endpoint and the same real Google Calendar data.
// ---------------------------------------------------------------------------
const chatThreads= {
  widget: {
    history: [], messagesEl: null, greeted: false
  }, page: {
    history: [], messagesEl: null, greeted: false
  }
};
// ---------------------------------------------------------------------------
// Email tag input — lets users type any email directly into the schedule form
// to invite participants who aren't in the local people list.
// ---------------------------------------------------------------------------
let _tagEmails = [];

function bindEmailTagInput() {
  const input = $('#emailTagInput');
  const wrap = $('#emailTagWrap');
  if (!input || !wrap) return;

  function addTag(raw) {
    const email = raw.trim().replace(/,+$/, '').toLowerCase();
    if (!email) return;
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      wrap.classList.add('shake');
      setTimeout(() => wrap.classList.remove('shake'), 400);
      return;
    }
    if (_tagEmails.includes(email)) return;
    _tagEmails.push(email);
    renderTags();
    input.value = '';
  }

  function renderTags() {
    const container = $('#emailTags');
    if (!container) return;
    container.innerHTML = _tagEmails.map(e =>
      `<span class="email-tag">${safe(e)}<button type="button" onclick="removeTag('${safe(e)}')" aria-label="Remove ${safe(e)}">&times;</button></span>`
    ).join('');
  }

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addTag(input.value);
    }
    if (e.key === 'Backspace' && !input.value && _tagEmails.length) {
      _tagEmails.pop();
      renderTags();
    }
  });
  input.addEventListener('blur', () => { if (input.value.trim()) addTag(input.value); });
  wrap.addEventListener('click', () => input.focus());
}

window.removeTag = email => {
  _tagEmails = _tagEmails.filter(e => e !== email);
  const container = $('#emailTags');
  if (container) container.innerHTML = _tagEmails.map(e =>
    `<span class="email-tag">${safe(e)}<button type="button" onclick="removeTag('${safe(e)}')" aria-label="Remove ${safe(e)}">&times;</button></span>`
  ).join('');
};

function getTagEmails() { return [..._tagEmails]; }

function clearTagEmails() {
  _tagEmails = [];
  const c = $('#emailTags'); if (c) c.innerHTML = '';
  const i = $('#emailTagInput'); if (i) i.value = '';
}

const CHAT_GREETING="Hi! I can show your free slots, check a specific time for conflicts, or book a meeting straight onto your calendar. What do you need?";

// ---------------------------------------------------------------------------
// Smart Scheduler tab switching
// ---------------------------------------------------------------------------
function bindSmartTabs() {
  $$('.smart-tab').forEach(btn => {
    btn.onclick = () => {
      $$('.smart-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      $('#smartTabChat').classList.toggle('d-none', tab !== 'chat');
      $('#smartTabOptimizer').classList.toggle('d-none', tab !== 'optimizer');
    };
  });
  $('#runOptimizerBtn')?.addEventListener('click', runOptimizer);
}

// ---------------------------------------------------------------------------
// Meeting Optimizer
// ---------------------------------------------------------------------------
async function runOptimizer() {
  if (!useBackend) {
    $('#optimizerResult').innerHTML = '<p class="muted small p-3">Sign in with Google to analyze your calendar.</p>';
    return;
  }

  const btn = $('#runOptimizerBtn');
  btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Analyzing...';
  btn.disabled = true;
  $('#optimizerResult').innerHTML = '<div class="optimizer-loading"><span class="spinner-border spinner-border-sm"></span> Fetching your calendar and generating suggestions...</div>';

  try {
    const data = await apiFetch('/api/ai/optimize', {
      method: 'POST',
      body: JSON.stringify({ timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' })
    });
    renderOptimizerResult(data);
  } catch (err) {
    $('#optimizerResult').innerHTML = `<div class="optimizer-error"><i class="bi bi-exclamation-circle"></i> Could not analyze calendar: ${safe(err.message)}</div>`;
  } finally {
    btn.innerHTML = '<i class="bi bi-stars"></i> Re-analyze';
    btn.disabled = false;
  }
}

function renderOptimizerResult(data) {
  const analysis = data.analysis || {};
  const suggestions = data.suggestions || [];
  const gain = data.overall_productivity_gain || 0;
  const summary = data.summary || '';
  const days = data.day_summaries || {};

  // Day load bars
  const sortedDays = Object.entries(days).sort(([a], [b]) => a.localeCompare(b));
  const maxMinutes = Math.max(...sortedDays.map(([, d]) => d.total_minutes), 1);

  const dayBarsHtml = sortedDays.map(([date, d]) => {
    const pct = Math.round((d.total_minutes / maxMinutes) * 100);
    const hours = (d.total_minutes / 60).toFixed(1);
    const isOver = d.total_minutes > 240 || d.count > 5;
    const isLight = d.total_minutes < 120;
    const barClass = isOver ? 'overloaded' : isLight ? 'light' : '';
    const label = new Date(date + 'T12:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    return `<div class="opt-day-row">
      <span class="opt-day-label">${label}</span>
      <div class="opt-day-bar-wrap">
        <div class="opt-day-bar ${barClass}" style="width:${pct}%"></div>
      </div>
      <span class="opt-day-stat">${d.count} meetings · ${hours}h</span>
    </div>`;
  }).join('');

  // Suggestions
  const suggestionsHtml = suggestions.length
    ? suggestions.map(s => {
        const fromLabel = s.from_date ? new Date(s.from_date + 'T12:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) : '';
        const toLabel = s.to_date ? new Date(s.to_date + 'T12:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) : '';
        const canApply = s.type === 'move' && s.event_id && s.to_date;
        return `<div class="opt-suggestion" id="opt-${s.id}">
          <div class="opt-suggestion-head">
            <div>
              <b>${safe(s.title)}</b>
              <p>${safe(s.explanation)}</p>
              ${s.meeting_title ? `<small class="opt-meeting-name"><i class="bi bi-calendar3"></i> ${safe(s.meeting_title)}</small>` : ''}
              ${fromLabel && toLabel ? `<small class="opt-move-arrow"><i class="bi bi-arrow-right"></i> ${fromLabel} → ${toLabel}</small>` : ''}
            </div>
            <div class="opt-gain-badge">+${s.productivity_gain}%</div>
          </div>
          ${canApply ? `<button class="btn btn-soft btn-sm opt-apply-btn" onclick="applyOptimization('${s.id}','${s.event_id}','${s.to_date}')">
            <i class="bi bi-check2"></i> Apply suggestion
          </button>` : ''}
        </div>`;
      }).join('')
    : '<p class="muted small">No suggestions — your schedule looks well balanced!</p>';

  $('#optimizerResult').innerHTML = `
    <div class="opt-summary-card">
      <div class="opt-summary-left">
        <div class="opt-gain-ring">
          <b>+${gain}%</b>
          <small>estimated gain</small>
        </div>
      </div>
      <div>
        <h4>Schedule analysis</h4>
        <p>${safe(summary)}</p>
        <div class="opt-stats">
          <span><i class="bi bi-calendar3"></i> ${analysis.total_meeting_hours || 0}h total</span>
          <span><i class="bi bi-graph-up-arrow"></i> ${analysis.avg_meetings_per_day || 0} meetings/day avg</span>
          ${analysis.overloaded_days?.length ? `<span class="text-danger"><i class="bi bi-exclamation-triangle"></i> ${analysis.overloaded_days.length} overloaded day${analysis.overloaded_days.length > 1 ? 's' : ''}</span>` : ''}
        </div>
      </div>
    </div>
    <div class="opt-section">
      <h4>Meeting load — next 14 days</h4>
      <div class="opt-day-bars">${dayBarsHtml || '<p class="muted small">No meetings found.</p>'}</div>
    </div>
    <div class="opt-section">
      <h4>AI suggestions <span class="chip">${suggestions.length} suggestions</span></h4>
      <div class="opt-suggestions">${suggestionsHtml}</div>
    </div>`;
}

window.applyOptimization = async (suggestionId, eventId, toDate) => {
  const btn = document.querySelector(`#opt-${suggestionId} .opt-apply-btn`);
  if (btn) { btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Applying...'; btn.disabled = true; }

  try {
    // Calculate a sensible start time — default to 10am on the target date
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    await apiFetch(`/api/meetings/reschedule-event`, {
      method: 'POST',
      body: JSON.stringify({ event_id: eventId, to_date: toDate, timezone })
    });
    if (btn) btn.closest('.opt-suggestion').innerHTML = '<p class="opt-applied"><i class="bi bi-check-circle-fill text-success"></i> Applied — meeting moved on Google Calendar.</p>';
    toast('Meeting moved', 'Google Calendar has been updated.');
    await loadMeetingsFromBackend();
    renderAll();
  } catch (err) {
    if (btn) { btn.innerHTML = '<i class="bi bi-check2"></i> Apply suggestion'; btn.disabled = false; }
    toast('Could not apply', err.message);
  }
};

function bindChatAssistant() {
  chatThreads.widget.messagesEl=$('#chatWidgetMessages');
  chatThreads.page.messagesEl=$('#smartChatMessages');

  $('#chatLauncher').onclick=()=> {
    let widget=$('#chatWidget');
    let opening=widget.classList.contains('d-none');
    widget.classList.toggle('d-none');
    if(opening&&!chatThreads.widget.greeted) {
      appendChatMessage('widget', 'ai', CHAT_GREETING);
      chatThreads.widget.greeted=true
    }
    if(opening)setTimeout(()=>$('#chatWidgetInput').focus(), 50)
  };
  $('#chatWidgetClose').onclick=()=>$('#chatWidget').classList.add('d-none');
  $('#chatWidgetForm').onsubmit=e=> {
    e.preventDefault();
    let input=$('#chatWidgetInput'), text=input.value.trim();
    if(!text)return;
    input.value='';
    sendChatMessage('widget', text)
  };

  $('#smartChatForm').onsubmit=e=> {
    e.preventDefault();
    let input=$('#smartChatInput'), text=input.value.trim();
    if(!text)return;
    input.value='';
    sendChatMessage('page', text)
  };
  $$('#smartPage [data-suggest]').forEach(b=>b.onclick=()=> {
    sendChatMessage('page', b.dataset.suggest)
  })
}

// Ensures the Smart Scheduler page shows its greeting the first time it's
// visited, even if the user never opens the floating bubble.
function ensurePageChatGreeted() {
  if(!chatThreads.page.greeted&&chatThreads.page.messagesEl) {
    appendChatMessage('page', 'ai', CHAT_GREETING);
    chatThreads.page.greeted=true
  }
}

function appendChatMessage(thread, role, text, extraHtml='') {
  let t=chatThreads[thread];
  if(!t||!t.messagesEl)return;
  let bubble=document.createElement('div');
  bubble.className='chat-msg '+(role==='user'?'chat-msg-user': 'chat-msg-ai');
  bubble.innerHTML=`<div class="chat-bubble">${safe(text).replace(/\n/g,'<br>')}${extraHtml}</div>`;
  t.messagesEl.appendChild(bubble);
  t.messagesEl.scrollTop=t.messagesEl.scrollHeight;
  return bubble
}

function appendChatTyping(thread) {
  let t=chatThreads[thread];
  if(!t||!t.messagesEl)return null;
  let bubble=document.createElement('div');
  bubble.className='chat-msg chat-msg-ai';
  bubble.innerHTML='<div class="chat-bubble chat-typing"><span></span><span></span><span></span></div>';
  t.messagesEl.appendChild(bubble);
  t.messagesEl.scrollTop=t.messagesEl.scrollHeight;
  return bubble
}

function renderSlotChips(thread, slots) {
  if(!slots||!slots.length)return;
  let html=slots.map(s=> {
    let label=new Date(s.date+'T12:00').toLocaleDateString('en-US', {
      weekday: 'short', month: 'short', day: 'numeric'
    })+' at '+fmtTime(s.time);
    return `<button type="button" class="chat-slot-chip" data-date="${s.date}" data-time="${s.time}">${label}</button>`
  }).join('');
  let wrap=document.createElement('div');
  wrap.className='chat-slot-list';
  wrap.innerHTML=html;
  chatThreads[thread].messagesEl.appendChild(wrap);
  chatThreads[thread].messagesEl.scrollTop=chatThreads[thread].messagesEl.scrollHeight;
  wrap.querySelectorAll('.chat-slot-chip').forEach(chip=>chip.onclick=()=> {
    go('schedule');
    $('#meetingDate').value=chip.dataset.date;
    $('#meetingTime').value=chip.dataset.time;
    preview();
    toast('Time applied', 'That slot is ready in your meeting form.')
  })
}

async function sendChatMessage(thread, text) {
  let t=chatThreads[thread];
  appendChatMessage(thread, 'user', text);
  t.history.push( {
    role: 'user', content: text
  });
  let typingBubble=appendChatTyping(thread);

  if(!useBackend) {
    typingBubble&&typingBubble.remove();
    appendChatMessage(thread, 'ai', "You'll need to sign in with Google before I can check your calendar or book anything.");
    return
  }

  let payload= {
    message: text, history: t.history.slice(-8), reference_date: todayIso(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC', participants_context: peopleContext()
  };

  let data;
  try {
    data=await apiFetch('/api/ai/chat', {
      method: 'POST', body: JSON.stringify(payload)
    })
  } catch(err) {
    typingBubble&&typingBubble.remove();
    appendChatMessage(thread, 'ai', "Sorry, I couldn't reach the scheduling service: "+err.message);
    return
  }

  typingBubble&&typingBubble.remove();
  let reply=data.reply||"Done.";
  appendChatMessage(thread, 'ai', reply);
  t.history.push( {
    role: 'assistant', content: reply
  });

  let d=data.data||{};
  if(data.intent==='show_free_slots'&&d.free_slots) {
    renderSlotChips(thread, d.free_slots)
  }
  if(data.intent==='resolve_conflict'&&d.availability&&d.availability.conflict&&d.availability.alternatives) {
    renderSlotChips(thread, d.availability.alternatives);
    showConflictDialog({
      title: 'Conflict found',
      message: 'That time is busy. Pick one of these free slots to use in the schedule form.',
      alternatives: d.availability.alternatives,
      allowForce: false,
      onPick: slot => {
        go('schedule');
        $('#meetingDate').value = slot.date;
        $('#meetingTime').value = slot.time;
        preview();
        toast('Free slot applied', 'That slot is ready in your meeting form.');
      }
    })
  }
  if(data.intent==='create_meeting'&&d.meeting) {
    meetings.unshift(normalizeMeeting(d.meeting));
    renderStats();
    renderMeetings();
    renderInvites();
    renderCalendar();
    checkMeetingReminders(false);
    if(d.meeting.status === 'pending') appendChatMessage(thread, 'ai', (d.meeting.email_status&&d.meeting.email_status.sent) ? 'Email invite sent. This meeting will stay pending until all participants accept.' : 'Meeting created, but email could not be sent from this machine.');
  }
  if(data.intent==='create_meeting'&&d.error&&d.alternatives) {
    renderSlotChips(thread, d.alternatives);
    showAiConflictDialog(thread, text, d);
    renderForceBookButton(thread, text)
  }
  // Necessity warning — meeting could be an email or async
  if(data.intent==='necessity_warning') {
    renderNecessityWarning(thread, d);
  }
  // Overload warning — too many meetings on that day
  if(data.intent==='overload_warning') {
    if(d.alternatives&&d.alternatives.length) renderSlotChips(thread, d.alternatives);
    renderForceBookButton(thread, d.original_message || text);
  }
  // Participant picker — AI needs to know who to invite
  if(data.intent==='needs_participants') {
    renderParticipantPicker(thread, d.parsed || {}, text);
  }
}

async function forceBookOriginalFromChat(thread, originalText) {
  let t=chatThreads[thread];
  appendChatMessage(thread, 'user', 'Book it anyway');
  let typingBubble=appendChatTyping(thread);
  try {
    let data=await apiFetch('/api/ai/chat', {
      method: 'POST', body: JSON.stringify( {
        message: originalText, history: t.history.slice(-8), reference_date: todayIso(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC', participants_context: peopleContext(), force: true
      })
    });
    typingBubble&&typingBubble.remove();
    appendChatMessage(thread, 'ai', data.reply||'Done.');
    if(data.data&&data.data.meeting) {
      meetings.unshift(normalizeMeeting(data.data.meeting));
      renderStats(); renderMeetings(); renderInvites(); renderCalendar(); checkMeetingReminders(false)
    }
  } catch(err) {
    typingBubble&&typingBubble.remove();
    appendChatMessage(thread, 'ai', "Couldn't book that: "+err.message)
  }
}

function showAiConflictDialog(thread, originalText, data) {
  const parsed = data.parsed || {};
  showConflictDialog({
    title: 'Meeting time conflict',
    message: 'The requested time is busy. Choose a suggested free slot, or book the original time anyway.',
    alternatives: data.alternatives || [],
    allowForce: true,
    onPick: slot => {
      const nextRequest = `${originalText}. Use ${slot.date} at ${slot.time} instead.`;
      sendChatMessage(thread, nextRequest);
    },
    onForce: () => forceBookOriginalFromChat(thread, originalText)
  });
}
function renderForceBookButton(thread, originalText) {
  let t=chatThreads[thread];
  let btn=document.createElement('button');
  btn.type='button';
  btn.className='chat-force-btn';
  btn.textContent='Book the original time anyway';
  t.messagesEl.appendChild(btn);
  t.messagesEl.scrollTop=t.messagesEl.scrollHeight;
  btn.onclick=async ()=> {
    btn.disabled=true;
    btn.textContent='Booking...';
    await forceBookOriginalFromChat(thread, originalText);
    btn.remove()
  }
}

function renderParticipantPicker(thread, parsed, originalText) {
  const t = chatThreads[thread];
  if (!t || !t.messagesEl) return;

  const contacts = peopleContext();
  const card = document.createElement('div');
  card.className = 'chat-participant-picker';

  const contactOptions = contacts.map(c =>
    `<label class="cpp-option">
      <input type="checkbox" class="cpp-check" value="${safe(c.email)}" data-name="${safe(c.name)}">
      <span class="cpp-avatar">${safe(c.name.charAt(0).toUpperCase())}</span>
      <span class="cpp-info">
        <b>${safe(c.name)}</b>
        <small>${safe(c.email)}</small>
      </span>
    </label>`
  ).join('');

  card.innerHTML = `
    <div class="cpp-head">
      <i class="bi bi-people"></i>
      <b>Select participants</b>
    </div>
    ${contacts.length ? `
      <div class="cpp-search-wrap">
        <i class="bi bi-search"></i>
        <input type="text" class="cpp-search" placeholder="Search contacts...">
      </div>
      <div class="cpp-list">${contactOptions}</div>` : ''}
    <div class="cpp-email-row">
      <input type="email" class="cpp-email-input form-control form-control-sm"
        placeholder="Or type an email address and press Enter">
    </div>
    <div class="cpp-tags"></div>
    <button type="button" class="btn btn-primary btn-sm cpp-confirm-btn" disabled>
      <i class="bi bi-calendar-check"></i> Book with selected participants
    </button>`;

  t.messagesEl.appendChild(card);
  t.messagesEl.scrollTop = t.messagesEl.scrollHeight;

  const searchInput = card.querySelector('.cpp-search');
  const list = card.querySelector('.cpp-list');
  const emailInput = card.querySelector('.cpp-email-input');
  const tagsEl = card.querySelector('.cpp-tags');
  const confirmBtn = card.querySelector('.cpp-confirm-btn');

  let manualEmails = [];

  function refreshTags() {
    tagsEl.innerHTML = manualEmails.map(e =>
      `<span class="email-tag">${safe(e)}<button type="button" onclick="this.closest('.email-tag').remove()" data-email="${safe(e)}">&times;</button></span>`
    ).join('');
    // Wire remove buttons
    tagsEl.querySelectorAll('.email-tag button').forEach(btn => {
      btn.onclick = () => {
        manualEmails = manualEmails.filter(e => e !== btn.dataset.email);
        refreshTags();
        updateConfirm();
      };
    });
    updateConfirm();
  }

  function getCheckedEmails() {
    return [...card.querySelectorAll('.cpp-check:checked')].map(c => c.value);
  }

  function updateConfirm() {
    const total = getCheckedEmails().length + manualEmails.length;
    confirmBtn.disabled = total === 0;
    confirmBtn.innerHTML = total
      ? `<i class="bi bi-calendar-check"></i> Book with ${total} participant${total > 1 ? 's' : ''}`
      : '<i class="bi bi-calendar-check"></i> Book with selected participants';
  }

  // Search filter
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      const q = searchInput.value.toLowerCase();
      card.querySelectorAll('.cpp-option').forEach(opt => {
        const name = opt.querySelector('b').textContent.toLowerCase();
        const email = opt.querySelector('small').textContent.toLowerCase();
        opt.style.display = (!q || name.includes(q) || email.includes(q)) ? '' : 'none';
      });
    });
  }

  // Checkbox changes
  card.addEventListener('change', e => {
    if (e.target.classList.contains('cpp-check')) updateConfirm();
  });

  // Manual email input
  emailInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const email = emailInput.value.trim().replace(/,+$/, '').toLowerCase();
      if (email && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) && !manualEmails.includes(email)) {
        manualEmails.push(email);
        refreshTags();
      } else if (email) {
        emailInput.classList.add('is-invalid');
        setTimeout(() => emailInput.classList.remove('is-invalid'), 1000);
      }
      emailInput.value = '';
    }
  });
  emailInput.addEventListener('blur', () => {
    const email = emailInput.value.trim().toLowerCase();
    if (email && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) && !manualEmails.includes(email)) {
      manualEmails.push(email);
      refreshTags();
      emailInput.value = '';
    }
  });

  // Confirm — re-send the original message with selected participants
  confirmBtn.onclick = async () => {
    const allEmails = [...new Set([...getCheckedEmails(), ...manualEmails])];
    if (!allEmails.length) return;

    confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Booking...';
    confirmBtn.disabled = true;

    // Build a new message that includes the emails explicitly so the backend
    // can extract them from the message text
    const emailList = allEmails.join(', ');
    const newMessage = originalText
      ? `${originalText} with ${emailList}`
      : `Book this meeting with ${emailList}`;

    card.style.opacity = '0.5';
    card.style.pointerEvents = 'none';
    await sendChatMessage(thread, newMessage);
    card.remove();
  };
}

function renderNecessityWarning(thread, data) {
  const t = chatThreads[thread];
  if (!t || !t.messagesEl) return;

  const verdict = data.verdict || 'email_instead';
  const originalMsg = data.original_message || '';
  const parsed = data.parsed || {};

  const card = document.createElement('div');
  card.className = 'necessity-warning-card';

  const iconMap = { email_instead: 'bi-envelope', async_instead: 'bi-camera-video' };
  const labelMap = { email_instead: 'Consider an email instead', async_instead: 'Consider async instead' };
  const icon = iconMap[verdict] || 'bi-lightbulb';
  const label = labelMap[verdict] || 'Heads up';

  card.innerHTML = `
    <div class="necessity-warning-head">
      <i class="bi ${icon}"></i>
      <b>${label}</b>
    </div>
    ${data.suggestion ? `<p class="necessity-suggestion">${safe(data.suggestion)}</p>` : ''}
    <div class="necessity-actions">
      <button type="button" class="btn btn-soft btn-sm necessity-skip-btn">
        <i class="bi bi-envelope"></i> Send email instead
      </button>
      <button type="button" class="btn btn-primary btn-sm necessity-book-btn">
        <i class="bi bi-calendar-check"></i> Book anyway
      </button>
    </div>
    <div class="necessity-compose d-none">
      <div class="necessity-compose-head">
        <span><i class="bi bi-stars"></i> AI-drafted email</span>
        <small class="muted">Edit before sending</small>
      </div>
      <div class="necessity-compose-loading">
        <span class="spinner-border spinner-border-sm"></span> Drafting email...
      </div>
      <div class="necessity-compose-fields d-none">
        <div class="mb-2">
          <label class="form-label necessity-label">To</label>
          <input type="text" class="form-control form-control-sm necessity-to" placeholder="recipient@example.com">
        </div>
        <div class="mb-2">
          <label class="form-label necessity-label">Subject</label>
          <input type="text" class="form-control form-control-sm necessity-subject">
        </div>
        <div class="mb-2">
          <label class="form-label necessity-label">Message</label>
          <textarea class="form-control form-control-sm necessity-body" rows="6"></textarea>
        </div>
        <div class="necessity-send-row">
          <button type="button" class="btn btn-primary btn-sm necessity-send-btn">
            <i class="bi bi-send"></i> Send email
          </button>
          <button type="button" class="btn btn-soft btn-sm necessity-cancel-compose">
            Cancel
          </button>
        </div>
      </div>
    </div>`;

  t.messagesEl.appendChild(card);
  t.messagesEl.scrollTop = t.messagesEl.scrollHeight;

  // "Send email instead" — show compose area and generate draft
  card.querySelector('.necessity-skip-btn').onclick = async () => {
    const compose = card.querySelector('.necessity-compose');
    const loading = card.querySelector('.necessity-compose-loading');
    const fields = card.querySelector('.necessity-compose-fields');
    compose.classList.remove('d-none');
    card.querySelector('.necessity-actions').style.display = 'none';
    t.messagesEl.scrollTop = t.messagesEl.scrollHeight;

    // Pre-fill recipients from parsed intent if available
    const participants = (parsed.participants || []).join(', ');
    card.querySelector('.necessity-to').value = participants;

    // Call AI to generate draft
    try {
      const draftData = await apiFetch('/api/ai/draft-email', {
        method: 'POST',
        body: JSON.stringify({
          meeting: {
            title: parsed.title || '',
            date: parsed.date || '',
            start_time: parsed.time || '',
            duration_minutes: parsed.duration_minutes || 30,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
            description: parsed.description || '',
          }
        })
      });
      card.querySelector('.necessity-subject').value = draftData.subject || '';
      card.querySelector('.necessity-body').value = draftData.body || '';
    } catch (_) {
      card.querySelector('.necessity-subject').value = `Re: ${parsed.title || 'Update'}`;
      card.querySelector('.necessity-body').value = `Hi,\n\nJust a quick update regarding ${parsed.title || 'our topic'}.\n\nBest regards`;
    }

    loading.classList.add('d-none');
    fields.classList.remove('d-none');
    card.querySelector('.necessity-to').focus();
    t.messagesEl.scrollTop = t.messagesEl.scrollHeight;
  };

  // Cancel compose — go back to action buttons
  card.querySelector('.necessity-cancel-compose').onclick = () => {
    card.querySelector('.necessity-compose').classList.add('d-none');
    card.querySelector('.necessity-actions').style.display = '';
  };

  // Send email
  card.querySelector('.necessity-send-btn').onclick = async () => {
    const sendBtn = card.querySelector('.necessity-send-btn');
    const toVal = card.querySelector('.necessity-to').value.trim();
    const subject = card.querySelector('.necessity-subject').value.trim();
    const body = card.querySelector('.necessity-body').value.trim();

    if (!toVal) {
      card.querySelector('.necessity-to').focus();
      return;
    }
    const recipients = toVal.split(/[,;\s]+/).map(e => e.trim()).filter(e => e.includes('@'));
    if (!recipients.length) {
      card.querySelector('.necessity-to').focus();
      return;
    }

    sendBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Sending...';
    sendBtn.disabled = true;

    try {
      const result = await apiFetch('/api/ai/send-skip-email', {
        method: 'POST',
        body: JSON.stringify({
          meeting: {
            title: parsed.title || '',
            description: parsed.description || '',
          },
          recipients,
          subject,
          body,
        })
      });
      card.innerHTML = `<p class="necessity-resolved">
        <i class="bi bi-check-circle-fill text-success"></i>
        Email sent to ${recipients.join(', ')} — no meeting needed!
      </p>`;
      toast('Email sent', `"${subject}" delivered successfully.`);
    } catch (err) {
      sendBtn.innerHTML = '<i class="bi bi-send"></i> Send email';
      sendBtn.disabled = false;
      appendChatMessage(thread, 'ai', `Could not send email: ${err.message}`);
    }
  };

  // "Book anyway"
  card.querySelector('.necessity-book-btn').onclick = async () => {
    card.querySelector('.necessity-book-btn').disabled = true;
    card.querySelector('.necessity-book-btn').innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    card.querySelector('.necessity-skip-btn').disabled = true;
    await forceBookOriginalFromChat(thread, originalMsg);
    card.remove();
  };
}























// =============================================================================
// SMART TIMETABLE SCHEDULER
// =============================================================================

let ttEntries = [];           // all timetable entries loaded from backend
let ttSelectedParticipants = []; // emails/names chosen for slot-finding
let ttEditId = null;          // entry currently being edited
let ttInitialized = false;    // only bind events once

// ---------------------------------------------------------------------------
// Page init
// ---------------------------------------------------------------------------
function initTimetablePage() {
  if (!ttInitialized) {
    ttBindEvents();
    ttInitialized = true;
  }
  ttUpdateFieldLabels();
  ttLoadOrganizations();
  ttLoadEntries();
}

// Label configs per org type
const TT_FIELD_LABELS = {
  college:    { subject: 'Subject *',       room: 'Room',          task_ph: 'e.g. Data Structures', room_ph: 'e.g. C101' },
  school:     { subject: 'Subject *',       room: 'Classroom',     task_ph: 'e.g. Mathematics',     room_ph: 'e.g. Room 5' },
  university: { subject: 'Course / Module *', room: 'Hall / Lab',  task_ph: 'e.g. Machine Learning', room_ph: 'e.g. Lab B2' },
  company:    { subject: 'Task / Agenda *', room: 'Location / Link', task_ph: 'e.g. Sprint planning', room_ph: 'e.g. Conf Room A or Meet link' },
};

function ttUpdateFieldLabels() {
  const type = $('#ttOrgType').value || 'company';
  const cfg = TT_FIELD_LABELS[type] || TT_FIELD_LABELS.company;

  // Manual form labels
  const subjLabel = $('#ttSubjectLabel');
  const roomLabel = $('#ttRoomLabel');
  const subjInput = $('#ttSubject');
  const roomInput = $('#ttRoom');
  if (subjLabel) subjLabel.textContent = cfg.subject;
  if (roomLabel) roomLabel.textContent = cfg.room;
  if (subjInput) subjInput.placeholder = cfg.task_ph;
  if (roomInput) roomInput.placeholder = cfg.room_ph;

  // Table column headers
  const subjCol = $('#ttSubjectColHeader');
  const roomCol = $('#ttRoomColHeader');
  if (subjCol) subjCol.textContent = cfg.subject.replace(' *', '');
  if (roomCol) roomCol.textContent = cfg.room;

  // Upload hint
  const hint = $('#ttUploadHint');
  if (hint) hint.textContent = `Column for tasks/subjects should be named "${cfg.subject.replace(' *', '')}" or "Subject/Task"`;
}

async function ttLoadOrganizations() {
  if (!useBackend) return;
  try {
    const data = await apiFetch('/api/timetable/organizations');
    const orgs = data.organizations || [];
    const list = $('#ttOrgNameList');
    if (list) {
      list.innerHTML = orgs.map(o => `<option value="${safe(o.name)}">`).join('');
    }
    // If we have orgs saved, auto-select the first one and set its type
    if (orgs.length && !$('#ttOrgName').value) {
      $('#ttOrgName').value = orgs[0].name;
      if (orgs[0].type) $('#ttOrgType').value = orgs[0].type;
      ttUpdateFieldLabels();
    }
  } catch (_) {
    // silently skip — non-critical
  }
}

// ---------------------------------------------------------------------------
// Event bindings (run once)
// ---------------------------------------------------------------------------
function ttBindEvents() {
  // Reload when org type changes — updates field labels
  $('#ttOrgType').addEventListener('change', () => {
    ttUpdateFieldLabels();
  });

  // Reload table when org name changes
  $('#ttOrgName').addEventListener('change', () => {
    ttSelectedParticipants = [];
    ttLoadEntries();
  });
  $('#ttOrgName').addEventListener('blur', () => {
    ttSelectedParticipants = [];
    ttLoadEntries();
  });

  // Toggle manual add form
  $('#ttToggleForm').onclick = () => {
    const form = $('#ttManualForm');
    const hidden = form.classList.contains('d-none');
    form.classList.toggle('d-none', !hidden);
    if (!hidden) ttResetForm();
  };
  $('#ttCancelForm').onclick = () => { $('#ttManualForm').classList.add('d-none'); ttResetForm(); };

  // Save entry
  $('#ttSaveEntry').onclick = ttSaveEntry;

  // File upload
  $('#ttFileInput').addEventListener('change', ttHandleFileUpload);

  // Filters
  $('#ttApplyFilter').onclick = ttLoadEntries;
  $('#ttSearchFilter').addEventListener('keydown', e => { if (e.key === 'Enter') ttLoadEntries(); });

  // Refresh
  $('#ttRefreshBtn').onclick = ttLoadEntries;

  // Delete entire sheet
  if ($('#ttDeleteSheetBtn')) $('#ttDeleteSheetBtn').onclick = ttDeleteSheet;

  // Participant search
  $('#ttParticipantSearch').addEventListener('input', ttRenderParticipantList);

  // Find slot
  $('#ttFindSlot').onclick = ttFindCommonSlot;
}

// ---------------------------------------------------------------------------
// Load entries from backend
// ---------------------------------------------------------------------------
async function ttLoadEntries() {
  if (!useBackend) { ttRenderTable([]); return; }
  try {
    const params = new URLSearchParams();
    const orgName = ($('#ttOrgName').value || '').trim();
    const orgType = $('#ttOrgType').value;
    const day  = $('#ttDayFilter').value;
    const dept = ($('#ttDeptFilter').value || '').trim();
    const name = ($('#ttSearchFilter').value || '').trim();
    if (orgName) params.set('organization_name', orgName);
    else if (orgType) params.set('organization_type', orgType);
    if (day)  params.set('day', day);
    if (dept) params.set('department', dept);
    if (name) params.set('participant', name);
    const data = await apiFetch(`/api/timetable?${params}`);
    ttEntries = data.entries || [];
    ttRenderTable(ttEntries);
    ttRenderParticipantList();
    const orgLabel = orgName || orgType;
    $('#ttEntryCount').textContent = `${ttEntries.length} entr${ttEntries.length === 1 ? 'y' : 'ies'}${orgLabel ? ' — ' + orgLabel : ''}`;
  } catch (err) {
    toast('Timetable error', err.message);
  }
}

// ---------------------------------------------------------------------------
// Render table
// ---------------------------------------------------------------------------
function ttRenderTable(entries) {
  const tbody = $('#ttTableBody');
  if (!entries.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center muted small py-4">No entries found.</td></tr>';
    return;
  }
  tbody.innerHTML = entries.map(e => `
    <tr>
      <td>
        <b class="tt-name">${safe(e.participant_name)}</b>
        ${e.participant_email ? `<small class="d-block muted">${safe(e.participant_email)}</small>` : ''}
      </td>
      <td><span class="tt-day-badge">${safe(e.day)}</span></td>
      <td class="tt-time">${safe(e.start_time.slice(0,5))}–${safe(e.end_time.slice(0,5))}</td>
      <td>${safe(e.subject_or_task)}</td>
      <td class="muted small">${safe(e.department || '—')}</td>
      <td class="muted small">${safe(e.room || '—')}</td>
      <td class="tt-actions">
        <button class="icon-btn" title="Edit" onclick="ttStartEdit('${e.id}')"><i class="bi bi-pencil"></i></button>
        <button class="icon-btn text-danger" title="Delete" onclick="ttDeleteEntry('${e.id}')"><i class="bi bi-trash"></i></button>
      </td>
    </tr>`).join('');
}

// ---------------------------------------------------------------------------
// Participant list (unique names from loaded entries)
// ---------------------------------------------------------------------------
function ttRenderParticipantList() {
  const search = ($('#ttParticipantSearch').value || '').toLowerCase();
  const seen = new Map();
  ttEntries.forEach(e => {
    const key = e.participant_email || e.participant_name;
    if (!seen.has(key)) seen.set(key, { name: e.participant_name, email: e.participant_email, dept: e.department });
  });
  const filtered = [...seen.values()].filter(p =>
    !search || p.name.toLowerCase().includes(search) || (p.email || '').toLowerCase().includes(search) || (p.dept || '').toLowerCase().includes(search)
  );
  const el = $('#ttParticipantList');
  if (!filtered.length) {
    el.innerHTML = '<p class="muted small text-center p-2">No participants in timetable yet.</p>';
    return;
  }
  el.innerHTML = filtered.map(p => {
    const key = p.email || p.name;
    const selected = ttSelectedParticipants.includes(key);
    return `<label class="tt-participant-option ${selected ? 'selected' : ''}">
      <input type="checkbox" class="d-none" ${selected ? 'checked' : ''} onchange="ttToggleParticipant('${safe(key)}', this.checked)">
      <span class="tt-p-avatar">${p.name.charAt(0).toUpperCase()}</span>
      <span class="tt-p-info">
        <b>${safe(p.name)}</b>
        <small>${safe(p.email || p.dept || '')}</small>
      </span>
      <i class="bi ${selected ? 'bi-check-circle-fill' : 'bi-circle'} tt-p-check"></i>
    </label>`;
  }).join('');
  ttRenderSelectedChips();
}

function ttToggleParticipant(key, checked) {
  if (checked && !ttSelectedParticipants.includes(key)) {
    ttSelectedParticipants.push(key);
  } else if (!checked) {
    ttSelectedParticipants = ttSelectedParticipants.filter(k => k !== key);
  }
  ttRenderParticipantList();
}

function ttRenderSelectedChips() {
  const el = $('#ttSelectedParticipants');
  el.innerHTML = ttSelectedParticipants.map(k =>
    `<span class="email-tag">${safe(k)}<button type="button" onclick="ttRemoveParticipant('${safe(k)}')">&times;</button></span>`
  ).join('');
  $('#ttFindSlot').disabled = ttSelectedParticipants.length === 0;
}

window.ttRemoveParticipant = key => {
  ttSelectedParticipants = ttSelectedParticipants.filter(k => k !== key);
  ttRenderParticipantList();
};

// ---------------------------------------------------------------------------
// Save / Edit entry
// ---------------------------------------------------------------------------
async function ttSaveEntry() {
  const btn = $('#ttSaveEntry');
  const payload = {
    participant_name:  $('#ttPName').value.trim(),
    participant_email: $('#ttPEmail').value.trim(),
    department:        $('#ttDept').value.trim(),
    class_or_team:     $('#ttClass').value.trim(),
    organization_type: $('#ttOrgType').value,
    organization_name: ($('#ttOrgName').value || '').trim(),
    day:               $('#ttDay').value,
    start_time:        $('#ttStart').value,
    end_time:          $('#ttEnd').value,
    subject_or_task:   $('#ttSubject').value.trim(),
    room:              $('#ttRoom').value.trim(),
  };
  if (!payload.participant_name || !payload.day || !payload.start_time || !payload.end_time || !payload.subject_or_task) {
    toast('Missing fields', 'Participant name, day, times, and subject are required.'); return;
  }
  btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
  btn.disabled = true;
  try {
    if (ttEditId) {
      await apiFetch(`/api/timetable/update/${ttEditId}`, { method: 'PUT', body: JSON.stringify(payload) });
      toast('Entry updated', payload.participant_name + ' — ' + payload.day);
    } else {
      await apiFetch('/api/timetable/manual', { method: 'POST', body: JSON.stringify(payload) });
      toast('Entry added', payload.participant_name + ' — ' + payload.day);
    }
    ttResetForm();
    $('#ttManualForm').classList.add('d-none');
    ttLoadEntries();
  } catch (err) {
    toast('Error', err.message);
  } finally {
    btn.innerHTML = '<i class="bi bi-check-lg"></i> Save entry';
    btn.disabled = false;
  }
}

window.ttStartEdit = id => {
  const e = ttEntries.find(x => x.id === id);
  if (!e) return;
  ttEditId = id;
  $('#ttPName').value  = e.participant_name;
  $('#ttPEmail').value = e.participant_email || '';
  $('#ttDept').value   = e.department || '';
  $('#ttClass').value  = e.class_or_team || '';
  $('#ttDay').value    = e.day;
  $('#ttStart').value  = e.start_time.slice(0, 5);
  $('#ttEnd').value    = e.end_time.slice(0, 5);
  $('#ttSubject').value = e.subject_or_task;
  $('#ttRoom').value   = e.room || '';
  $('#ttOrgType').value = e.organization_type || 'company';
  $('#ttManualForm').classList.remove('d-none');
  $('#ttManualForm').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  $('#ttSaveEntry').innerHTML = '<i class="bi bi-check-lg"></i> Update entry';
};

function ttResetForm() {
  ttEditId = null;
  ['#ttPName','#ttPEmail','#ttDept','#ttClass','#ttSubject','#ttRoom'].forEach(id => { const el = $(id); if(el) el.value = ''; });
  $('#ttDay').value = '';
  $('#ttStart').value = '';
  $('#ttEnd').value = '';
  $('#ttSaveEntry').innerHTML = '<i class="bi bi-check-lg"></i> Save entry';
}

window.ttDeleteEntry = async id => {
  const e = ttEntries.find(x => x.id === id);
  if (!e) return;
  // Use our custom modal pattern
  const confirmed = await ttConfirmDelete(e.participant_name, e.day, e.subject_or_task);
  if (!confirmed) return;
  try {
    await apiFetch(`/api/timetable/delete/${id}`, { method: 'DELETE' });
    toast('Entry deleted', e.participant_name + ' — ' + e.day);
    ttLoadEntries();
  } catch (err) {
    toast('Error', err.message);
  }
};

async function ttDeleteSheet() {
  const orgName = ($('#ttOrgName').value || '').trim();
  const count = ttEntries.length;
  if (!count) {
    toast('No entries', 'There are no timetable entries to delete.');
    return;
  }
  const label = orgName ? `entire timetable sheet for "${orgName}"` : 'ALL timetable entries';
  const confirmed = await ttConfirmDeleteSheet(label, count);
  if (!confirmed) return;

  if (useBackend) {
    try {
      if (orgName) {
        await apiFetch('/api/timetable/delete-organization', {
          method: 'DELETE',
          body: JSON.stringify({ organization_name: orgName })
        });
      } else {
        await apiFetch('/api/timetable/clear-all', { method: 'DELETE' });
      }
      toast('Sheet deleted', `Deleted ${count} timetable entr${count === 1 ? 'y' : 'ies'}.`);
      $('#ttOrgName').value = '';
      await ttLoadOrganizations();
      await ttLoadEntries();
    } catch (err) {
      toast('Delete failed', err.message);
    }
  } else {
    ttEntries = [];
    ttRenderTable([]);
    $('#ttEntryCount').textContent = '0 entries';
    toast('Sheet deleted', 'All local timetable entries removed.');
  }
}

function ttConfirmDeleteSheet(label, count) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'tt-confirm-overlay';
    overlay.innerHTML = `
      <div class="tt-confirm-box" style="width:360px">
        <div class="delete-modal-icon"><i class="bi bi-trash3"></i></div>
        <h5 class="fw-800 text-center mb-1">Delete entire excel sheet?</h5>
        <p class="muted small text-center mt-2 mb-3">This will permanently delete the ${safe(label)} (${count} ${count === 1 ? 'entry' : 'entries'}). This action cannot be undone.</p>
        <div class="d-flex gap-2 justify-content-center">
          <button class="btn btn-soft px-3" id="ttConfirmSheetNo">Cancel</button>
          <button class="btn btn-danger px-3" id="ttConfirmSheetYes"><i class="bi bi-trash3"></i> Delete sheet</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#ttConfirmSheetYes').onclick = () => { document.body.removeChild(overlay); resolve(true); };
    overlay.querySelector('#ttConfirmSheetNo').onclick  = () => { document.body.removeChild(overlay); resolve(false); };
  });
}

function ttConfirmDelete(name, day, subject) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'tt-confirm-overlay';
    overlay.innerHTML = `
      <div class="tt-confirm-box">
        <div class="delete-modal-icon"><i class="bi bi-trash3"></i></div>
        <h5 class="fw-800 text-center mb-1">Delete entry?</h5>
        <p class="muted small text-center">${safe(name)} — ${safe(day)} — ${safe(subject)}</p>
        <div class="d-flex gap-2 justify-content-center mt-3">
          <button class="btn btn-soft" id="ttConfirmNo">Keep it</button>
          <button class="btn btn-danger" id="ttConfirmYes"><i class="bi bi-trash3"></i> Delete</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#ttConfirmYes').onclick = () => { document.body.removeChild(overlay); resolve(true); };
    overlay.querySelector('#ttConfirmNo').onclick  = () => { document.body.removeChild(overlay); resolve(false); };
  });
}

// ---------------------------------------------------------------------------
// File upload — preview first, then ask user to save or discard
// ---------------------------------------------------------------------------
let _pendingUploadFile = null;

async function ttHandleFileUpload(e) {
  const file = e.target.files[0];
  if (!file) return;
  e.target.value = '';   // reset so same file can be re-selected

  const status = $('#ttUploadStatus');
  const orgName = ($('#ttOrgName').value || '').trim();
  if (!orgName) {
    status.innerHTML = '<span class="text-danger"><i class="bi bi-exclamation-circle"></i> Enter an organization name before uploading.</span>';
    return;
  }

  status.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Previewing file…';

  const form = new FormData();
  form.append('file', file);
  form.append('organization_type', $('#ttOrgType').value);
  form.append('organization_name', orgName);

  try {
    const token = getToken();
    const res = await fetch(`${API_BASE}/api/timetable/preview-upload`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Preview failed');

    status.innerHTML = '';
    _pendingUploadFile = file;
    ttShowUploadPreviewModal(data, orgName, form);
  } catch (err) {
    status.innerHTML = `<span class="text-danger"><i class="bi bi-exclamation-circle"></i> ${safe(err.message)}</span>`;
  }
}

function ttShowUploadPreviewModal(data, orgName, originalForm) {
  // Remove any existing preview modal
  document.getElementById('ttPreviewModal')?.remove();

  const validCount  = data.valid_count  || 0;
  const errorCount  = data.total_errors || 0;
  const totalRows   = data.total_rows   || 0;

  // Sample rows (first 5)
  const sample = (data.valid || []).slice(0, 5);
  const sampleHtml = sample.map(r => `
    <tr>
      <td>${safe(r.participant_name)}</td>
      <td>${safe(r.day)}</td>
      <td>${safe(r.start_time)}–${safe(r.end_time)}</td>
      <td>${safe(r.subject_or_task)}</td>
    </tr>`).join('');

  const errorHtml = (data.errors || []).slice(0, 5).map(err =>
    `<small class="text-danger d-block"><i class="bi bi-exclamation-circle"></i> Row ${err.row}: ${safe(err.error)}</small>`
  ).join('') + (errorCount > 5 ? `<small class="muted">…and ${errorCount - 5} more errors</small>` : '');

  const overlay = document.createElement('div');
  overlay.id = 'ttPreviewModal';
  overlay.className = 'tt-confirm-overlay';
  overlay.innerHTML = `
    <div class="tt-confirm-box tt-preview-box">
      <div class="tt-preview-head">
        <i class="bi bi-file-earmark-spreadsheet"></i>
        <div>
          <h5 class="mb-0">Upload preview</h5>
          <small class="muted">${safe(orgName)}</small>
        </div>
      </div>
      <div class="tt-preview-stats">
        <div class="tt-preview-stat">
          <b>${totalRows}</b><small>Total rows</small>
        </div>
        <div class="tt-preview-stat text-success">
          <b>${validCount}</b><small>Ready to save</small>
        </div>
        <div class="tt-preview-stat ${errorCount ? 'text-danger' : 'muted'}">
          <b>${errorCount}</b><small>Errors</small>
        </div>
      </div>
      ${validCount ? `
        <div class="tt-preview-table-wrap">
          <table class="table tt-table mb-0">
            <thead><tr><th>Participant</th><th>Day</th><th>Time</th><th>Subject / Task</th></tr></thead>
            <tbody>${sampleHtml}</tbody>
          </table>
          ${totalRows > 5 ? `<p class="muted small text-center mt-1">…and ${totalRows - 5} more rows</p>` : ''}
        </div>` : ''}
      ${errorHtml ? `<div class="tt-upload-errors mt-2">${errorHtml}</div>` : ''}
      ${validCount === 0 ? `<p class="text-danger text-center small mt-2"><i class="bi bi-exclamation-triangle"></i> No valid rows found. Fix the errors and try again.</p>` : ''}
      <div class="d-flex gap-2 justify-content-center mt-3 flex-wrap">
        <button class="btn btn-soft" id="ttPreviewDiscard">
          <i class="bi bi-x-lg"></i> Discard
        </button>
        ${validCount > 0 ? `
        <button class="btn btn-primary" id="ttPreviewSave">
          <i class="bi bi-cloud-upload"></i> Save ${validCount} row${validCount !== 1 ? 's' : ''} to database
        </button>` : ''}
      </div>
    </div>`;

  document.body.appendChild(overlay);

  overlay.querySelector('#ttPreviewDiscard').onclick = () => {
    overlay.remove();
    _pendingUploadFile = null;
    $('#ttUploadStatus').innerHTML = '<span class="muted small">Upload discarded.</span>';
  };

  const saveBtn = overlay.querySelector('#ttPreviewSave');
  if (saveBtn) {
    saveBtn.onclick = async () => {
      saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving…';
      saveBtn.disabled = true;
      overlay.querySelector('#ttPreviewDiscard').disabled = true;

      // Re-upload the same file but to the actual save endpoint
      const saveForm = new FormData();
      saveForm.append('file', _pendingUploadFile);
      saveForm.append('organization_type', $('#ttOrgType').value);
      saveForm.append('organization_name', ($('#ttOrgName').value || '').trim());

      try {
        const token = getToken();
        const res = await fetch(`${API_BASE}/api/timetable/upload`, {
          method: 'POST',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: saveForm,
        });
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || 'Save failed');

        overlay.remove();
        _pendingUploadFile = null;
        ttLoadOrganizations();
        ttLoadEntries();
        $('#ttUploadStatus').innerHTML =
          `<span class="text-success"><i class="bi bi-check-circle"></i> ${result.inserted} saved, ${result.skipped} skipped.</span>`;
        toast('Timetable saved', `${result.inserted} entries added for "${($('#ttOrgName').value||'').trim()}".`);
      } catch (err) {
        saveBtn.innerHTML = '<i class="bi bi-cloud-upload"></i> Retry save';
        saveBtn.disabled = false;
        overlay.querySelector('#ttPreviewDiscard').disabled = false;
        $('#ttUploadStatus').innerHTML = `<span class="text-danger"><i class="bi bi-exclamation-circle"></i> ${safe(err.message)}</span>`;
      }
    };
  }
}

// ---------------------------------------------------------------------------
// Find common slot
// ---------------------------------------------------------------------------
async function ttFindCommonSlot() {
  if (!ttSelectedParticipants.length) { toast('Select participants', 'Choose at least one participant first.'); return; }
  const btn = $('#ttFindSlot');
  btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Analyzing…';
  btn.disabled = true;
  $('#ttRecommendations').innerHTML = '<div class="tt-loading"><span class="spinner-border spinner-border-sm"></span> AI is analyzing timetables and calendars…</div>';

  try {
    const data = await apiFetch('/api/timetable/find-common-slot', {
      method: 'POST',
      body: JSON.stringify({
        participants: ttSelectedParticipants,
        organization_name: ($('#ttOrgName').value || '').trim(),
        organization_type: $('#ttOrgType').value,
        duration_minutes: +$('#ttDuration').value,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
        work_start: $('#ttWorkStart').value || '09:00',
        work_end:   $('#ttWorkEnd').value   || '18:00',
        skip_lunch:    $('#ttSkipLunch').checked,
        skip_weekends: $('#ttSkipWeekends').checked,
        search_days: 14,
      })
    });
    ttRenderRecommendations(data.slots || [], data.participants || [], data.duration_minutes || 60);
  } catch (err) {
    $('#ttRecommendations').innerHTML = `<p class="muted small p-3"><i class="bi bi-exclamation-circle text-danger"></i> ${safe(err.message)}</p>`;
  } finally {
    btn.innerHTML = '<i class="bi bi-stars"></i> Find best slot';
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Render AI recommendations
// ---------------------------------------------------------------------------
function ttRenderRecommendations(slots, participants, duration) {
  const el = $('#ttRecommendations');
  if (!slots.length) {
    el.innerHTML = '<div class="tt-empty-recs"><i class="bi bi-calendar-x"></i><p>No common free slot found in the next 14 days. Try fewer participants or a shorter duration.</p></div>';
    return;
  }

  el.innerHTML = slots.slice(0, 3).map((s, i) => {
    const dateLabel = new Date(s.date + 'T12:00').toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
    const allAvail = s.available_count >= participants.length;
    const rankClass = i === 0 ? 'tt-rec-best' : '';
    return `<div class="tt-rec-card ${rankClass}">
      <div class="tt-rec-rank">${safe(s.rank || 'Option ' + (i+1))}</div>
      <div class="tt-rec-time">
        <b>${dateLabel}</b>
        <span>${safe(s.start_time)}–${safe(s.end_time)}</span>
      </div>
      <div class="tt-rec-avail">
        <i class="bi ${allAvail ? 'bi-people-fill text-success' : 'bi-exclamation-circle text-warning'}"></i>
        ${allAvail
          ? `All ${s.available_count} participant${s.available_count > 1 ? 's' : ''} available`
          : `${s.available_count} of ${participants.length} available`}
      </div>
      ${s.explanation ? `<p class="tt-rec-explanation">${safe(s.explanation)}</p>` : ''}
      <button class="btn btn-primary btn-sm w-100 mt-2"
        onclick="ttScheduleFromSlot('${s.date}','${s.start_time}','${s.end_time}','${s.available.join(',')}')">
        <i class="bi bi-calendar-check"></i> Schedule meeting
      </button>
    </div>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// Schedule from chosen slot
// ---------------------------------------------------------------------------
window.ttScheduleFromSlot = async (date, startTime, endTime, availableStr) => {
  const participants = availableStr.split(',').filter(Boolean);
  const title = prompt('Meeting title:', 'Team meeting');
  if (!title) return;

  try {
    const data = await apiFetch('/api/timetable/schedule', {
      method: 'POST',
      body: JSON.stringify({
        title,
        description: '',
        date,
        start_time: startTime,
        end_time: endTime,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
        participants,
      })
    });
    meetings.unshift(normalizeMeeting(data.meeting));
    renderStats(); renderMeetings(); renderInvites(); renderCalendar();
    toast('Meeting scheduled', `"${title}" on ${date} at ${startTime}`);
    go('calendar');
  } catch (err) {
    toast('Could not schedule', err.message);
  }
};
