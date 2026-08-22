/* =============================================================================
   Souly — application controller and voice loop.

   Voice : microphone capture, STT, speech output, word-synced read-aloud
   App   : routing, the lesson flow, the hint ladder, stall detection
   ============================================================================= */

/* =============================================================================
   VOICE
   ============================================================================= */

const Voice = (() => {
  'use strict';

  let recorder = null, chunks = [], recording = false;
  let currentAudio = null, stream = null;
  let highlightTimer = null;

  function speak(text, onEnd) {
    if (!text || !State.settings || !State.settings.read_aloud) {
      if (onEnd) onEnd();
      return;
    }
    stopSpeaking();
    Api.speak(text).then(result => {
      if (result.audio_base64) {
        const audio = new Audio(`data:${result.mime_type};base64,${result.audio_base64}`);
        audio.volume = (State.settings.voice_volume || 70) / 100;
        audio.onended = () => { currentAudio = null; if (onEnd) onEnd(); };
        audio.onerror = () => browserSpeak(text, onEnd);
        currentAudio = audio;
        audio.play().catch(() => browserSpeak(text, onEnd));
      } else {
        browserSpeak(text, onEnd);
      }
    }).catch(() => browserSpeak(text, onEnd));
  }

  function browserSpeak(text, onEnd) {
    if (!('speechSynthesis' in window)) { if (onEnd) onEnd(); return; }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.volume = (State.settings?.voice_volume || 70) / 100;
    // Slower than default. Children with auditory processing differences, and
    // anyone reading along with the text, need the extra room.
    u.rate = 0.92;
    u.pitch = 1.05;
    u.lang = ({ en: 'en-US', ar: 'ar-SA', fr: 'fr-FR' })[State.settings?.language] || 'en-US';
    if (onEnd) u.onend = onEnd;
    window.speechSynthesis.speak(u);
  }

  /* Read the lesson step with word-level highlighting.

     Adesope & Nesbit (2012) found narration plus text beats narration alone
     (g = 0.29) — but the benefit disappears when a competing picture is on
     screen. Synchronising the highlight is what keeps the two channels
     indexing each other rather than fighting. */
  function readStep(button) {
    const body = document.getElementById('lessonBody');
    if (!body) return;

    const words = Array.from(body.querySelectorAll('.w'));
    const text = words.map(w => w.textContent).join(' ');
    if (!text.trim()) return;

    clearHighlight();
    if (button) button.classList.add('speaking');

    // ~150 words/minute at our slower rate.
    const perWord = 400;
    let index = 0;
    highlightTimer = setInterval(() => {
      words.forEach((w, i) => {
        w.classList.toggle('now', i === index);
        w.classList.toggle('spoken', i < index);
      });
      index += 1;
      if (index > words.length) clearHighlight();
    }, perWord);

    speak(text, () => {
      clearHighlight();
      if (button) button.classList.remove('speaking');
    });
  }

  function clearHighlight() {
    if (highlightTimer) { clearInterval(highlightTimer); highlightTimer = null; }
    document.querySelectorAll('.lesson-body .w').forEach(w => {
      w.classList.remove('now', 'spoken');
    });
  }

  function stopSpeaking() {
    if (currentAudio) { currentAudio.pause(); currentAudio = null; }
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    clearHighlight();
  }

  function speakElement(id, button) {
    const el = document.getElementById(id);
    if (!el) return;
    if (button) button.classList.add('speaking');
    speak(el.textContent, () => { if (button) button.classList.remove('speaking'); });
  }

  function setStatus(text, className) {
    const el = document.getElementById('voiceStatus');
    if (el) { el.innerHTML = text; el.className = 'voice-status ' + (className || ''); }
  }

  function setButton(className) {
    const btn = document.getElementById('voiceBtn');
    if (btn) btn.className = 'voice-btn ' + (className || '');
  }

  async function start() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus('Voice needs a browser with microphone support.', 'error');
      return;
    }
    stopSpeaking();
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
      });
    } catch (err) {
      // "Said no" and "no microphone" need different fixes, so say which.
      setStatus(err.name === 'NotAllowedError'
        ? 'I need permission for the microphone. You can type instead.'
        : 'No microphone found. You can type instead.', 'error');
      return;
    }

    chunks = [];
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : (MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '');
    recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    recorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
    recorder.onstop = handleStop;
    recorder.start();

    recording = true;
    setButton('recording');
    setStatus('<span class="wave"><span></span><span></span><span></span><span></span></span> Listening — tap to stop', 'listening');
    setTimeout(() => { if (recording) stop(); }, 30000);
  }

  function stop() {
    if (recorder && recording) {
      recording = false;
      recorder.stop();
      if (stream) stream.getTracks().forEach(t => t.stop());
      setButton('thinking');
      setStatus('<span class="spinner"></span> Thinking…');
    }
  }

  async function handleStop() {
    const blob = new Blob(chunks, { type: 'audio/webm' });
    chunks = [];
    if (blob.size < 1200) {
      setButton('');
      setStatus("I didn't hear anything. Hold the button a bit longer.", 'error');
      return;
    }

    const form = new FormData();
    form.append('audio', blob, 'speech.webm');
    form.append('speak', 'true');
    if (State.lessonPageId) form.append('page_id', String(State.lessonPageId));

    try {
      const result = await Api.voiceAsk(form);
      setButton('');
      if (!result.stt_ok) {
        setStatus("I didn't catch that. Try again, or type it.", 'error');
        App.soulySays(result.reply);
        return;
      }
      setStatus('');
      App.soulySays(result.reply, { heard: result.heard, engine: result.engine });
      if (result.award) { State.applyAward(result.award); App.syncCounters(); }
      if (result.speech) playSpeech(result.speech, result.reply);
    } catch (err) {
      setButton('');
      setStatus('Something went wrong: ' + err.message, 'error');
    }
  }

  function playSpeech(speech, fallbackText) {
    if (!State.settings?.read_aloud) return;
    if (speech.audio_base64) {
      const audio = new Audio(`data:${speech.mime_type};base64,${speech.audio_base64}`);
      audio.volume = (State.settings.voice_volume || 70) / 100;
      audio.onerror = () => browserSpeak(fallbackText);
      currentAudio = audio;
      audio.play().catch(() => browserSpeak(fallbackText));
    } else {
      browserSpeak(fallbackText);
    }
  }

  function toggle() { recording ? stop() : start(); }

  return { toggle, start, stop, speak, speakElement, readStep, stopSpeaking,
           browserSpeak, playSpeech };
})();


