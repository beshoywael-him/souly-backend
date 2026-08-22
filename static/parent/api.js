/* =============================================================================
   api.js — every network call the Parents' Hub makes.

   Same rule as static/student/api.js: nothing else in this app calls fetch().
   When the token header changes, or a retry policy is added, or the base path
   moves, exactly one file changes.

   State lives here too, and it is deliberately thin: the signed-in parent,
   their children, and which child is currently selected. Everything else is
   re-fetched on navigation. There is no client-side cache of a child's
   progress — a parent looking at a stale number is worse than a parent
   waiting 80ms, and with four screens able to show the same child at once,
   caching is how they start disagreeing.
   ============================================================================= */

const Api = (() => {

  const TOKEN_KEY = 'souly.parent.token';
  const CHILD_KEY = 'souly.parent.child';

  /* ---------------------------------------------------------------------------
     State
     -------------------------------------------------------------------------
     `child` is the external_id of the child every screen is currently about.
     It is persisted so that a parent who reloads the page — or opens it again
     that evening — lands back on the child they were looking at, rather than
     being silently bounced to their first-born.
     ------------------------------------------------------------------------- */
  const State = {
    token: null,
    parent: null,
    children: [],
    child: null,
  };

  function loadToken() {
    try { State.token = localStorage.getItem(TOKEN_KEY); } catch (e) { State.token = null; }
    return State.token;
  }

  function saveToken(token) {
    State.token = token;
    try { localStorage.setItem(TOKEN_KEY, token); } catch (e) { /* private mode */ }
  }

  function clearToken() {
    State.token = null;
    State.parent = null;
    State.children = [];
    State.child = null;
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(CHILD_KEY);
    } catch (e) { /* ignore */ }
  }

  function rememberChild(extId) {
    State.child = extId;
    try { localStorage.setItem(CHILD_KEY, extId); } catch (e) { /* ignore */ }
  }

  function recallChild() {
    try { return localStorage.getItem(CHILD_KEY); } catch (e) { return null; }
  }

  /* ---------------------------------------------------------------------------
     The one fetch
     ------------------------------------------------------------------------- */

  class ApiError extends Error {
    constructor(status, detail) {
      super(detail || 'Request failed');
      this.status = status;
      this.detail = detail;
    }
  }

  async function request(path, options = {}) {
    const opts = {
      method: options.method || 'GET',
      headers: { 'Accept': 'application/json' },
    };

    // X-Souly-Parent, not X-Souly-Token. The student app's header name is
    // different on purpose: a student token can never be sent here by
    // accident, and the server looks it up in a different table anyway.
    if (State.token) opts.headers['X-Souly-Parent'] = State.token;

    if (options.body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(options.body);
    }

    let res;
    try {
      res = await fetch('/api/parent' + path, opts);
    } catch (e) {
      // The MiFi hiccupped, or the laptop went to sleep. Say that, rather
      // than letting an unhandled promise rejection blank the screen.
      throw new ApiError(0, 'Cannot reach Souly. Check the connection and try again.');
    }

    // A 401 from ANY endpoint except sign-in means the token died: drop it and
    // let app.js show the gate again, rather than leaving a signed-out page
    // half-rendered.
    //
    // Sign-in is the exception, and it matters. A 401 there means the code
    // was wrong, and the server said so in words a parent can act on. Wrapping
    // it in "Session expired" tells someone mistyping their code that
    // something has gone wrong with an account they have not opened yet.
    if (res.status === 401 && !path.startsWith('/auth/login')) {
      clearToken();
      throw new ApiError(401, 'Session expired');
    }

    let payload = null;
    const text = await res.text();
    if (text) {
      try { payload = JSON.parse(text); } catch (e) { payload = null; }
    }

    if (!res.ok) {
      const detail = payload && payload.detail
        ? (typeof payload.detail === 'string' ? payload.detail : 'Request failed')
        : `Request failed (${res.status})`;
      throw new ApiError(res.status, detail);
    }

    return payload;
  }

  /* ---------------------------------------------------------------------------
     Auth
     ------------------------------------------------------------------------- */

  async function login(accessCode) {
    const data = await request('/auth/login', {
      method: 'POST',
      body: { access_code: accessCode, device_label: navigator.platform || null },
    });
    saveToken(data.token);
    adoptSession(data);
    return data;
  }

  async function resume() {
    if (!loadToken()) return null;
    const data = await request('/auth/me');
    adoptSession(data);
    return data;
  }

  async function logout() {
    const token = State.token;
    clearToken();
    if (token) {
      try { await fetch('/api/parent/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      }); } catch (e) { /* signing out locally is what matters */ }
    }
  }

  /* Take a /login or /me payload and settle on which child is selected. */
  function adoptSession(data) {
    State.parent = data.parent;
    State.children = data.children || [];

    const remembered = recallChild();
    const stillLinked = State.children.some(c => c.external_id === remembered);

    if (stillLinked) {
      State.child = remembered;
    } else if (State.children.length) {
      // No memory, or the remembered child is no longer linked to this
      // parent. Pick the one with something unread, then the most recently
      // active — a parent opening the hub usually wants whichever child has
      // news, and with two sons that guess is right more often than "first".
      const withNews = State.children.find(c => c.unread_total > 0);
      const chosen = withNews || State.children
        .slice()
        .sort((a, b) => (b.last_active_date || '').localeCompare(a.last_active_date || ''))[0];
      rememberChild(chosen.external_id);
    } else {
      State.child = null;
    }
  }

  function currentChild() {
    return State.children.find(c => c.external_id === State.child) || null;
  }

  async function refreshChildren() {
    const data = await request('/children');
    State.children = data.children || [];
    // Keep the selection valid if a child was unlinked while we were open.
    if (!State.children.some(c => c.external_id === State.child) && State.children.length) {
      rememberChild(State.children[0].external_id);
    }
    return data;
  }

  /* ---------------------------------------------------------------------------
     Per-child reads. `c()` builds the path for the selected child, so no
     caller ever has to remember to scope its request — forgetting would mean
     showing one sibling's data under the other's name.
     ------------------------------------------------------------------------- */

  const c = (suffix) => `/children/${encodeURIComponent(State.child)}${suffix}`;

  return {
    State,
    ApiError,

    login,
    resume,
    logout,
    loadToken,
    clearToken,
    rememberChild,
    currentChild,
    refreshChildren,

    overview:     () => request(c('/overview')),
    progress:     () => request(c('/progress')),
    subjects:     () => request(c('/subjects')),
    subject:      (code) => request(c(`/subjects/${encodeURIComponent(code)}`)),
    notes:        () => request(c('/notes')),
    achievements: () => request(c('/achievements')),
    support:      () => request(c('/support')),
    teachers:     () => request(c('/teachers')),

    markNoteRead: (id) => request(`/notes/${id}/read`, { method: 'POST' }),
    saveSettings: (settings) => request(c('/support'), { method: 'PUT', body: { settings } }),

    conversations: () => request('/conversations'),
    conversation:  (id) => request(`/conversations/${id}`),
    sendMessage:   (id, body) => request(`/conversations/${id}/messages`, {
                     method: 'POST', body: { body },
                   }),
    startConversation: (teacherId) => request(c('/conversations'), {
                     method: 'POST', body: { teacher_id: teacherId },
                   }),
  };
})();
