/* =============================================================================
   Souly — page renderers.

   One function per screen. Each takes live API data and returns HTML.
   No page holds its own copy of a number; they all read from State, which is
   updated from award responses, so two screens can never disagree.

   The lesson screen is the centre of the app now. Everything else exists to
   get a child into it or to celebrate them coming out of it.
   ============================================================================= */

const Pages = (() => {
  'use strict';

  const E = Util.esc;

  /* --- Shared bits ---------------------------------------------------------- */

  function soulyFace(size) {
    const cls = size === 'big' ? '' : 'robot-mini';
    return `<div class="${cls}"><div class="robot-head"><div class="robot-face">
      <div class="robot-eye left"></div><div class="robot-eye right"></div>
      <div class="robot-smile"></div></div></div></div>`;
  }

  function topBar(icon, title, subtitle, backPage) {
    return `<div class="top-bar" style="padding:4px 0 10px;">
      <div class="top-bar-left">
        ${backPage ? `<button class="top-bar-back" onclick="App.go('${backPage}')" aria-label="Go back">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
        </button>` : ''}
        <div>
          <div class="top-bar-title">${icon} ${E(title)}</div>
          ${subtitle ? `<div class="top-bar-sub">${E(subtitle)}</div>` : ''}
        </div>
      </div>
    </div>`;
  }

  /* Split the body text into word spans so read-aloud can highlight along.
     Synchronised highlighting is what makes narration-plus-text help rather
     than compete: the two channels index each other. */
  /* Word spans drive the read-aloud highlight. Paragraphs are kept as
     paragraphs: the model is asked for three short chunks for a child with
     ADHD and it writes them, but this used to flatten the blank lines and
     serve the whole thing as one block — so the chunking was happening and
     nobody could see it. */
  function wordSpans(text) {
    const spanify = (chunk) => E(chunk)
      .split(/(\s+)/)
      .map(tok => (/^\s+$/.test(tok) ? tok : `<span class="w">${tok}</span>`))
      .join('');

    const paras = String(text || '')
      .split(/\n\s*\n|\n/)
      .map(t => t.trim())
      .filter(Boolean);

    if (paras.length <= 1) return spanify(text || '');
    return paras.map(pgh => `<p class="lesson-para">${spanify(pgh)}</p>`).join('');
  }

  /* ==========================================================================
     PLAN STRIP — the visual schedule, always on screen
     ========================================================================== */

  function planStrip(plan, profile) {
    if (!plan) return '';
    const item = (key, label, icon) => {
      const state = plan[key] ? 'done' : (plan.current === key ? 'current' : '');
      return `<span class="plan-item ${state}">
        <span>${plan[key] ? '✓' : icon}</span>
        <span class="plan-text">${label}</span>
      </span>`;
    };
    return `
      <span class="plan-label">Today</span>
      ${item('lesson_done', 'Lesson', '📘')}
      ${item('quiz_done', 'Practice', '✏️')}
      ${item('game_done', 'Game', '🎮')}
      <span class="plan-spacer"></span>
      ${profile ? `<span class="plan-stat" data-bind="stars">⭐ ${Util.num(profile.stars)}</span>
      <span class="plan-stat" data-bind="streak">🔥 ${profile.day_streak}</span>` : ''}
    `;
  }

  /* ==========================================================================
     HOME — start or resume a session
     ========================================================================== */

  function home(data) {
    const p = data.profile;
    const lesson = data.todays_lesson;

    const flagNotice = data.pending_flags > 0
      ? `<div class="notice-strip">👋 Ready when you are — we can pick up where you left off.</div>`
      : '';

    return `
      <div class="home-header" style="padding:6px 0 12px;">
        <div>
          <div class="greeting">${E(data.greeting)}</div>
          <div class="greeting-sub">${E(data.greeting_sub)}</div>
        </div>
      </div>

      ${flagNotice}

      <div class="glass-card">
        <div class="speech-wrap">
          ${soulyFace()}
          <div class="speech-bubble" id="homeSoulyMsg">${E(data.souly_message)}</div>
          <button class="speak-btn" onclick="Voice.speakElement('homeSoulyMsg', this)" aria-label="Read aloud">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3a4.5 4.5 0 00-2.5-4v8a4.5 4.5 0 002.5-4z"/></svg>
          </button>
        </div>
      </div>

      ${lesson ? `
      <div class="continue-card">
        <div class="continue-art">${lesson.icon || '📘'}</div>
        <div style="flex:1; min-width:0;">
          <div style="font-size:12px; color:#a78bfa; font-weight:700;">${lesson.pages_completed > 0 ? 'Carry on with' : 'Starting today'}</div>
          <div style="font-size:20px; font-weight:800; color:#4c1d95; margin:4px 0 10px;">${E(lesson.title)}</div>
          <div class="progress-bar-bg" style="margin-bottom:6px;">
            <div class="progress-bar-fill" style="width:${lesson.progress_pct}%; background:linear-gradient(90deg,#7C3AED,#A855F7);"></div>
          </div>
          <div style="font-size:12px; color:#a78bfa; font-weight:600;">${lesson.pages_completed} of ${lesson.total_pages} pages</div>
        </div>
        <button class="btn-primary" style="flex-shrink:0;" onclick="App.openLesson(${lesson.lesson_id})">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
          ${lesson.pages_completed > 0 ? 'Carry on' : 'Start'}
        </button>
      </div>` : Util.empty('📚', 'No lessons are loaded yet. Your teacher needs to add the curriculum.')}

      <div class="card-row" style="margin-top:16px;">
        <div class="qa-card" onclick="App.go('learn')" role="button" tabindex="0">
          <div class="qa-icon">📚</div><div class="qa-title">Learn</div><div class="qa-desc">Pick a topic</div>
        </div>
        <div class="qa-card" onclick="App.go('progress')" role="button" tabindex="0">
          <div class="qa-icon">📊</div><div class="qa-title">Progress</div><div class="qa-desc">How you're doing</div>
        </div>
        <div class="qa-card" onclick="App.go('games')" role="button" tabindex="0">
          <div class="qa-icon">🎮</div><div class="qa-title">Games</div><div class="qa-desc">After your lesson</div>
        </div>
        <div class="qa-card" onclick="App.go('achievements')" role="button" tabindex="0">
          <div class="qa-icon">🏆</div><div class="qa-title">Badges</div><div class="qa-desc">${p.badges_earned} earned</div>
        </div>
      </div>

      ${data.schedule && data.schedule.length ? `
      <div class="section-title-sm">Today's timetable</div>
      <div class="card-row">
        ${data.schedule.map(s => `
          <div style="display:flex; align-items:center; gap:12px; padding:12px 14px; background:#f5f3ff; border-radius:14px;">
            <span style="font-size:20px;">${s.icon}</span>
            <div style="font-size:13px; font-weight:700; color:#4c1d95;">
              ${Util.time12(s.time)} · ${E(s.label)}
            </div>
          </div>`).join('')}
      </div>` : ''}
    `;
  }

  /* ==========================================================================
     LEARN — subject rail + learning path
     ========================================================================== */

  function learn(data) {
    /* The empty state is written by the server, because "nothing is loaded"
       and "nothing for YOUR grade" are different sentences and only the
       server knows which is true. A grade 6 child right now has no books at
       all, and that has to read as an honest message rather than as a broken
       screen or a spinner that never stops. */
    const es = data.empty_state;
    const notice = es ? `<div class="notice-strip">📭 ${E(es.title)}</div>` : '';

    return `
      ${topBar('📚', 'Learn', 'Pick where you want to go', 'home')}
      ${notice}

      <div class="subject-rail" id="subjectRail" role="tablist">
        ${data.subjects.map(s => {
          const empty = !s.has_content;
          return `<button class="subject-chip ${empty ? 'empty' : ''}" data-code="${s.code}"
                    ${empty ? 'disabled' : `onclick="App.openSubject('${s.code}')"`}
                    role="tab" aria-selected="false">
            <span class="chip-ico">${s.icon}</span>
            <span>${E(s.name)}</span>
            <span class="chip-pct">${empty ? '—' : s.progress_pct + '%'}</span>
          </button>`;
        }).join('')}
      </div>

      <div id="pathArea">
        ${es
          ? Util.empty('🌱', es.message)
          : Util.empty('👆', 'Choose a subject to see its lessons.')}
      </div>
    `;
  }

  /* The plan of lessons ahead — the book's own sequence, made visible.

     Every card is a real lesson in a real Ministry book, and the page span
     under the title is not decoration: it is what makes "we teach the
     curriculum" checkable by anyone holding the book. */
  function learningPath(subjectName, lessons) {
    if (!lessons.length) return Util.empty('📭', `No lessons in ${subjectName} yet.`);

    const card = (l, index) => {
      const state = l.is_complete ? 'done' : (l.status === 'current' ? 'current' : '');
      const statusLabel = l.is_complete
        ? `<span class="path-status done">✓ Finished</span>`
        : l.status === 'current'
          ? `<span class="path-status current">${l.pages_completed > 0 ? 'Carry on' : 'Start here'}</span>`
          : `<span class="path-status todo">Coming up</span>`;

      const pageSpan = l.first_page === l.last_page
        ? `page ${l.first_page}`
        : `pages ${l.first_page}–${l.last_page}`;

      return `
        <div class="path-node">
          <button class="path-card ${state}" onclick="App.openLesson(${l.topic_id})"
                  aria-label="${E(l.title)}, ${l.is_complete ? 'finished' : l.progress_pct + ' percent done'}">
            <div class="path-art" style="background:linear-gradient(135deg,#f5f3ff,#ede9fe);"><span>📘</span></div>
            <div class="path-title">${E(l.title)}</div>
            <div class="path-sub">${l.page_count} ${l.page_count === 1 ? 'page' : 'pages'} · ${pageSpan}</div>
            <div class="path-foot">
              <div class="sub-bar" style="margin-bottom:8px;">
                <div class="sub-bar-fill" style="width:${l.progress_pct}%; background:linear-gradient(90deg,#7C3AED,#A855F7);"></div>
              </div>
              ${statusLabel}
            </div>
          </button>
        </div>
        ${index < lessons.length - 1
          ? `<div class="path-link ${l.is_complete ? 'done' : ''}"></div>` : ''}
      `;
    };

    const book = lessons[0].book_title || '';
    const unverified = lessons.some(l => !l.is_verified);

    return `
      <div class="section-title-sm">${E(subjectName)} — your path</div>
      ${book ? `<div class="path-book">From ${E(book)}</div>` : ''}
      ${unverified
        ? `<div class="notice-strip">⏳ Some of these are waiting for a teacher to check them.</div>`
        : ''}
      <div class="path">${lessons.map(card).join('')}</div>
    `;
  }

  /* ==========================================================================
     LESSON — the two-pane screen. This is the app.
     ========================================================================== */

  /* ==========================================================================
     THE SOULY THREAD

     One scrolling conversation, appended to. It used to be a single div that
     each reply overwrote, which meant a child could never see what they had
     just asked — so a follow-up felt like starting again, and on the practice
     screen a typed question produced no visible response at all because the
     element it wrote into did not exist there.
     ========================================================================== */

  function bubble(role, text, meta) {
    const cls = role === 'student' ? 'bubble student' : 'bubble souly';
    const pill = meta && meta.engine
      ? `<span class="engine-pill ${meta.engine === 'gemini' ? 'gemini' : 'fallback'}">${meta.engine === 'gemini' ? 'AI' : 'offline'}</span>`
      : '';
    // A picture the child asked for, arriving in the conversation where they
    // asked for it.
    const drawing = meta && meta.illustrationUrl
      ? `<div class="bubble-drawing"><img src="${E(meta.illustrationUrl)}"
           alt="${E(text)}" loading="lazy"
           onerror="this.closest('.bubble-drawing').remove()"></div>`
      : '';
    return `<div class="${cls}${meta && meta.offer ? ' offer' : ''}">${E(text)}${pill}${drawing}${
      meta && meta.actions ? meta.actions : ''}</div>`;
  }

  function soulyThread(intro) {
    return `<div class="souly-thread" id="soulyThread" aria-live="polite">
      ${bubble('souly', intro)}
    </div>`;
  }

  /* ==========================================================================
     THE VISUAL

     A picture with a job. The lesson screen used to show the scanned book
     page here, which was unreadable at pane size and was the source rather
     than the teaching.

     What the model returns is a SPEC, not a picture and not SVG — everything
     below is drawn here, in the app's own colours, at the app's own scale, so
     the numbers are always exactly what the page says and a diagram can never
     arrive malformed. `illustration` is the one kind that IS generated, and
     even then every word on it is drawn by us on top.
     ========================================================================== */

  const VIS_INK = '#4c1d95';
  const VIS_FILL = '#7C3AED';
  const VIS_SOFT = '#ede9fe';
  const VIS_MUTED = '#a78bfa';

  const DRAWN = {
    hundredths_grid: visGrid,
    place_value: visPlaceValue,
    number_line: visNumberLine,
    bar_compare: visBars,
    steps: visSteps,
    labelled_parts: visParts,
    cycle: visCycle,
  };

  /* What the drawn column is called. The heading is a promise about what is
     in the column, and for `steps` and `labelled_parts` that promise is the
     point: the column exists to say why each step is there, not to repeat the
     instruction the child has already read beside it. */
  const DIAGRAM_HEADING = {
    steps: 'Why each step',
    labelled_parts: 'What each part does',
    cycle: 'What happens at each stage',
    hundredths_grid: 'The number as a picture',
    place_value: 'Where each digit sits',
    number_line: 'Where the numbers sit',
    bar_compare: 'Side by side',
  };

  /* The picture, on its own, so the lesson can hang it above both columns.
     It is the widest thing on the page and every page gets one. */
  function visualPicture(spec, illustrationUrl) {
    if (!spec || !spec.scene || !illustrationUrl) return '';
    return `<div class="lesson-picture">
      ${visIllustration(spec, illustrationUrl)}
      ${spec.purpose
        ? `<p class="visual-purpose">${E(spec.purpose)}</p>` : ''}
    </div>`;
  }

  /* The drawn diagram, on its own, so it can sit in a column beside the
     words instead of stacked above them. */
  function visualDiagram(spec) {
    if (!spec) return '';
    const body = DRAWN[spec.kind];
    const drawn = body ? (body(spec) || '') : '';
    if (!drawn) return '';
    const heading = spec.title || DIAGRAM_HEADING[spec.kind] || 'A closer look';
    return `<figure class="visual visual-${spec.kind}">
      <figcaption class="visual-title">${E(heading)}</figcaption>
      <div class="visual-body">${drawn}</div>
    </figure>`;
  }

  /* Both together, stacked. Kept for anything that wants the old single
     block; the lesson screen places the two halves itself. */
  function visual(spec, illustrationUrl) {
    if (!spec) return '';
    return visualPicture(spec, illustrationUrl) + visualDiagram(spec);
  }

  /* Tenths and hundredths, the way this book teaches them: a whole cut into
     equal parts with some of them coloured in. */
  function visGrid(s) {
    const total = s.total === 10 ? 10 : 100;
    const cols = total === 10 ? 10 : 10;
    const rows = total / cols;
    const size = 30, gap = 2;
    const w = cols * size, h = rows * size;

    let cells = '';
    for (let i = 0; i < total; i++) {
      const x = (i % cols) * size, y = Math.floor(i / cols) * size;
      const on = i < (s.shaded || 0);
      cells += `<rect x="${x + gap / 2}" y="${y + gap / 2}"
        width="${size - gap}" height="${size - gap}" rx="4"
        fill="${on ? VIS_FILL : '#fff'}" stroke="${on ? VIS_FILL : VIS_SOFT}"
        stroke-width="2"><animate attributeName="opacity" from="0" to="1"
        dur="0.25s" begin="${Math.min(i, 40) * 0.012}s" fill="freeze"/></rect>`;
    }

    return `<svg viewBox="0 0 ${w} ${h}" class="visual-svg" role="img"
      aria-label="${s.shaded} out of ${total} squares coloured in">${cells}</svg>
      <div class="visual-readout">${s.shaded} out of ${total}</div>`;
  }

  /* The place-value chart the book itself uses, including the decimal point
     sitting between two columns rather than inside one. */
  function visPlaceValue(s) {
    const cols = s.columns || [];
    const after = Math.max(0, Math.min(cols.length, s.decimal_after || 0));

    const head = cols.map((c, i) =>
      `${i === after && after > 0 ? '<th class="pv-dot"></th>' : ''}
       <th class="${c.highlight ? 'on' : ''}">${E(c.place)}</th>`).join('');
    const digits = cols.map((c, i) =>
      `${i === after && after > 0 ? '<td class="pv-dot">.</td>' : ''}
       <td class="${c.highlight ? 'on' : ''}">${E(c.digit || '')}</td>`).join('');

    /* Wrapped, because this is the one diagram that has a floor on its own
       width: seven columns headed "Thousandths" cannot shrink to fit half a
       tablet pane, and shrinking the digits until they do would make the
       thing it teaches unreadable. So the digits stay legible and the chart
       scrolls inside its own box — which is a nuisance, but a local one. It
       never drags the rest of the page sideways with it. */
    return `<div class="pv-scroll" tabindex="0" role="group"
                 aria-label="Place value chart">
      <table class="pv-table"><thead><tr>${head}</tr></thead>
      <tbody><tr>${digits}</tr></tbody></table></div>`;
  }

  /* Where numbers sit next to each other — rounding, and comparing. */
  function visNumberLine(s) {
    const lo = s.min, hi = s.max, span = hi - lo;
    if (!(span > 0)) return '';
    const w = 640, h = 120, pad = 40;
    const at = (v) => pad + ((v - lo) / span) * (w - pad * 2);

    let ticks = '';
    if (s.step && s.step > 0 && span / s.step <= 40) {
      for (let v = lo; v <= hi + 1e-9; v += s.step) {
        ticks += `<line x1="${at(v)}" y1="54" x2="${at(v)}" y2="66"
                   stroke="${VIS_SOFT}" stroke-width="3" stroke-linecap="round"/>`;
      }
    }

    const marks = (s.marks || []).map((m, i) => {
      const x = at(m.value);
      const c = m.highlight ? VIS_FILL : VIS_MUTED;
      return `<g class="vis-mark" style="animation-delay:${i * 0.08}s">
        <line x1="${x}" y1="42" x2="${x}" y2="78" stroke="${c}" stroke-width="4"
              stroke-linecap="round"/>
        <circle cx="${x}" cy="60" r="${m.highlight ? 9 : 6}" fill="${c}"/>
        <text x="${x}" y="102" text-anchor="middle" font-size="17"
              font-weight="800" fill="${m.highlight ? VIS_INK : VIS_MUTED}"
        >${E(m.label || m.value)}</text></g>`;
    }).join('');

    return `<svg viewBox="0 0 ${w} ${h}" class="visual-svg" role="img"
      aria-label="A number line from ${lo} to ${hi}">
      <line x1="${pad}" y1="60" x2="${w - pad}" y2="60" stroke="${VIS_SOFT}"
            stroke-width="6" stroke-linecap="round"/>
      ${ticks}${marks}</svg>`;
  }

  function visBars(s) {
    const bars = s.bars || [];
    const max = Math.max(...bars.map(b => Math.abs(b.value) || 0), 1);
    return `<div class="vis-bars">${bars.map((b, i) => `
      <div class="vis-bar-row">
        <span class="vis-bar-label">${E(b.label)}</span>
        <span class="vis-bar-track">
          <span class="vis-bar-fill" style="width:${(Math.abs(b.value) / max) * 100}%;
                animation-delay:${i * 0.09}s"></span>
        </span>
        <span class="vis-bar-value">${E(b.value)}</span>
      </div>`).join('')}</div>`;
  }

  function visSteps(s) {
    return `<ol class="vis-steps">${(s.items || []).map((it, i) => `
      <li style="animation-delay:${i * 0.08}s">
        <span class="vis-step-num">${i + 1}</span>
        <span><strong>${E(it.label)}</strong>${it.note ? `<em>${E(it.note)}</em>` : ''}</span>
      </li>`).join('')}</ol>`;
  }

  function visParts(s) {
    return `<ul class="vis-parts">${(s.items || []).map((it, i) => `
      <li style="animation-delay:${i * 0.08}s">
        <span class="vis-dot"></span>
        <span><strong>${E(it.label)}</strong>${it.note ? `<em>${E(it.note)}</em>` : ''}</span>
      </li>`).join('')}</ul>`;
  }

  /* Something that goes round: photosynthesis, water, a life cycle. */
  function visCycle(s) {
    const items = s.items || [];
    const n = items.length;
    const w = 460, cx = w / 2, cy = 200, r = 128;

    const nodes = items.map((it, i) => {
      const a = (i / n) * Math.PI * 2 - Math.PI / 2;
      const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
      return `<g class="vis-mark" style="animation-delay:${i * 0.1}s">
        <circle cx="${x}" cy="${y}" r="34" fill="${VIS_SOFT}"
                stroke="${VIS_FILL}" stroke-width="3"/>
        <text x="${x}" y="${y + 5}" text-anchor="middle" font-size="15"
              font-weight="800" fill="${VIS_INK}">${i + 1}</text>
        <text x="${x}" y="${y + 56}" text-anchor="middle" font-size="14"
              font-weight="700" fill="${VIS_INK}">${E(it.label)}</text></g>`;
    }).join('');

    return `<svg viewBox="0 0 ${w} 400" class="visual-svg" role="img"
      aria-label="A cycle with ${n} stages">
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${VIS_SOFT}"
              stroke-width="5" stroke-dasharray="10 9" stroke-linecap="round"/>
      ${nodes}</svg>`;
  }

  /* The one kind that is really generated. The picture carries no words —
     everything written sits on top of it, in HTML, where it is exactly right
     and a screen reader can read it. */
  /* The generated picture. It drifts and breathes slightly rather than
     sitting dead on the page — cheap motion, no video, and it holds a child's
     eye the way a still image does not.

     While it is being drawn the frame shimmers, so the wait reads as
     something happening rather than as something broken. If it never arrives,
     the frame removes itself and any diagram underneath still stands. */
  function visIllustration(s, url) {
    if (!url) return '';

    /* A child who has asked for less movement is sent to the still, at the
       source — no point downloading an animation to hold it on frame one. */
    const still = window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const src = still ? url + (url.includes('?') ? '&' : '?') + 'still=1' : url;

    return `<div class="vis-illustration loading">
      <div class="vis-waiting" aria-hidden="true">
        <span class="vis-waiting-dot"></span>
        <span class="vis-waiting-dot"></span>
        <span class="vis-waiting-dot"></span>
      </div>
      <img id="lessonPicture" src="${E(src)}"
           alt="${E(s.purpose || s.scene || 'A picture for this lesson')}"
           onload="this.parentNode.classList.remove('loading')"
           onerror="console.warn('[souly] no picture — open this URL to see why:', this.src); this.closest('.vis-illustration').remove()">
    </div>`;
  }


  /* ==========================================================================
     THE LESSON STAGE — one picture, then two columns of equal width

     The old screen stacked three blocks of three different widths and three
     different alignments: a 460px picture centred, a 460px step list centred,
     and full-bleed left-aligned text. It read as three unrelated things and
     it was tall enough that a child had to scroll or zoom out to see any of
     it — which is exactly the load these children have least of to spend.

     So: one band across the top for the picture, and beneath it two columns
     of identical width, identical padding and identical alignment. Each has
     one job and says so in its heading.

       left   what the page has you DO, and why each part of it is there
       right  the same page said in words, written for this child

     They are deliberately not the same content twice. The left column is the
     shape of the task; the right is the telling of it. A child who can hold
     one of those can use it to get at the other, which is the whole reason
     for showing both.

     When there is no diagram the words take the full width rather than
     leaving a hole — an empty column is worse than one column.
     ========================================================================== */
  function lessonStage(view) {
    const spec = view.visual;
    const picture = visualPicture(spec, view.illustration_url);

    /* THE CURTAIN

       While the picture is still being drawn, the words below it are in the
       DOM but hidden, and Souly does not start reading. The picture is the
       explanation for these children and the sentences are the support, so
       showing the support first teaches them to skip the part that was built
       for them — and a child who has already read the text has no reason to
       look up when the picture finally lands.

       Hidden rather than absent, so a screen reader still has the whole page
       in order and nothing jumps when it lifts. app.js lifts it on load, on
       error, or on a timer — see revealWhenDrawn(). If there is no picture
       coming at all there is no curtain: nothing to wait for. */
    const curtained = picture ? ' curtained' : '';
    const diagram = visualDiagram(spec);
    const words = wordSpans(view.explanation);

    const chips = (view.adapted_for && view.adapted_for.length) ? `
      <div class="adapted-strip" aria-label="How this lesson was written for you">
        <span class="adapted-label">Written for you</span>
        ${view.adapted_for.map(a => `<span class="adapted-chip">${E(a)}</span>`).join('')}
      </div>` : '';

    const wordCol = `
      <section class="lesson-col lesson-col-words" aria-label="The lesson in words">
        <h3 class="lesson-col-head">In words</h3>
        ${chips}
        <div class="lesson-body" id="lessonBody">${words}</div>
      </section>`;

    if (!diagram) {
      return `<div class="lesson-stage${curtained}">${picture}
        <div class="lesson-duo solo">${wordCol}</div></div>`;
    }

    /* One diagram has a floor on its width. A place-value chart with the
       thousandths column in it is seven headed columns wide, and half a
       tablet pane cannot hold that at any size a child can read — the column
       clipped it, and the digit it clipped was the highlighted one the whole
       page is about. So a wide chart takes the full width and the words run
       underneath it. Losing the side-by-side layout on those pages is worth
       a great deal less than losing the digit. */
    const wide = spec.kind === 'place_value' && (spec.columns || []).length > 5;
    if (wide) {
      return `<div class="lesson-stage${curtained}">
        ${picture}
        <section class="lesson-col lesson-col-diagram" aria-label="How this page works">
          ${diagram}
        </section>
        <div class="lesson-duo solo">${wordCol}</div>
      </div>`;
    }

    return `<div class="lesson-stage${curtained}">
      ${picture}
      <div class="lesson-duo">
        <section class="lesson-col lesson-col-diagram" aria-label="How this page works">
          ${diagram}
        </section>
        ${wordCol}
      </div>
    </div>`;
  }

  function lesson(data, view, soulyState) {
    /* `view` is what GET /lessons/{id}/pages/{page} returned: the page image,
       and the explanation Souly wrote for THIS child from THIS page.

       The image is the canon — the Ministry's own printed page, which is what
       makes the content checkable. The text beside it is the rendition, and
       it is the only part that differs between two children on the same page. */
    if (!view) return Util.empty('📄', 'This lesson has no pages yet.');

    const total = data.total_pages || (data.pages || []).length;
    const ordinal = view.ordinal || 1;
    const isLast = ordinal >= total;

    return `
      ${topBar('📘', data.title, `${E(data.subject_name || '')} · page ${ordinal} of ${total}`, 'learn')}

      <div class="split-body">

        <!-- LEFT: the book page, then what Souly makes of it. -->
        <section class="pane pane-content" aria-label="Lesson content">
          <div class="glass-card" style="flex:1; display:flex; flex-direction:column;">
            ${lessonStage(view)}

            <div class="lesson-source">
              ${E(view.book_title || '')} · page ${view.page}
              ${view.cached ? '' : (view.engine === 'gemini'
                ? '<span class="engine-pill gemini">AI</span>'
                : '<span class="engine-pill fallback">offline</span>')}
            </div>

            <div class="step-dots" aria-label="Page ${ordinal} of ${total}">
              ${(data.pages || []).map((_, i) =>
                `<div class="step-dot ${i + 1 === ordinal ? 'active' : (i + 1 < ordinal ? 'done' : '')}"></div>`
              ).join('')}
            </div>

            <div class="step-nav">
              <button class="btn-secondary" onclick="App.goToPage(${ordinal - 1})"
                      ${ordinal === 1 ? 'disabled' : ''}>Back</button>
              <button class="btn-primary" onclick="App.goToPage(${ordinal + 1})">
                ${isLast ? 'Finish' : 'Next'}
              </button>
            </div>
          </div>
        </section>

        <!-- RIGHT: Souly. Never a separate screen. -->
        <aside class="pane pane-souly" aria-label="Souly">
          <div class="souly-head">
            ${soulyFace()}
            <div>
              <div class="souly-name">Souly</div>
              <div class="souly-state" id="soulyState">${soulyState || 'Here if you need me'}</div>
            </div>
            <div style="margin-left:auto;">
              <button class="speak-btn" onclick="App.readStep(this)" aria-label="Read the lesson aloud">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3a4.5 4.5 0 00-2.5-4v8a4.5 4.5 0 002.5-4z"/></svg>
              </button>
            </div>
          </div>

          ${soulyThread(data.souly_intro || "Tap a button below if any part of this is confusing. Asking is normal — I'd rather you asked.")}

          <div class="help-buttons">
            <button class="help-btn" onclick="App.askHelp('simpler')">
              <span class="help-ico">🤔</span><span>I don't get this</span>
            </button>
            <button class="help-btn" onclick="App.askHelp('example')">
              <span class="help-ico">💡</span><span>Show me an example</span>
            </button>
            <button class="help-btn" onclick="App.askHelp('another_way')">
              <span class="help-ico">🔄</span><span>Say it another way</span>
            </button>
          </div>

          <div class="voice-status" id="voiceStatus"></div>

          <div class="ask-row">
            <input class="input-field" id="askInput" placeholder="Or ask me anything…"
                   autocomplete="off" aria-label="Ask Souly a question"
                   oninput="App.touch()"
                   onkeydown="App.touch(); if(event.key==='Enter'){App.askFree()}">
            <button class="voice-btn" id="voiceBtn" onclick="Voice.toggle()" aria-label="Talk to Souly">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M12 14a3 3 0 003-3V5a3 3 0 00-6 0v6a3 3 0 003 3z"/><path d="M17 11a5 5 0 01-10 0H5a7 7 0 006 6.92V21h2v-3.08A7 7 0 0019 11z"/></svg>
            </button>
          </div>
        </aside>

      </div>
    `;
  }

  /* ==========================================================================
     PRACTICE — question left, hint ladder right
     ========================================================================== */

  function practice(state) {
    const q = state.question;
    if (!q) return Util.empty('🎉', 'No question loaded.');

    return `
      ${topBar('✏️', 'Practice', `${E(state.lesson_title || '')} · ${state.index + 1} of ${state.total}`, 'lesson')}

      <div class="split-body">

        <section class="pane pane-content" aria-label="Question">
          <div class="glass-card" style="flex:1; display:flex; flex-direction:column;">
            <div class="practice-origin">
              ${q.origin === 'generated'
                ? `<span class="gen">✨ Souly wrote this from your lesson</span>`
                : 'From the question bank'}
            </div>
            <div class="practice-question" id="practicePrompt">${E(q.prompt)}</div>

            <div id="practiceOptions" style="margin-top:14px;">
              ${q.options.map((opt, i) => `
                <button class="quiz-option" onclick="App.answerPractice(${i})">
                  <span class="opt-letter">${String.fromCharCode(65 + i)}</span>
                  <span>${E(opt)}</span>
                </button>`).join('')}
            </div>

            <div id="practiceFeedback"></div>

            <div class="step-dots" style="margin-top:auto;">
              ${Array.from({length: state.total}, (_, i) =>
                `<div class="step-dot ${i === state.index ? 'active' : (i < state.index ? 'done' : '')}"></div>`
              ).join('')}
            </div>
          </div>
        </section>

        <aside class="pane pane-souly" aria-label="Souly">
          <div class="souly-head">
            ${soulyFace()}
            <div>
              <div class="souly-name">Souly</div>
              <div class="souly-state" id="soulyState">Stuck? I'll give you a clue, not the answer.</div>
            </div>
          </div>

          <div class="hint-ladder souly-thread" id="soulyThread" aria-live="polite"></div>

          <button class="hint-more" id="hintMore" onclick="App.nextHint()">
            💡 Give me a clue
          </button>
          <div class="hint-free-note">Asking for help never costs you stars.</div>

          <div class="voice-status" id="voiceStatus"></div>
          <div class="ask-row">
            <input class="input-field" id="askInput" placeholder="Ask me something…"
                   autocomplete="off" aria-label="Ask Souly a question"
                   oninput="App.touch()"
                   onkeydown="App.touch(); if(event.key==='Enter'){App.askFree()}">
            <button class="voice-btn" id="voiceBtn" onclick="Voice.toggle()" aria-label="Talk to Souly">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M12 14a3 3 0 003-3V5a3 3 0 00-6 0v6a3 3 0 003 3z"/><path d="M17 11a5 5 0 01-10 0H5a7 7 0 006 6.92V21h2v-3.08A7 7 0 0019 11z"/></svg>
            </button>
          </div>
        </aside>

      </div>
    `;
  }

  function hintStep(tier, text) {
    const labels = { 1: '1', 2: '2', 3: '3', 4: '✓' };
    return `<div class="hint-step">
      <span class="hint-tier-badge">${labels[tier] || tier}</span>
      <div class="hint-text">${E(text)}</div>
    </div>`;
  }

  function practiceComplete(state) {
    const pct = state.total ? Math.round((state.correct / state.total) * 100) : 0;
    return `
      ${topBar('🎉', 'Practice done', E(state.lesson_title || ''), 'learn')}
      <div class="card-row">
        <div class="glass-card" style="text-align:center;">
          <div style="font-size:60px;">${pct >= 70 ? '🏆' : '💪'}</div>
          <div style="font-size:22px; font-weight:800; color:#4c1d95; margin-top:8px;">
            ${state.correct} of ${state.total}
          </div>
          <div style="font-size:13px; color:#a78bfa; font-weight:600; margin-top:4px;">
            ${state.hints_used ? `You used ${state.hints_used} clue${state.hints_used > 1 ? 's' : ''} — that's good learning.` : 'No clues needed!'}
          </div>
        </div>
        <div class="glass-card">
          <div class="speech-wrap">
            ${soulyFace()}
            <div class="speech-bubble">${E(
              pct >= 70 ? "Nice work. You've got this one."
              : "Good effort. Some of those were tricky — we can go through them again whenever you like."
            )}</div>
          </div>
          <div style="display:flex; gap:10px; margin-top:16px;">
            <button class="btn-secondary" style="flex:1;" onclick="App.go('learn')">Back to Learn</button>
            <button class="btn-primary" style="flex:1; justify-content:center;" onclick="App.go('games')">Play a game</button>
          </div>
        </div>
      </div>
    `;
  }

  /* ==========================================================================
     PROGRESS
     ========================================================================== */

  function progress(data) {
    return `
      ${topBar('📊', 'Progress', 'How you are getting on', 'home')}

      <div class="card-row">
        <div class="glass-card" style="text-align:center;">
          <div style="font-size:13px; color:#a78bfa; font-weight:700;">Level ${data.level.level} · ${E(data.level.title)}</div>
          <div class="circle-progress" style="margin-top:10px;">
            <svg width="120" height="120" viewBox="0 0 120 120">
              <circle class="circle-progress-bg" cx="60" cy="60" r="50"/>
              <circle class="circle-progress-fill" cx="60" cy="60" r="50" stroke-dasharray="314" stroke-dashoffset="${Util.ringOffset(data.overall_progress_pct)}"/>
            </svg>
            <div class="circle-progress-text">${data.overall_progress_pct}%</div>
          </div>
          <div style="display:flex; justify-content:center; gap:22px; margin-top:12px;">
            <div><div style="font-size:19px; font-weight:800; color:#7C3AED;">${Util.num(data.level.xp)}</div><div style="font-size:11px; color:#a78bfa; font-weight:600;">XP</div></div>
            <div><div style="font-size:19px; font-weight:800; color:#7C3AED;">⭐ ${Util.num(data.stars)}</div><div style="font-size:11px; color:#a78bfa; font-weight:600;">Stars</div></div>
            <div><div style="font-size:19px; font-weight:800; color:#7C3AED;">🔥 ${data.day_streak}</div><div style="font-size:11px; color:#a78bfa; font-weight:600;">Days</div></div>
          </div>
        </div>

        <div class="glass-card">
          <div class="glass-card-title">📅 This week</div>
          <div class="week-chart">
            ${data.week.map(d => `
              <div class="week-col ${d.is_today ? 'today' : ''} ${d.is_future ? 'future' : ''}">
                <div class="week-bar-track">
                  <div class="week-bar" style="height:${Math.max(d.height_pct, d.seconds > 0 ? 8 : 3)}%"></div>
                </div>
                <div class="week-label">${d.label}</div>
              </div>`).join('')}
          </div>
          <div style="display:flex; justify-content:space-around; margin-top:10px; text-align:center;">
            <div><div style="font-size:16px; font-weight:800; color:#7C3AED;">${data.time_spent.today_label}</div><div style="font-size:11px; color:#a78bfa; font-weight:600;">Today</div></div>
            <div><div style="font-size:16px; font-weight:800; color:#7C3AED;">${data.time_spent.week_label}</div><div style="font-size:11px; color:#a78bfa; font-weight:600;">This week</div></div>
          </div>
        </div>
      </div>

      <div class="card-row" style="margin-top:14px;">
        <div class="glass-card">
          <div class="glass-card-title">📚 Subjects</div>
          <div style="margin-top:14px;">
            ${data.subjects.map(s => `
              <div style="margin-bottom:14px;">
                <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
                  <span style="font-size:13px; font-weight:700; color:#4c1d95;">${s.icon} ${E(s.name)}</span>
                  <span style="font-size:13px; font-weight:800; color:#7C3AED;">${s.progress_pct}%</span>
                </div>
                <div class="progress-bar-bg">
                  <div class="progress-bar-fill" style="width:${s.progress_pct}%; background:linear-gradient(90deg,${s.color_from},${s.color_to});"></div>
                </div>
              </div>`).join('')}
          </div>
        </div>

        <div class="glass-card">
          <div class="glass-card-title">💡 Skills</div>
          <div style="margin-top:14px;">
            ${data.skills.map(s => `
              <div style="margin-bottom:12px;">
                <div style="display:flex; justify-content:space-between; margin-bottom:5px;">
                  <span style="font-size:13px; font-weight:700; color:#4c1d95;">${s.icon} ${E(s.name)}</span>
                  <span style="font-size:13px; font-weight:800; color:#7C3AED;">${s.level_pct}%</span>
                </div>
                <div class="progress-bar-bg"><div class="progress-bar-fill" style="width:${s.level_pct}%; background:linear-gradient(90deg,#7C3AED,#A855F7);"></div></div>
              </div>`).join('')}
          </div>
        </div>
      </div>
    `;
  }

  /* ==========================================================================
     GAMES / REWARDS / ACHIEVEMENTS — session-end moments, not destinations
     ========================================================================== */

  function games(data) {
    return `
      ${topBar('🎮', 'Games', 'A break after your lesson', 'home')}
      <div class="card-row">
        ${data.games.map(g => `
          <div class="game-card" onclick="App.playGame(${g.id})" role="button" tabindex="0">
            <div class="game-icon">${g.icon}</div>
            <div class="game-info">
              <div class="game-name">${E(g.name)}</div>
              <div class="game-desc">${E(g.description)}</div>
              <div class="game-meta">
                <span class="game-badge" style="background:#fef3c7; color:#92400e;">+${g.star_reward} ⭐</span>
                <span class="game-badge" style="background:#ede9fe; color:#7C3AED;">${E(g.difficulty)}</span>
                ${g.times_played ? `<span class="game-badge" style="background:#dcfce7; color:#15803d;">Best ${g.best_score}</span>` : ''}
              </div>
            </div>
          </div>`).join('')}
      </div>
    `;
  }

  function achievements(data, profile) {
    const badgeCard = (b) => `
      <div class="badge-card ${b.unlocked ? '' : 'locked'}">
        <div class="badge-icon">${b.unlocked ? b.icon : '🔒'}</div>
        <div class="badge-name">${E(b.name)}</div>
        <div class="badge-desc">${E(b.description)}</div>
        ${b.unlocked
          ? `<div class="badge-status" style="background:#dcfce7; color:#15803d;">Earned</div>`
          : `<div class="badge-progress-bar"><div class="badge-progress-fill" style="width:${b.progress_pct}%"></div></div>
             <div class="badge-desc" style="margin-top:5px;">${b.progress} / ${b.target}</div>`}
      </div>`;

    return `
      ${topBar('🏆', 'Badges', `${data.earned_count} of ${data.total_count}`, 'home')}
      <div class="badge-grid" style="grid-template-columns:repeat(auto-fill,minmax(180px,1fr));">
        ${data.badges.map(badgeCard).join('')}
      </div>
    `;
  }

  function rewards(data) {
    return `
      ${topBar('🎁', 'Rewards', `You have ${Util.num(data.stars)} stars`, 'profile')}
      <div class="reward-grid" style="grid-template-columns:repeat(auto-fill,minmax(190px,1fr));">
        ${data.rewards.map(r => `
          <div class="reward-card ${r.owned ? 'owned' : (r.affordable ? '' : 'unaffordable')}">
            <div class="reward-icon">${r.icon}</div>
            <div class="reward-name">${E(r.name)}</div>
            <div class="reward-cost">⭐ ${Util.num(r.cost_stars)}</div>
            ${r.owned
              ? `<button class="btn-secondary" style="width:100%; ${r.equipped ? 'background:#dcfce7; color:#15803d;' : ''}"
                         onclick="App.equipReward(${r.id})">${r.equipped ? 'In use ✓' : 'Use'}</button>`
              : `<button class="btn-secondary" style="width:100%;" onclick="App.unlockReward(${r.id})"
                         ${r.affordable ? '' : 'disabled'}>
                   ${r.affordable ? 'Unlock' : `Need ${Util.num(r.stars_needed)}`}
                 </button>`}
          </div>`).join('')}
      </div>
    `;
  }

  /* ==========================================================================
     PROFILE
     ========================================================================== */

  function profile(p, s, health) {
    const toggle = (key, label, icon) => `
      <div class="setting-item">
        <div class="setting-left">
          <div class="setting-icon">${icon}</div>
          <div class="setting-name">${label}</div>
        </div>
        <div class="toggle ${s[key] ? 'on' : ''}" role="switch" aria-checked="${!!s[key]}"
             aria-label="${label}" tabindex="0" onclick="App.toggleSetting('${key}', this)">
          <div class="toggle-knob"></div>
        </div>
      </div>`;

    const segment = (key, options) => `
      <div class="seg-group">
        ${options.map(([value, label]) =>
          `<button class="seg-btn ${s[key] === value ? 'active' : ''}"
                   onclick="App.setSetting('${key}', '${value}')">${label}</button>`).join('')}
      </div>`;

    return `
      ${topBar('👤', 'Profile', E(p.full_name), 'home')}

      <div class="card-row">
        <div class="glass-card" style="text-align:center;">
          <div style="font-size:52px;">🧒</div>
          <div style="font-size:19px; font-weight:800; color:#4c1d95; margin-top:6px;">${E(p.display_name)}</div>
          <div style="font-size:13px; color:#a78bfa; font-weight:600;">Level ${p.level} · ${E(p.level_title)}</div>
          <div style="display:flex; justify-content:space-around; margin-top:16px;">
            <div><div style="font-size:18px; font-weight:800; color:#7C3AED;">${Util.num(p.stars)}</div><div style="font-size:11px; color:#a78bfa; font-weight:600;">Stars</div></div>
            <div><div style="font-size:18px; font-weight:800; color:#7C3AED;">${p.badges_earned}</div><div style="font-size:11px; color:#a78bfa; font-weight:600;">Badges</div></div>
            <div><div style="font-size:18px; font-weight:800; color:#7C3AED;">${p.lessons_completed}</div><div style="font-size:11px; color:#a78bfa; font-weight:600;">Lessons</div></div>
          </div>
          <button class="btn-secondary" style="width:100%; margin-top:16px;" onclick="App.go('rewards')">🎁 Rewards shop</button>
        </div>

        <div class="glass-card">
          <div class="glass-card-title">🔌 System</div>
          <div style="margin-top:12px; font-size:12px; font-weight:600; color:#a78bfa; line-height:2.1;">
            <div>AI brain: ${health && health.integrations.llm_gemini ? '🟢 Gemini connected' : '🟡 Offline mode'}</div>
            <div>Speech to text: ${health && health.integrations.stt_elevenlabs ? '🟢 ElevenLabs' : '🟡 Not configured'}</div>
            <div>Voice: ${health && health.integrations.tts ? '🟢 ' + health.tts_provider : '🟡 Browser voice'}</div>
            <div>Lessons loaded: ${health && health.curriculum ? health.curriculum.verified_lessons : '—'}</div>
          </div>
        </div>
      </div>

      <div class="section-title-sm">Preferences</div>
      <div class="card-row">
        <div>
          <div class="setting-item">
            <div class="setting-left"><div class="setting-icon">🔤</div><div class="setting-name">Text size</div></div>
            ${segment('font_size', [['small', 'S'], ['medium', 'M'], ['large', 'L']])}
          </div>
          <div class="setting-item">
            <div class="setting-left"><div class="setting-icon">🎨</div><div class="setting-name">Theme</div></div>
            ${segment('theme', [['light', 'Light'], ['purple', 'Calm'], ['dark', 'Dark']])}
          </div>
          <div class="setting-item">
            <div class="setting-left"><div class="setting-icon">🔊</div><div class="setting-name">Voice volume</div></div>
            <input type="range" class="range-slider" min="0" max="100" value="${s.voice_volume}"
                   aria-label="Voice volume" onchange="App.setSetting('voice_volume', Number(this.value))">
          </div>
          <div class="setting-item">
            <div class="setting-left"><div class="setting-icon">🌐</div><div class="setting-name">Language</div></div>
            ${segment('language', [['en', 'EN'], ['ar', 'AR'], ['fr', 'FR']])}
          </div>
        </div>

        <div>
          ${toggle('read_aloud', 'Read text aloud', '🗣️')}
          ${toggle('high_contrast', 'High contrast', '◐')}
          ${toggle('larger_buttons', 'Bigger buttons', '🔲')}
          ${toggle('reduce_motion', 'Less movement', '🌊')}
          ${toggle('closed_captions', 'Captions', '💬')}
          ${toggle('voice_commands', 'Voice commands', '🎤')}
        </div>
      </div>
    `;
  }

  return {
    planStrip, home, learn, learningPath, lesson, soulyThread, bubble, visual,
    practice, hintStep, practiceComplete,
    progress, games, achievements, rewards, profile,
    soulyFace, topBar,
  };
})();
