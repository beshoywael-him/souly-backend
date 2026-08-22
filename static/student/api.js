/* =============================================================================
   Souly — API client and shared state.

   Every network call in the app goes through Api.request(). That gives one
   place to handle the failure that actually matters here: the MiFi router
   dropping mid-session. Rather than let a fetch rejection bubble up as a
   blank screen, requests retry once, surface a calm notice, and the UI keeps
   whatever it last successfully rendered.
   ============================================================================= */

const Api = (() => {
  'use strict';

  // Who is signed in. Set by the sign-in screen, remembered across reloads.
  //
  // ?student=stu-02 still works as a developer shortcut for jumping straight
  // into a profile without signing in. It is a convenience for us, not a way
  // in for a child: the picker is what they see.
  const params = new URLSearchParams(location.search);
  const TOKEN_KEY = 'souly.token';
  const STUDENT_KEY = 'souly.student';

  let studentId = params.get('student')
    || localStorage.getItem(STUDENT_KEY)
    || null;
  let token = localStorage.getItem(TOKEN_KEY) || null;

  function setSession(newToken, newStudentId) {
    token = newToken;
    studentId = newStudentId;
    localStorage.setItem(TOKEN_KEY, newToken);
    localStorage.setItem(STUDENT_KEY, newStudentId);
  }

  function clearSession() {
    token = null;
    studentId = null;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(STUDENT_KEY);
  }

  const BASE = '';
  const TIMEOUT_MS = 15000;

  let online = true;
  const listeners = [];

  function onConnectionChange(fn) { listeners.push(fn); }

  function setOnline(value) {
    if (online === value) return;
    online = value;
    listeners.forEach(fn => { try { fn(online); } catch (_) {} });
  }

  function url(path) {
    if (path.startsWith('/api/me')) {
      return BASE + path.replace('/api/me', `/api/students/${studentId}`);
    }
    return BASE + path;
  }

  async function request(path, options = {}, attempt = 0) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout || TIMEOUT_MS);

    try {
      const response = await fetch(url(path), {
        ...options,
        signal: controller.signal,
        headers: {
          ...(options.body instanceof FormData
            ? {}
            : { 'Content-Type': 'application/json' }),
          ...(token ? { 'X-Souly-Token': token } : {}),
          ...(options.headers || {})
        }
      });
      clearTimeout(timer);
      setOnline(true);

      if (!response.ok) {
        let detail = `Request failed (${response.status})`;
        try {
          const body = await response.json();
          if (body.detail) {
            detail = typeof body.detail === 'string'
              ? body.detail
              : JSON.stringify(body.detail);
          }
        } catch (_) { /* non-JSON error body */ }

        const error = new Error(detail);
        error.status = response.status;
        throw error;
      }

      if (response.status === 204) return null;
      return await response.json();

    } catch (err) {
      clearTimeout(timer);

      // Retry once on a network-level failure. A 4xx is our bug and retrying
      // it just doubles the wrong request.
      const isNetwork = err.name === 'AbortError' || err.name === 'TypeError';
      if (isNetwork && attempt === 0) {
        await new Promise(r => setTimeout(r, 600));
        return request(path, options, 1);
      }
      if (isNetwork) setOnline(false);
      throw err;
    }
  }

  const get = (path) => request(path);
  const post = (path, body) =>
    request(path, { method: 'POST', body: JSON.stringify(body || {}) });
  const put = (path, body) =>
    request(path, { method: 'PUT', body: JSON.stringify(body || {}) });
  const del = (path) => request(path, { method: 'DELETE' });

  function postForm(path, formData, timeout) {
    return request(path, { method: 'POST', body: formData, timeout: timeout || 45000 });
  }

  return {
    get, post, put, del, postForm, request,
    onConnectionChange,
    isOnline: () => online,

    // ---- Session -------------------------------------------------------------
    setSession, clearSession,
    getToken: () => token,
    getStudentId: () => studentId,
    isSignedIn: () => Boolean(studentId),
    logout: () => post('/api/auth/logout', { token }).finally(clearSession),

    // ---- Endpoints, named so callers read like intent ----------------------
    home:          ()               => get('/api/me/home'),
    profile:       ()               => get('/api/me/profile'),
    settings:      ()               => get('/api/me/settings'),
    saveSettings:  (patch)          => put('/api/me/settings', patch),

    subjects:      ()               => get('/api/me/subjects'),
    // The plan of lessons ahead, in book order, with this child's progress.
    lessons:       (code)           => get(`/api/me/subjects/${code}/lessons`),
    plan:          ()               => get('/api/me/plan'),
    lesson:        (id)             => get(`/api/me/lessons/${id}`),

    // One page of the book, explained for THIS child. The explanation is
    // cached per child, so coming back to a page shows the same lesson — and
    // it still works when the network doesn't.
    lessonPage:    (id, page)       => get(`/api/me/lessons/${id}/pages/${page}`),

    completePage:  (id, page, secs, back) => post(`/api/me/lessons/${id}/page`,
                                            { page, duration_s: secs || 0,
                                              went_back: !!back }),

    // --- The lesson hint layer ------------------------------------------------
    // Help never leaves the lesson: these all answer about the page the child
    // is looking at right now.
    explainPage:   (pageId, mode, secs, who) =>
                     post(`/api/me/pages/${pageId}/explain`,
                          { mode, seconds_on_page: secs || 0,
                            initiated_by: who || 'student', speak: true }),

    hint:          (questionId, tier, opts) =>
                     post(`/api/me/questions/${questionId}/hint`,
                          { tier, speak: true, ...(opts || {}) }),

    checkAnswer:   (questionId, index, secs, attempts) =>
                     post(`/api/me/questions/${questionId}/check`,
                          { answer_index: index, seconds_taken: secs || 0,
                            attempts_before: attempts || 0 }),

    // Souly reads the book page and writes fresh questions from it, so a child
    // repeating a lesson doesn't see the same four items every time.
    generatePractice: (lessonId, count, page) =>
                     post(`/api/me/lessons/${lessonId}/generate-practice`,
                          { count: count || 4, page: page || null }),

    // Should Souly speak first? Triggered by lack of progress, never by gaze.
    stallCheck:    (secs, wrong, offered) =>
                     post('/api/me/stall-check',
                          { seconds_on_page: secs, wrong_attempts: wrong || 0,
                            already_offered: !!offered }),

    startQuiz:     (opts)           => post('/api/me/quiz', opts || {}),
    currentQuiz:   ()               => get('/api/me/quiz/current'),
    answerQuiz:    (qid, idx, secs) => post(`/api/me/quiz/${qid}/answer`,
                                            { answer_index: idx, duration_s: secs || 0 }),

    chat:          (msg, pageId)    => post('/api/me/chat',
                          { message: msg, page_id: pageId || null }),
    chatHistory:   ()               => get('/api/me/chat/history'),
    clearChat:     ()               => del('/api/me/chat/history'),
    speak:         (text)           => post('/api/me/voice/speak', { text }),
    voiceAsk:      (formData)       => postForm('/api/me/voice/ask', formData),

    games:         ()               => get('/api/me/games'),
    gameQuestions: (id, n)          => get(`/api/me/games/${id}/questions?count=${n || 10}`),
    gameResult:    (id, body)       => post(`/api/me/games/${id}/result`, body),

    rewards:       ()               => get('/api/me/rewards'),
    unlockReward:  (id)             => post(`/api/me/rewards/${id}/unlock`),
    equipReward:   (id)             => post(`/api/me/rewards/${id}/equip`),

    achievements:  ()               => get('/api/me/achievements'),
    progress:      ()               => get('/api/me/progress'),
    challenge:     ()               => get('/api/me/challenge'),
    claimChallenge:()               => post('/api/me/challenge/claim'),

    health:        ()               => get('/health')
  };
})();


