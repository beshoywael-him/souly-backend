/* =============================================================================
   Every network call the classroom screen makes.

   One place, for the same reason the student app has one: the token header,
   the timeouts, the offline detection and the retry rule are decided once
   instead of in fifteen call sites.

   The header is X-Souly-Teacher. Not X-Souly-Token, not X-Souly-Parent. Three
   realms, three headers, three token tables — a token from one can never be
   presented to another by accident.
   ============================================================================= */

const Api = (() => {
  'use strict';

  const TOKEN_KEY = 'souly.teacher.token';
  const TIMEOUT_MS = 8000;

  const State = { token: null, teacher: null, online: true };

  try { State.token = localStorage.getItem(TOKEN_KEY); } catch (e) { State.token = null; }

  function saveToken(t) {
    State.token = t;
    try { localStorage.setItem(TOKEN_KEY, t); } catch (e) { /* private window */ }
  }

  function clearToken() {
    State.token = null;
    State.teacher = null;
    try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* ignore */ }
  }

  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout || TIMEOUT_MS);

    const opts = {
      method: options.method || 'GET',
      signal: controller.signal,
      headers: { 'Accept': 'application/json' },
    };
    if (State.token) opts.headers['X-Souly-Teacher'] = State.token;
    if (options.body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(options.body);
    }

    let res;
    try {
      res = await fetch('/api/teacher' + path, opts);
    } catch (e) {
      /* The router hiccupped, or the backend laptop went to sleep. Say that,
         rather than letting an unhandled rejection blank a screen a teacher
         is relying on mid-lesson. */
      State.online = false;
      throw new ApiError(0, 'Cannot reach Souly. Check the connection.');
    } finally {
      clearTimeout(timer);
    }

    State.online = true;

    /* A 401 from anything except sign-in means the token died. Drop it and
       let app.js show the gate, rather than leaving a signed-out screen
       half-drawn with somebody else's children on it. */
    if (res.status === 401 && !path.startsWith('/auth/login')) {
      clearToken();
      throw new ApiError(401, 'Session expired. Sign in again.');
    }

    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }

    if (!res.ok) {
      const detail = (data && (data.detail || data.message)) || res.statusText;
      throw new ApiError(res.status, typeof detail === 'string' ? detail : 'Something went wrong.');
    }
    return data;
  }

  /* -------------------------------------------------------------------------
     Auth
     ------------------------------------------------------------------------- */

  async function login(email, password) {
    const data = await request('/auth/login', {
      method: 'POST',
      body: { email, password, device_label: navigator.platform || null },
    });
    saveToken(data.token);
    State.teacher = data.teacher;
    return data;
  }

  async function resume() {
    if (!State.token) return null;
    const data = await request('/auth/me');
    State.teacher = data.teacher;
    return data;
  }

  async function logout() {
    const token = State.token;
    clearToken();
    if (!token) return;
    try {
      await fetch('/api/teacher/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
    } catch (e) { /* signing out locally is what actually matters */ }
  }

  /* -------------------------------------------------------------------------
     The board
     ------------------------------------------------------------------------- */

  /* One call per refresh, on purpose. This screen polls on the same router
     the camera is publishing over; three requests every two seconds is three
     times the chance of being visibly stale while somebody is watching. */
  const board = () => request('/board');

  const review = (flagId, status, note) =>
    request('/flags/' + flagId, { method: 'PATCH', body: { status, note: note || null } });

  const events = (flagId) => request('/flags/' + flagId + '/events');

  return { State, ApiError, login, resume, logout, board, review, events, clearToken };
})();