/* =============================================================================
   APP
   ============================================================================= */

const App = (() => {
  'use strict';

  let currentPage = 'home';
  let health = null;
  let stallTimer = null;
  // When the child last did anything: typed, sent, tapped a button, answered.
  // This is what "idle" is measured from.
  let lastInteractionAt = Date.now();
  // How many requests to Souly are in flight. Never interrupt one.
  let inFlight = 0;
  let stepEnteredAt = null;
  let offeredOnThisStep = false;

  const el = (id) => document.getElementById(id);
  const pageEl = (name) => el('page-' + name);

  /* ---- Feedback ---------------------------------------------------------- */

  function toast(message, type) {
    const stack = el('toastStack');
    if (!stack) return;
    const node = document.createElement('div');
    node.className = 'toast ' + (type || '');
    node.textContent = message;
    stack.appendChild(node);
    setTimeout(() => {
      node.style.opacity = '0';
      setTimeout(() => node.remove(), 300);
    }, type === 'error' ? 5000 : 2600);
  }

  function announce(text) {
    const region = el('srAnnounce');
    if (region) region.textContent = text;
  }

  function confetti() {
    if (State.settings?.reduce_motion) return;
    const container = el('confetti');
    if (!container) return;
    const colors = ['#7C3AED', '#A855F7', '#FDE047', '#34D399', '#F472B6', '#60A5FA'];
    container.innerHTML = '';
    for (let i = 0; i < 50; i++) {
      const piece = document.createElement('div');
      piece.className = 'confetti-piece';
      piece.style.left = Math.random() * 100 + '%';
      piece.style.background = colors[Math.floor(Math.random() * colors.length)];
      piece.style.animationDelay = Math.random() * 0.6 + 's';
      piece.style.animationDuration = (1.5 + Math.random() * 1.5) + 's';
      const size = 6 + Math.random() * 8;
      piece.style.width = piece.style.height = size + 'px';
      piece.style.borderRadius = Math.random() > 0.5 ? '50%' : '2px';
      container.appendChild(piece);
    }
    setTimeout(() => { container.innerHTML = ''; }, 3500);
  }

  function overlay(icon, title, subtitle) {
    el('starsIcon').textContent = icon;
    el('starsTitle').textContent = title;
    el('starsSub').textContent = subtitle;
    const modal = el('starsOverlay');
    modal.dataset.shownAt = String(Date.now());
    modal.classList.add('active');
    announce(title + '. ' + subtitle);
  }

  function closeOverlay() {
    const modal = el('starsOverlay');
    modal.classList.remove('active');
    delete modal.dataset.shownAt;
  }

  function overlayIsOpen() {
    return el('starsOverlay').classList.contains('active');
  }

  function celebrateBadges(badges) {
    if (!badges?.length) return;
    const b = badges[0];
    confetti();
    overlay(b.icon, 'Badge earned', `${b.name} — ${b.description}`);
    Voice.speak(`You earned the ${b.name} badge.`);
  }

  function celebrateLevel(award) {
    confetti();
    overlay('🎉', `Level ${award.level}`, `You're a ${award.level_title} now.`);
    Voice.speak(`Level ${award.level}. You're a ${award.level_title} now.`);
  }

  function syncCounters() {
    const p = State.profile;
    if (!p) return;
    document.querySelectorAll('[data-bind="stars"]').forEach(n => {
      n.textContent = '⭐ ' + Util.num(p.stars);
    });
    document.querySelectorAll('[data-bind="streak"]').forEach(n => {
      n.textContent = '🔥 ' + p.day_streak;
    });
  }

  function handleAward(award, label) {
    if (!award) return;
    State.applyAward(award);
    syncCounters();
    if (award.stars_delta > 0) {
      toast(`${label || 'Nice'} +${award.stars_delta} ⭐`, 'star');
    }
    if (award.new_badges?.length) celebrateBadges(award.new_badges);
    else if (award.leveled_up) celebrateLevel(award);
  }

  /* ---- Plan strip -------------------------------------------------------- */

  function renderPlan() {
    const strip = el('planStrip');
    if (!strip) return;
    strip.innerHTML = Pages.planStrip(State.plan, State.profile);
  }

  /* ---- Settings ---------------------------------------------------------- */

  function applySettings(s) {
    State.settings = s;
    document.body.className = [
      'font-' + s.font_size,
      'theme-' + s.theme,
      s.high_contrast ? 'high-contrast' : '',
      s.larger_buttons ? 'larger-buttons' : '',
      s.reduce_motion ? 'reduce-motion' : ''
    ].filter(Boolean).join(' ');
  }

  async function setSetting(key, value) {
    const previous = State.settings[key];
    State.settings[key] = value;
    applySettings(State.settings);
    try {
      applySettings(await Api.saveSettings({ [key]: value }));
      if (currentPage === 'profile') renderProfile();
    } catch (err) {
      State.settings[key] = previous;
      applySettings(State.settings);
      toast('Could not save that: ' + err.message, 'error');
      if (currentPage === 'profile') renderProfile();
    }
  }

  function toggleSetting(key, node) {
    const next = !State.settings[key];
    node.classList.toggle('on', next);
    node.setAttribute('aria-checked', String(next));
    setSetting(key, next);
  }

  /* ---- Routing -----------------------------------------------------------

     The screen lives in the URL. A child who refreshes — or whose tablet
     reloads the page on its own, which happens — used to land back at Home
     having lost the lesson they were three pages into. Now the address bar
     carries it, so a refresh comes back to the same page of the same lesson,
     and Back works the way a child expects a Back button to work.
     ---------------------------------------------------------------------- */

  const RESUMABLE = ['home', 'learn', 'progress', 'profile', 'games',
                     'rewards', 'achievements', 'lesson'];

  function writeRoute(page, topicId, pageNo) {
    // Practice is deliberately not resumable: its questions are generated for
    // one sitting and reloading into a half-finished set is worse than
    // starting it again.
    if (!RESUMABLE.includes(page)) return;
    const hash = (page === 'lesson' && topicId)
      ? `#lesson/${topicId}${pageNo ? '/' + pageNo : ''}`
      : `#${page}`;
    if (location.hash !== hash) {
      history.replaceState(null, '', hash);
    }
  }

  function readRoute() {
    const raw = (location.hash || '').replace(/^#/, '').split('/');
    const page = raw[0];
    if (!RESUMABLE.includes(page)) return null;
    return {
      page,
      topicId: raw[1] ? Number(raw[1]) : null,
      pageNo: raw[2] ? Number(raw[2]) : null,
    };
  }

  async function go(page) {
    Voice.stopSpeaking();
    Games.stop();
    stopStallWatch();

    document.querySelectorAll('.page').forEach(p => {
      p.classList.remove('active');
    });
    const target = pageEl(page);
    if (target) target.classList.add('active');

    // The lesson and practice screens fill the viewport; the rest scroll.
    ['lesson', 'practice'].forEach(name => {
      const node = pageEl(name);
      if (node) node.classList.toggle('split', name === page);
    });

    document.querySelectorAll('.rail-item').forEach(n => n.classList.remove('active'));
    const nav = document.querySelector(`.rail-item[data-page="${page}"]`);
    if (nav) nav.classList.add('active');

    currentPage = page;
    if (page !== 'lesson') writeRoute(page);

    const loaders = {
      home: renderHome, learn: renderLearn, progress: renderProgress,
      profile: renderProfile, games: renderGames,
      achievements: renderAchievements, rewards: renderRewards
    };
    if (loaders[page]) await loaders[page]();
    announce(page + ' page');
  }

  async function load(page, label, loader) {
    const container = pageEl(page);
    container.innerHTML = Util.loading(label);
    try {
      await loader(container);
    } catch (err) {
      container.innerHTML = Util.error(err.message || 'Could not load this page.',
                                       `App.go('${page}')`);
    }
  }

  /* ---- Pages ------------------------------------------------------------- */

  const renderHome = () => load('home', 'Getting ready…', async (c) => {
    const data = await Api.home();
    State.home = data;
    State.profile = data.profile;
    State.plan = { ...data.daily_challenge, current: 'lesson_done' };
    applySettings(data.settings);
    c.innerHTML = Pages.home(data);
    renderPlan();
  });

  const renderLearn = () => load('learn', 'Loading subjects…', async (c) => {
    const data = await Api.subjects();
    State.subjects = data;
    c.innerHTML = Pages.learn(data);
  });

  const renderProgress = () => load('progress', 'Adding it up…', async (c) => {
    c.innerHTML = Pages.progress(await Api.progress());
  });

  const renderGames = () => load('games', 'Loading games…', async (c) => {
    const data = await Api.games();
    State.games = data;
    c.innerHTML = Pages.games(data);
  });

  const renderAchievements = () => load('achievements', 'Counting badges…', async (c) => {
    const [data, profile] = await Promise.all([Api.achievements(), Api.profile()]);
    State.profile = profile;
    c.innerHTML = Pages.achievements(data, profile);
  });

  const renderRewards = () => load('rewards', 'Opening the shop…', async (c) => {
    const data = await Api.rewards();
    State.rewards = data;
    c.innerHTML = Pages.rewards(data);
  });

  const renderProfile = () => load('profile', 'Loading…', async (c) => {
    const [profile, settings] = await Promise.all([Api.profile(), Api.settings()]);
    State.profile = profile;
    applySettings(settings);
    c.innerHTML = Pages.profile(profile, settings, health);
  });

  /* ---- Subjects & the learning path -------------------------------------- */

  async function openSubject(code) {
    document.querySelectorAll('.subject-chip').forEach(chip => {
      chip.classList.toggle('active', chip.dataset.code === code);
      chip.setAttribute('aria-selected', String(chip.dataset.code === code));
    });

    const area = el('pathArea');
    area.innerHTML = Util.loading('Loading lessons…');
    try {
      const lessons = await Api.lessons(code);
      const subject = State.subjects.subjects.find(s => s.code === code);
      area.innerHTML = Pages.learningPath(subject ? subject.name : code, lessons);
    } catch (err) {
      area.innerHTML = Util.error(err.message);
    }
  }

  /* ---- The lesson -------------------------------------------------------- */

  async function openLesson(topicId, startPage) {
    await go('lesson');
    await load('lesson', 'Opening your lesson…', async (c) => {
      const data = await Api.lesson(topicId);
      State.lesson = data;

      if (!data.pages || !data.pages.length) {
        pageEl('lesson').innerHTML = Pages.lesson(data, null);
        return;
      }

      /* Resume where they left off. `last_page` is the book's printed page
         number, not an ordinal, so it survives the lesson map being
         re-ingested with an extra page in the middle. A page number in the
         URL — from a refresh — wins over it. */
      let index = 0;
      const wanted = startPage || data.last_page;
      if (wanted) {
        const found = data.pages.findIndex(p => p.page === Number(wanted));
        if (found >= 0) index = found;
      }
      await showPage(index);
    });
  }

  /* Fetch and render one page: the scanned page itself, plus the explanation
     Souly wrote for THIS child from it. The explanation is cached server-side
     per child, so coming back to a page shows the same lesson rather than a
     freshly-invented one — and it still works when the network doesn't. */
  async function showPage(index) {
    const data = State.lesson;
    if (!data || !data.pages || !data.pages.length) return;

    const clamped = Math.max(0, Math.min(index, data.pages.length - 1));
    const page = data.pages[clamped];

    State.lessonPageIndex = clamped;
    State.lessonPage = page.page;
    State.lessonPageId = page.page_id;
    writeRoute('lesson', data.id, page.page);

    const host = pageEl('lesson');
    host.classList.add('split');

    let view;
    try {
      view = await Api.lessonPage(data.id, page.page);
    } catch (err) {
      /* The page image is still worth showing even when the explanation
         cannot be written — the book is the content, and a child can read it
         while we say so plainly. */
      view = {
        page: page.page, page_id: page.page_id, ordinal: clamped + 1,
        total_pages: data.pages.length, image_url: page.image_url,
        book_title: data.book_title,
        explanation: "I can't write my own explanation right now, so let's "
                   + "read this page together. Tell me the first bit you're "
                   + "not sure about.",
        engine: 'fallback', cached: false,
      };
    }

    host.innerHTML = Pages.lesson(data, view);
    State.lessonView = view;

    stepEnteredAt = Date.now();
    offeredOnThisStep = false;
    startStallWatch();

    if (State.settings?.read_aloud) {
      // Small delay so the screen settles before the voice starts — an
      // abrupt jump is disorienting.
      setTimeout(() => Voice.readStep(), 500);
    }
  }

  /* Move to the page at `ordinal` (1-based within the lesson), recording the
     page they are leaving as worked through. */
  async function goToPage(ordinal) {
    const data = State.lesson;
    if (!data || ordinal < 1) return;

    stopStallWatch();
    Voice.stopSpeaking();

    const seconds = stepEnteredAt ? Math.round((Date.now() - stepEnteredAt) / 1000) : 0;
    const goingBack = ordinal - 1 < State.lessonPageIndex;
    const leaving = data.pages[State.lessonPageIndex];

    try {
      const result = await Api.completePage(data.id, leaving.page, seconds, goingBack);
      handleAward(result.award,
                  result.lesson_complete ? 'Lesson finished!' : 'Page done');

      if (result.lesson_complete) {
        confetti();
        overlay('🎓', 'Lesson finished', `Nice work on ${data.title}.`);
        Voice.speak(`Nice work. You finished ${data.title}.`);
        State.plan = { ...State.plan, lesson_done: true, current: 'quiz_done' };
        renderPlan();
        // Straight into practice on the same material, rather than dumping
        // them back at a menu.
        setTimeout(() => startPractice(data.id), 900);
        return;
      }
    } catch (err) {
      toast('Could not save progress: ' + err.message, 'error');
    }

    if (ordinal > data.pages.length) { go('learn'); return; }

    await showPage(ordinal - 1);
  }

  function readStep(button) { Voice.readStep(button); }

  /* ---- The Souly thread ---------------------------------------------------

     Everything Souly and the child say to each other, in order, in one
     scrolling column. Both screens have one: on the lesson screen it is the
     right-hand pane, on the practice screen it is the hint ladder, so a clue
     and an answer to a typed question sit in the same conversation rather
     than in two places that don't know about each other.
     ---------------------------------------------------------------------- */

  const Thread = {
    container() { return el('soulyThread'); },

    add(role, text, meta) {
      const box = this.container();
      if (!box) return;
      this.clearTyping();
      box.insertAdjacentHTML('beforeend', Pages.bubble(role, text, meta));
      box.scrollTop = box.scrollHeight;
    },

    /* Raw HTML, for the hint ladder's own step markup. */
    addHTML(html) {
      const box = this.container();
      if (!box) return;
      this.clearTyping();
      box.insertAdjacentHTML('beforeend', html);
      box.scrollTop = box.scrollHeight;
    },

    typing() {
      const box = this.container();
      if (!box) return;
      this.clearTyping();
      box.insertAdjacentHTML('beforeend',
        '<div class="bubble souly typing" id="soulyTyping">' +
        '<span class="typing-dots"><span></span><span></span><span></span></span></div>');
      box.scrollTop = box.scrollHeight;
    },

    clearTyping() {
      const node = el('soulyTyping');
      if (node) node.remove();
    },
  };

  /* ---- Help: the three buttons ------------------------------------------- */

  const HELP_LABELS = {
    simpler: "I don't get this",
    example: 'Show me an example',
    another_way: 'Say it another way',
  };

  function soulySays(text, meta) {
    if (meta && meta.heard) Thread.add('student', meta.heard);
    Thread.add('souly', text, meta);
    announce('Souly says: ' + text);
  }

  async function askHelp(mode, initiatedBy) {
    if (!State.lessonPageId) return;
    touch();

    // Show what was asked, so the reply has something to be a reply TO.
    Thread.add('student', HELP_LABELS[mode] || mode);
    Thread.typing();

    document.querySelectorAll('.help-btn').forEach(b => { b.disabled = true; });
    const state = el('soulyState');
    if (state) state.textContent = 'Thinking…';

    const seconds = idleSeconds();
    inFlight += 1;

    try {
      const result = await Api.explainPage(State.lessonPageId, mode, seconds,
                                           initiatedBy || 'student');
      Thread.add('souly', result.text, { engine: result.engine });
      announce('Souly says: ' + result.text);
      if (state) state.textContent = result.cached ? 'Same answer as before' : 'Here if you need me';
      if (result.speech) Voice.playSpeech(result.speech, result.text);
      else Voice.speak(result.text);
    } catch (err) {
      Thread.add('souly', "I couldn't work that out just now. Read it once more and tell me which bit is tricky.");
      toast(err.message, 'error');
    } finally {
      inFlight -= 1;
      touch();
      document.querySelectorAll('.help-btn').forEach(b => { b.disabled = false; });
    }
  }

  async function askFree() {
    const input = el('askInput');
    const text = input ? input.value.trim() : '';
    if (!text) return;
    input.value = '';
    touch();

    Thread.add('student', text);
    Thread.typing();
    inFlight += 1;

    try {
      const result = await Api.chat(text, State.lessonPageId);
      Thread.add('souly', result.reply, {
        engine: result.engine,
        illustrationUrl: result.illustration_url || null,
      });
      announce('Souly says: ' + result.reply);
      Voice.speak(result.reply);
      if (result.award) { State.applyAward(result.award); syncCounters(); }
    } catch (err) {
      Thread.add('souly', "I'm having trouble reaching my brain right now.");
      toast(err.message, 'error');
    } finally {
      inFlight -= 1;
      touch();
    }
  }

  /* ---- Stall detection ---------------------------------------------------
     Souly speaks first when a child stops making progress.

     Deliberately NOT triggered by looking away. Autistic children avert their
     gaze MORE as a task gets harder — it reduces cognitive load so they can
     think. An attention-based trigger would interrupt them at exactly the
     wrong moment. Lack of progress is a signal; lack of eye contact is not.

     "Idle" means idle. It used to mean "time since the page opened", which
     never reset when the child did something — so asking a question and
     reading the reply still counted as being stuck, and the offer landed on
     top of the answer they had just been given.
     ---------------------------------------------------------------------- */

  const OFFER_COOLDOWN_MS = 30000;

  function touch() {
    lastInteractionAt = Date.now();
  }

  function idleSeconds() {
    return Math.round((Date.now() - (lastInteractionAt || Date.now())) / 1000);
  }

  function startStallWatch() {
    stopStallWatch();
    touch();
    stallTimer = setInterval(async () => {
      if (offeredOnThisStep || !lastInteractionAt) return;
      if (overlayIsOpen()) return;
      // Never interrupt a request that is already running, and never speak
      // straight after speaking.
      if (inFlight > 0) return;
      if (Date.now() - lastInteractionAt < OFFER_COOLDOWN_MS) return;

      try {
        const result = await Api.stallCheck(idleSeconds(),
                                            State.wrongAttempts || 0,
                                            offeredOnThisStep);
        if (result.offer && inFlight === 0 &&
            Date.now() - lastInteractionAt >= OFFER_COOLDOWN_MS) {
          offeredOnThisStep = true;
          offerHelp();
        }
      } catch (_) { /* a failed poll must never break the lesson */ }
    }, 10000);
  }

  function stopStallWatch() {
    if (stallTimer) { clearInterval(stallTimer); stallTimer = null; }
  }

  function offerHelp() {
    const page = State.lesson?.pages?.[State.lessonPageIndex];
    const topic = page ? `page ${page.page}` : 'this part';

    // Offer the specific thing, not "do you need help?" — a general offer is
    // an evaluation, and it invites a child to say no out of embarrassment.
    // It goes in as a turn at the bottom of the conversation, never as a
    // layer over what Souly just said.
    Thread.add('souly', `Want me to go over ${topic} again?`, {
      offer: true,
      actions: `<div class="bubble-actions">
         <button class="btn-secondary" onclick="App.askHelp('simpler','souly')">Yes please</button>
         <button class="btn-secondary" onclick="App.dismissOffer()">I'm fine</button>
       </div>`,
    });
    announce(`Souly asks: want me to go over ${topic} again?`);
  }

  function dismissOffer() {
    // "I'm fine" is respected for the rest of the page. A child who says
    // they're working should be believed.
    offeredOnThisStep = true;
    touch();
    Thread.add('souly', "No problem. I'm here if you change your mind.");
  }

  /* ---- Practice + the hint ladder ---------------------------------------- */

  async function startPractice(lessonId) {
    await go('practice');
    pageEl('practice').classList.add('split');
    pageEl('practice').innerHTML = Util.loading('Souly is writing you some questions…');

    try {
      const result = await Api.generatePractice(lessonId, 4);
      if (!result.questions.length) {
        pageEl('practice').innerHTML = Util.empty('🤔',
          'No practice questions available for this lesson yet.');
        return;
      }
      State.practice = {
        lesson_id: lessonId,
        lesson_title: State.lesson?.title || '',
        questions: result.questions,
        index: 0,
        correct: 0,
        total: result.questions.length,
        hints_used: 0,
        engine: result.engine,
        generated: !result.fell_back_to_bank
      };
      State.wrongAttempts = 0;
      renderPractice();
      if (result.fell_back_to_bank && result.rejection_reasons?.length) {
        console.info('[souly] generation fell back to bank:', result.rejection_reasons);
      }
    } catch (err) {
      pageEl('practice').innerHTML = Util.error(err.message, "App.go('learn')");
    }
  }

  function renderPractice() {
    const p = State.practice;
    const q = p.questions[p.index];
    State.currentQuestionId = q.id;
    State.hintTier = 0;
    State.wrongAttempts = 0;

    pageEl('practice').innerHTML = Pages.practice({
      question: q,
      index: p.index,
      total: p.total,
      lesson_title: p.lesson_title
    });
    pageEl('practice').classList.add('split');

    stepEnteredAt = Date.now();
    if (State.settings?.read_aloud) {
      setTimeout(() => Voice.speakElement('practicePrompt'), 400);
    }
  }

  async function answerPractice(index) {
    touch();
    const p = State.practice;
    const q = p.questions[p.index];

    document.querySelectorAll('#practiceOptions .quiz-option')
      .forEach(n => { n.style.pointerEvents = 'none'; });

    const seconds = stepEnteredAt ? Math.round((Date.now() - stepEnteredAt) / 1000) : 0;

    let result;
    try {
      result = await Api.checkAnswer(q.id, index, seconds, State.wrongAttempts);
    } catch (err) {
      toast(err.message, 'error');
      document.querySelectorAll('#practiceOptions .quiz-option')
        .forEach(n => { n.style.pointerEvents = ''; });
      return;
    }

    document.querySelectorAll('#practiceOptions .quiz-option').forEach((node, i) => {
      if (i === result.correct_index) node.classList.add('correct');
      else if (i === index) node.classList.add('wrong');
    });

    const feedback = el('practiceFeedback');
    if (feedback) {
      // On a wrong answer `explanation` is deliberately null — the answer is
      // not handed over, it is worked towards through the ladder. So show the
      // server's message instead of an empty box.
      const body = result.correct
        ? (result.explanation || '')
        : (result.message || "Let's look at it together.");
      feedback.innerHTML = `<div class="glass-card" style="margin-top:14px; background:${result.correct ? '#f0fdf4' : '#fef7ed'};">
        <div style="font-weight:800; color:${result.correct ? '#15803d' : '#b45309'}; margin-bottom:6px;">
          ${result.correct ? "✅ That's right" : "💭 Not quite — let's look again"}
        </div>
        <div style="font-size:14px; color:#4c1d95; font-weight:500; line-height:1.6;">${Util.esc(body)}</div>
      </div>`;
    }

    if (result.correct) {
      p.correct += 1;
      Voice.speak('That is right. ' + (result.explanation || ''));
      setTimeout(nextPracticeQuestion, 2200);
    } else {
      State.wrongAttempts += 1;
      touch();
      // A wrong answer routes into the ladder rather than just being marked
      // wrong. Souly offers the next clue without being asked — `next_tier`
      // climbs a rung per attempt, so a child who keeps missing it gets more
      // concrete help rather than the same nudge again.
      if (result.next_tier) State.hintTier = result.next_tier - 1;
      const state = el('soulyState');
      if (state) state.textContent = 'Let me give you a clue.';
      setTimeout(() => nextHint('souly'), 700);

      // Allow another attempt.
      document.querySelectorAll('#practiceOptions .quiz-option').forEach((node, i) => {
        if (i !== result.correct_index) node.classList.remove('wrong');
        node.style.pointerEvents = '';
      });
    }
  }

  async function nextHint(initiatedBy) {
    const tier = Math.min(4, (State.hintTier || 0) + 1);
    State.hintTier = tier;
    State.practice.hints_used += 1;

    const more = el('hintMore');
    if (more) { more.disabled = true; more.textContent = 'Thinking…'; }
    touch();
    Thread.typing();
    inFlight += 1;

    try {
      const result = await Api.hint(State.currentQuestionId, tier, {
        attempts_before: State.wrongAttempts || 0,
        initiated_by: initiatedBy || 'student'
      });

      Thread.addHTML(Pages.hintStep(tier, result.text));
      Voice.speak(result.text);

      if (more) {
        if (result.next_tier) {
          more.disabled = false;
          more.textContent = { 2: '💡 Show me an example',
                               3: '💡 Walk me through it',
                               4: '💡 Just tell me' }[result.next_tier] || '💡 More help';
        } else {
          more.style.display = 'none';
        }
      }
    } catch (err) {
      Thread.clearTyping();
      if (more) { more.disabled = false; more.textContent = '💡 Give me a clue'; }
      toast(err.message, 'error');
    } finally {
      inFlight -= 1;
      touch();
    }
  }

  function nextPracticeQuestion() {
    const p = State.practice;
    p.index += 1;
    if (p.index >= p.total) {
      pageEl('practice').innerHTML = Pages.practiceComplete(p);
      pageEl('practice').classList.remove('split');
      State.plan = { ...State.plan, quiz_done: true, current: 'game_done' };
      renderPlan();
      confetti();
      return;
    }
    renderPractice();
  }

  /* ---- Games / rewards --------------------------------------------------- */

  async function playGame(gameId) {
    await go('games');
    const game = State.games.games.find(g => g.id === gameId);
    if (!game) { toast('Game not found', 'error'); return; }
    pageEl('games').innerHTML = Util.loading('Starting ' + game.name + '…');
    try {
      await Games.play(game);
    } catch (err) {
      pageEl('games').innerHTML = Util.error(err.message, "App.go('games')");
    }
  }

  async function unlockReward(rewardId) {
    try {
      const result = await Api.unlockReward(rewardId);
      confetti();
      overlay(result.reward.icon, 'Unlocked', `${result.reward.name} for ${result.stars_spent} stars.`);
      if (State.profile) State.profile.stars = result.stars_remaining;
      syncCounters();
      renderRewards();
    } catch (err) {
      toast(err.status === 402 ? err.message : 'Could not unlock: ' + err.message,
            err.status === 402 ? '' : 'error');
    }
  }

  async function equipReward(rewardId) {
    try {
      await Api.equipReward(rewardId);
      toast('Done', 'star');
      const reward = State.rewards.rewards.find(r => r.id === rewardId);
      if (reward?.payload?.theme) await setSetting('theme', reward.payload.theme);
      renderRewards();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  /* ---- Boot -------------------------------------------------------------- */

  function checkOrientation() {
    const hint = el('rotateHint');
    if (!hint) return;
    const narrow = window.innerHeight > window.innerWidth && window.innerWidth < 620;
    hint.style.display = narrow ? 'flex' : 'none';
  }

  async function init() {
    Api.onConnectionChange(online => {
      if (!online) toast('Lost connection — I will keep trying.', 'error');
      else toast('Back online');
    });

    try { health = await Api.health(); } catch (_) { /* diagnostics only */ }

    // Sensible defaults so the sign-in screen can already speak and respect
    // reduced motion before we know whose settings to load.
    applySettings({ font_size: 'medium', theme: 'light', read_aloud: 1,
                    voice_volume: 70, language: 'en' });

    checkOrientation();
    window.addEventListener('resize', checkOrientation);
    window.addEventListener('orientationchange', checkOrientation);
    bindGlobalKeys();

    // Nothing loads until we know who is using the tablet.
    const resumed = await Gate.resume();
    if (!resumed) {
      if (Api.isSignedIn()) {
        // ?student= developer shortcut, no token.
        Gate.hide();
        await afterLogin();
      } else {
        await Gate.start();
      }
    }
  }

  /* Called by the sign-in screen once a child is through. */
  async function afterLogin() {
    try {
      applySettings(await Api.settings());
    } catch (_) { /* keep the defaults */ }

    showAvatar();

    // Come back to wherever they were, not to the top.
    const route = readRoute();
    if (route && route.page === 'lesson' && route.topicId) {
      await openLesson(route.topicId, route.pageNo);
      return;
    }
    await go(route ? route.page : 'home');
  }

  /* Their own face, top right, as the way into the profile. */
  async function showAvatar() {
    const btn = el('avatarBtn');
    if (!btn) return;
    btn.hidden = false;

    try {
      const profile = State.profile || await Api.profile();
      State.profile = profile;
      const face = el('avatarFace');
      if (!face) return;
      const url = profile.avatar_url;
      if (url && /^(https?:|\/|data:)/.test(url)) {
        btn.innerHTML = `<img src="${Util.esc(url)}" alt="">`;
      } else if (url) {
        face.textContent = url;      // the avatar is an emoji animal
      }
      btn.setAttribute('aria-label',
        `${profile.display_name || 'Your'} profile`);
    } catch (_) { /* the default face is fine */ }
  }

  function bindGlobalKeys() {
    document.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ' ') &&
          e.target.matches('[role="button"], .toggle')) {
        e.preventDefault();
        e.target.click();
      }
      if (e.key === 'Escape') closeOverlay();
    });

    el('starsOverlay').addEventListener('click', (e) => {
      if (e.target.id === 'starsOverlay') closeOverlay();
    });

    // A celebration should never outlast the moment it belongs to.
    setInterval(() => {
      const modal = el('starsOverlay');
      if (modal.classList.contains('active')) {
        const shownFor = Date.now() - (modal.dataset.shownAt || Date.now());
        if (shownFor > 12000) closeOverlay();
      }
    }, 2000);
  }

  async function signOut() {
    Voice.stopSpeaking();
    try { await Api.logout(); } catch (_) {}
    location.reload();
  }

  return {
    init, afterLogin, signOut,
    go, toast, syncCounters, closeOverlay, overlayIsOpen,
    celebrateBadges, celebrateLevel, renderPlan,
    openSubject, openLesson, goToPage, readStep,
    askHelp, askFree, soulySays, dismissOffer, touch, showAvatar,
    startPractice, answerPractice, nextHint,
    playGame, unlockReward, equipReward,
    setSetting, toggleSetting
  };
})();

document.addEventListener('DOMContentLoaded', App.init);