/* =============================================================================
   Shared state — one object the whole app reads from.
   ============================================================================= */

const State = {
  profile: null,
  plan: null,
  lessonPageId: null,
  practice: null,
  currentQuestionId: null,
  hintTier: 0,
  wrongAttempts: 0,
  settings: null,
  home: null,
  subjects: null,
  lesson: null,
  // The printed page number the child is on, and where in the lesson's page
  // list that is. Both, because the book's numbering and the lesson's own
  // ordering are different things.
  lessonPage: null,
  lessonPageIndex: 0,
  lessonStartedAt: null,
  quiz: null,
  quizStartedAt: null,
  chatMessages: [],
  games: null,
  activeGame: null,
  rewards: null,
  achievements: null,
  progress: null,
  degraded: false,

  /* Keep every visible star/level/streak counter in sync from one award
     response, so two screens can never disagree about the same number. */
  applyAward(award) {
    if (!award || !this.profile) return;
    this.profile.stars = award.total_stars;
    this.profile.xp = award.total_xp;
    this.profile.level = award.level;
    this.profile.level_title = award.level_title;
    this.profile.day_streak = award.streak_days;
    if (this.home && this.home.profile) {
      Object.assign(this.home.profile, {
        stars: award.total_stars,
        xp: award.total_xp,
        level: award.level,
        level_title: award.level_title,
        day_streak: award.streak_days
      });
    }
  }
};


/* =============================================================================
   Small shared helpers.
   ============================================================================= */

const Util = {
  /* Escape before interpolating into innerHTML. Lesson text, chat replies and
     student answers all reach the DOM this way, and a model or a child can
     produce an angle bracket. */
  esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  },

  num(value) {
    return (value === null || value === undefined) ? '0' : Number(value).toLocaleString();
  },

  pct(value) {
    return Math.max(0, Math.min(100, Math.round(value || 0)));
  },

  /* Circumference of the r=50 progress ring, for stroke-dashoffset. */
  ringOffset(percent) {
    const circumference = 314;
    return circumference - (circumference * this.pct(percent)) / 100;
  },

  time12(hhmm) {
    if (!hhmm) return '';
    const [h, m] = hhmm.split(':').map(Number);
    const suffix = h >= 12 ? 'PM' : 'AM';
    const hour = h % 12 || 12;
    return `${hour}:${String(m).padStart(2, '0')} ${suffix}`;
  },

  loading(label) {
    return `<div class="page-loading"><div class="spinner"></div><div>${this.esc(label || 'Loading…')}</div></div>`;
  },

  error(message, retryFn) {
    return `<div class="error-banner">
      <div>${this.esc(message)}</div>
      ${retryFn ? `<button onclick="${retryFn}">Try again</button>` : ''}
    </div>`;
  },

  empty(icon, message) {
    return `<div class="empty-state"><span class="empty-icon">${icon}</span>${this.esc(message)}</div>`;
  },

  robotMini() {
    return `<div class="robot-mini"><div class="robot-head"><div class="robot-face">
      <div class="robot-eye left"></div><div class="robot-eye right"></div>
      <div class="robot-smile"></div></div></div></div>`;
  },

  speechCard(message, withSpeakButton) {
    return `<div class="glass-card"><div class="speech-wrap">
      ${this.robotMini()}
      <div class="speech-bubble">${this.esc(message)}</div>
      ${withSpeakButton ? `<button class="speak-btn" onclick="Voice.speak(${JSON.stringify(message).replace(/"/g, '&quot;')})" aria-label="Read aloud">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3a4.5 4.5 0 00-2.5-4v8a4.5 4.5 0 002.5-4z"/></svg>
      </button>` : ''}
    </div></div>`;
  },

  topBar(icon, title, subtitle, backPage) {
    return `<div class="top-bar">
      <div class="top-bar-left">
        <button class="top-bar-back" onclick="App.go('${backPage || 'home'}')" aria-label="Go back">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
        </button>
        <div>
          <div class="top-bar-title">${icon} ${this.esc(title)}</div>
          <div class="top-bar-sub">${this.esc(subtitle)}</div>
        </div>
      </div>
    </div>`;
  },

  starPill(stars) {
    return `<div class="streak-badge">⭐ ${this.num(stars)}</div>`;
  }
};
