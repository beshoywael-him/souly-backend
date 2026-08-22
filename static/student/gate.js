/* =============================================================================
   Souly — sign-in and the entry activity.

   Two flows that both run before the app proper:

     Gate      pick your face -> tap your three pictures
     Onboard   the getting-to-know-you activity, once

   Kept in its own file because neither has anything to do with tutoring, and
   because it is the only part of the app a child sees before they trust it.
   ============================================================================= */

const Gate = (() => {
  'use strict';

  const E = Util.esc;
  let profiles = [];
  let pictures = [];
  let passwordLength = 3;
  let chosen = null;         // the profile being signed into
  let picked = [];           // pictures tapped so far
  let mode = 'login';        // 'login' | 'setup'
  let confirming = null;     // first pass of a new password, awaiting repeat

  const el = (id) => document.getElementById(id);
  const gate = () => el('gate');

  function show(html) {
    gate().innerHTML = html;
    gate().classList.add('active');
  }

  function hide() { gate().classList.remove('active'); }

  /* ==========================================================================
     Screen 1 — who's using this tablet
     ========================================================================== */

  async function start() {
    show(`<div class="page-loading"><div class="spinner"></div><div>Loading…</div></div>`);
    try {
      const data = await Api.get('/api/auth/profiles');
      profiles = data.profiles;
      pictures = data.pictures;
      passwordLength = data.password_length;
      renderPicker();
    } catch (err) {
      show(`<div class="gate-title">Can't reach Souly</div>
            <div class="gate-hint">${E(err.message)}</div>
            <button class="btn-primary" onclick="Gate.start()">Try again</button>`);
    }
  }

  function renderPicker() {
    show(`
      <div class="gate-brand">Souly</div>
      <div class="gate-sub">Who's learning today?</div>
      <div class="profile-row" id="profileRow">
        ${profiles.map(p => `
          <button class="profile-tile ${p.locked_seconds ? 'locked' : ''}"
                  data-id="${E(p.external_id)}"
                  ${p.locked_seconds ? 'disabled' : `onclick="Gate.choose('${E(p.external_id)}')"`}
                  aria-label="${E(p.display_name)}">
            <div class="profile-face" style="background:${E(p.avatar_color)}22; border-color:${E(p.avatar_color)}33;">
              ${p.avatar}
            </div>
            <div class="profile-name">${E(p.display_name)}</div>
            <div class="profile-flag">
              ${p.locked_seconds ? `Locked for ${Math.ceil(p.locked_seconds / 60)} min`
                : p.needs_password ? 'Tap to set up' : ''}
            </div>
          </button>`).join('')}
      </div>
    `);
  }

  /* Netflix move: the others fade out, the chosen one grows. */
  function choose(extId) {
    chosen = profiles.find(p => p.external_id === extId);
    if (!chosen) return;

    const row = el('profileRow');
    row.classList.add('choosing');
    row.querySelector(`[data-id="${extId}"]`)?.classList.add('chosen');

    mode = chosen.needs_password ? 'setup' : 'login';
    picked = [];
    confirming = null;

    setTimeout(renderPassword, 620);
  }

  /* ==========================================================================
     Screen 2 — the picture password
     ========================================================================== */

  function renderPassword() {
    const setup = mode === 'setup';
    const repeat = setup && confirming;

    const title = setup
      ? (repeat ? 'Tap them again to be sure' : `Hi ${chosen.display_name}!`)
      : `Hi ${chosen.display_name}!`;

    const hint = setup
      ? (repeat
          ? 'Same three pictures, same order.'
          : `Choose three pictures. That's how you'll get in next time — so pick
             three you'll remember, like a little story.`)
      : 'Tap your three pictures.';

    show(`
      <div class="profile-face" style="background:${E(chosen.avatar_color)}22; width:88px; height:88px; font-size:46px; margin-bottom:16px;">
        ${chosen.avatar}
      </div>
      <div class="gate-title">${E(title)}</div>
      <div class="gate-hint">${E(hint)}</div>

      <div class="pic-slots" id="picSlots">
        ${Array.from({length: passwordLength}, (_, i) =>
          `<div class="pic-slot" data-slot="${i}"></div>`).join('')}
      </div>

      <div class="pic-grid">
        ${pictures.map(p => `
          <button class="pic-btn" data-code="${E(p.code)}"
                  onclick="Gate.tap('${E(p.code)}')"
                  aria-label="${E(p.label)}">${p.emoji}</button>`).join('')}
      </div>

      <div class="gate-error" id="gateError"></div>

      <div class="gate-actions">
        <button class="btn-secondary" onclick="Gate.back()">Not me</button>
        <button class="btn-secondary" onclick="Gate.clearPicks()">Start again</button>
      </div>
    `);
  }

  function tap(code) {
    if (picked.length >= passwordLength) return;
    picked.push(code);

    const pic = pictures.find(p => p.code === code);
    const slot = document.querySelector(`.pic-slot[data-slot="${picked.length - 1}"]`);
    if (slot) {
      // The slot shows a dot, not the picture. Someone standing behind the
      // child shouldn't be able to read their password off the screen.
      slot.textContent = '●';
      slot.classList.add('filled');
    }

    const btn = document.querySelector(`.pic-btn[data-code="${code}"]`);
    if (btn) {
      btn.classList.add('picked');
      btn.insertAdjacentHTML('beforeend',
        `<span class="pick-order">${picked.length}</span>`);
    }

    if (picked.length === passwordLength) setTimeout(submit, 350);
  }

  function clearPicks() {
    picked = [];
    document.querySelectorAll('.pic-slot').forEach(s => {
      s.textContent = ''; s.classList.remove('filled', 'error');
    });
    document.querySelectorAll('.pic-btn').forEach(b => {
      b.classList.remove('picked');
      b.querySelector('.pick-order')?.remove();
    });
    el('gateError').textContent = '';
  }

  function shakeSlots(message) {
    document.querySelectorAll('.pic-slot').forEach(s => s.classList.add('error'));
    el('gateError').textContent = message;
    setTimeout(clearPicks, 700);
  }

  async function submit() {
    const body = { student_ext_id: chosen.external_id, pictures: picked };

    // Setting up: ask for it twice. A child who mistypes their own password on
    // day one is locked out of their profile until a teacher resets it.
    if (mode === 'setup' && !confirming) {
      if (new Set(picked).size < passwordLength) {
        shakeSlots('Pick three different pictures.');
        return;
      }
      confirming = [...picked];
      picked = [];
      renderPassword();
      return;
    }

    if (mode === 'setup' && confirming) {
      if (confirming.join('|') !== picked.join('|')) {
        confirming = null;
        shakeSlots("Those didn't match. Let's try again from the start.");
        setTimeout(renderPassword, 800);
        return;
      }
    }

    try {
      const path = mode === 'setup' ? '/api/auth/set-password' : '/api/auth/login';
      const result = await Api.post(path, body);
      Api.setSession(result.token, result.student_ext_id);
      welcome(result);
    } catch (err) {
      if (err.status === 429) shakeSlots(err.message);
      else if (err.status === 401) shakeSlots("That's not quite right. Have another go.");
      else shakeSlots(err.message);
    }
  }

  /* ==========================================================================
     Screen 3 — greeted by name
     ========================================================================== */

  function welcome(result) {
    show(`
      <div class="profile-face" style="background:${E(chosen.avatar_color)}22; width:132px; height:132px; font-size:70px; margin-bottom:20px;">
        ${chosen.avatar}
      </div>
      <div class="gate-title">Hi ${E(result.display_name)}!</div>
      <div class="gate-hint">${result.needs_onboarding
        ? "Before we start, will you help me with something?"
        : "Good to see you. Let's carry on."}</div>
    `);

    if (State.settings?.read_aloud !== false) {
      Voice.browserSpeak(`Hi ${result.display_name}!`);
    }

    setTimeout(() => {
      if (result.needs_onboarding) Onboard.start();
      else { hide(); App.afterLogin(); }
    }, 1700);
  }

  function back() {
    chosen = null; picked = []; confirming = null;
    renderPicker();
  }

  async function resume() {
    /* Reload with a token still in hand: skip straight past the picker. */
    const token = Api.getToken();
    if (!token) return false;
    try {
      const me = await Api.get('/api/auth/me');
      Api.setSession(token, me.student_ext_id);
      if (me.needs_onboarding) { Onboard.start(); return true; }
      hide();
      return true;
    } catch (_) {
      Api.clearSession();
      return false;
    }
  }

  return { start, choose, tap, clearPicks, back, resume, hide };
})();


/* =============================================================================
   THE ENTRY ACTIVITY
   ============================================================================= */

const Onboard = (() => {
  'use strict';

  const E = Util.esc;
  let data = null;
  let queue = [];            // flattened list of screens
  let index = 0;
  let interests = [];
  let prefs = {};
  let itemState = null;      // per-item working state

  const el = (id) => document.getElementById(id);
  const gate = () => el('gate');

  function show(html) {
    gate().innerHTML = html;
    gate().classList.add('active');
  }

  function progressDots() {
    return `<div class="activity-progress">
      ${queue.map((_, i) =>
        `<div class="ap-dot ${i === index ? 'active' : (i < index ? 'done' : '')}"></div>`
      ).join('')}
    </div>`;
  }

  async function start() {
    show(`<div class="page-loading"><div class="spinner"></div><div>One moment…</div></div>`);
    try {
      data = await Api.get('/api/me/onboarding');
      buildQueue();
      renderIntro();
    } catch (err) {
      show(`<div class="gate-title">Something went wrong</div>
            <div class="gate-hint">${E(err.message)}</div>
            <button class="btn-primary" onclick="Onboard.skipAll()">Skip for now</button>`);
    }
  }

  function buildQueue() {
    queue = [{ kind: 'interests' }];
    data.reasoning_items.forEach(item => queue.push({ kind: 'reasoning', item }));
    data.modality_items.forEach(item => queue.push({ kind: 'modality', item }));
    data.preferences.forEach(q => queue.push({ kind: 'preference', q }));
    index = 0;
  }

  /* --- The map, before anything starts --------------------------------------
     Showing exactly what's coming is the highest-value screen in this flow.
     Unpredictability, not difficulty, is what drives anxiety here.
     ------------------------------------------------------------------------ */
  function renderIntro() {
    show(`
      <div class="gate-brand">Souly</div>
      <div class="gate-hint" style="max-width:34rem; font-size:16px; margin-top:16px;">
        ${E(data.intro)}
      </div>

      <div class="plan-cards">
        ${data.plan.map(p => `
          <div class="plan-card">
            <div class="pc-emoji">${p.emoji}</div>
            <div class="pc-label">${E(p.label)}</div>
            <div class="pc-count">${p.count} ${p.count === 1 ? 'thing' : 'things'}</div>
          </div>`).join('')}
      </div>

      <div class="gate-hint">About ${data.estimated_minutes} minutes. No marks, no timer.</div>

      <div class="gate-actions">
        <button class="btn-primary" onclick="Onboard.next()">I'm ready</button>
        <button class="btn-secondary" onclick="Onboard.skipAll()">Maybe later</button>
      </div>
    `);
    if (State.settings?.read_aloud !== false) Voice.browserSpeak(data.intro);
  }

  function next() {
    if (index >= queue.length) { finish(); return; }
    const screen = queue[index];
    if (screen.kind === 'interests') renderInterests();
    else if (screen.kind === 'reasoning') renderReasoning(screen.item);
    else if (screen.kind === 'modality') renderModality(screen.item);
    else renderPreference(screen.q);
  }

  function advance() { index += 1; next(); }

  /* --- Interests ------------------------------------------------------------ */

  function renderInterests() {
    show(`
      ${progressDots()}
      <div class="gate-title">What do you like?</div>
      <div class="gate-hint">Pick as many as you want. I'll use them in examples.</div>
      <div class="interest-grid">
        ${data.interests.map(i => `
          <button class="interest-btn ${interests.includes(i.code) ? 'picked' : ''}"
                  data-code="${E(i.code)}" onclick="Onboard.toggleInterest('${E(i.code)}')">
            <div class="ib-emoji">${i.emoji}</div>
            <div class="ib-label">${E(i.label)}</div>
          </button>`).join('')}
      </div>
      <div class="gate-actions">
        <button class="btn-primary" onclick="Onboard.saveInterests()">Next</button>
      </div>
      <button class="skip-link" onclick="Onboard.saveInterests()">Skip this</button>
    `);
  }

  function toggleInterest(code) {
    const i = interests.indexOf(code);
    if (i >= 0) interests.splice(i, 1); else interests.push(code);
    document.querySelector(`.interest-btn[data-code="${code}"]`)
      ?.classList.toggle('picked');
  }

  async function saveInterests() {
    try { await Api.post('/api/me/onboarding/interests', { interests }); }
    catch (_) { /* never block the child on a failed save */ }
    advance();
  }

  /* --- Reasoning items — the graduated prompts ------------------------------ */

  function renderReasoning(item) {
    itemState = {
      item, prompts: 0, attempts: 0,
      startedAt: Date.now(), firstAttemptMs: null,
    };

    const body = item.sequence
      ? `<div class="q-sequence">
           ${item.sequence.map(c => `
             <div class="q-cell ${c === '❓' ? 'unknown' : ''}">${c}</div>`).join('')}
         </div>`
      : `<div class="q-analogy">
           <div class="q-cell">${item.analogy.a}</div>
           <div class="q-arrow">→</div>
           <div class="q-cell">${item.analogy.b}</div>
           <div class="q-arrow" style="margin:0 10px;">and</div>
           <div class="q-cell">${item.analogy.c}</div>
           <div class="q-arrow">→</div>
           <div class="q-cell unknown">❓</div>
         </div>`;

    show(`
      ${progressDots()}
      <div class="gate-title">${E(item.question)}</div>
      <div class="gate-hint">Take as long as you like.</div>
      ${body}
      <div class="q-options" id="qOptions">
        ${item.options.map((o, i) => `
          <button class="q-option" data-i="${i}" onclick="Onboard.answer(${i})">${o}</button>`).join('')}
      </div>
      <div id="qPrompt"></div>
      <button class="skip-link" onclick="Onboard.skipItem()">Skip this one</button>
    `);
  }

  async function answer(chosenIndex) {
    if (!itemState) return;
    if (itemState.firstAttemptMs === null) {
      itemState.firstAttemptMs = Date.now() - itemState.startedAt;
    }
    itemState.attempts += 1;

    const buttons = document.querySelectorAll('#qOptions .q-option');
    buttons.forEach(b => { b.style.pointerEvents = 'none'; });

    let result;
    try {
      result = await Api.post('/api/me/onboarding/attempt', {
        item_code: itemState.item.code,
        answer_index: chosenIndex,
        prompts_used: itemState.prompts,
        first_attempt_ms: itemState.firstAttemptMs,
        total_ms: Date.now() - itemState.startedAt,
        attempts: itemState.attempts,
      });
    } catch (err) {
      advance();
      return;
    }

    if (result.correct) {
      buttons[chosenIndex]?.classList.add('correct');
      if (State.settings?.read_aloud !== false) Voice.browserSpeak(result.feedback);
      setTimeout(advance, 1100);
      return;
    }

    // Wrong. Climb one rung of the FIXED ladder — never a generated hint.
    buttons[chosenIndex]?.classList.add('wrong');

    if (itemState.prompts >= 4) { setTimeout(advance, 1400); return; }

    itemState.prompts += 1;
    try {
      const prompt = await Api.post('/api/me/onboarding/prompt', {
        item_code: itemState.item.code,
        tier: itemState.prompts,
      });
      el('qPrompt').innerHTML = `<div class="q-prompt">🤔 ${E(prompt.text)}</div>`;
      if (State.settings?.read_aloud !== false) Voice.browserSpeak(prompt.text);

      if (prompt.is_final) {
        // Rung 4 is the worked model. Show it, then move on — never leave a
        // child stuck on an item they can't do.
        setTimeout(advance, 4500);
      } else {
        buttons.forEach((b, i) => {
          if (i !== chosenIndex) b.style.pointerEvents = '';
        });
      }
    } catch (_) {
      advance();
    }
  }

  async function skipItem() {
    if (itemState) {
      try {
        await Api.post('/api/me/onboarding/skip', {
          item_code: itemState.item.code,
          total_ms: Date.now() - itemState.startedAt,
        });
      } catch (_) { /* skipping must never fail */ }
    }
    advance();
  }

  /* --- Reading vs listening ------------------------------------------------- */

  function renderModality(item) {
    itemState = { item, prompts: 0, attempts: 0,
                  startedAt: Date.now(), firstAttemptMs: null };

    const isListening = item.mode === 'listen';

    show(`
      ${progressDots()}
      <div class="gate-title">${isListening ? 'Have a listen' : 'Have a read'}</div>
      <div class="gate-hint">${isListening
        ? "I'll read this one to you. Tap to hear it again."
        : 'Read this, then answer the question.'}</div>

      ${isListening
        ? `<button class="btn-primary" style="margin:20px 0;" onclick="Onboard.replay()">
             🔊 Play it again
           </button>`
        : `<div class="reading-passage">${E(item.passage)}</div>`}

      <div class="gate-title" style="font-size:19px; margin-top:10px;">${E(item.question)}</div>
      <div class="q-options" id="qOptions">
        ${item.options.map((o, i) => `
          <button class="q-option text-option" data-i="${i}"
                  onclick="Onboard.answer(${i})">${E(o)}</button>`).join('')}
      </div>
      <button class="skip-link" onclick="Onboard.skipItem()">Skip this one</button>
    `);

    // The listening item is spoken and never shown — that is the whole point
    // of the comparison. If the text were on screen it would measure reading
    // twice.
    if (isListening) setTimeout(() => Voice.browserSpeak(item.passage), 500);
  }

  function replay() {
    if (itemState?.item?.passage) Voice.browserSpeak(itemState.item.passage);
  }

  /* --- Preferences ---------------------------------------------------------- */

  function renderPreference(q) {
    show(`
      ${progressDots()}
      <div class="gate-title">${E(q.question)}</div>
      <div class="gate-hint">You can change this any time.</div>
      <div class="pref-options">
        ${q.options.map((o, i) => `
          <button class="pref-btn" onclick="Onboard.pickPreference('${E(q.setting)}', ${JSON.stringify(o.value)})">
            <div class="pb-emoji">${o.emoji}</div>
            <div class="pb-label">${E(o.label)}</div>
          </button>`).join('')}
      </div>
      <button class="skip-link" onclick="Onboard.advance()">Skip this</button>
    `);
  }

  function pickPreference(setting, value) {
    prefs[setting] = value;
    advance();
  }

  /* --- Finish ---------------------------------------------------------------- */

  async function finish() {
    show(`<div class="page-loading"><div class="spinner"></div>
          <div>Thanks — putting that together…</div></div>`);

    try {
      if (Object.keys(prefs).length) {
        await Api.post('/api/me/onboarding/preferences', prefs);
      }
      const profile = await Api.post('/api/me/onboarding/finish', {});
      renderDone(profile);
    } catch (err) {
      renderDone(null);
    }
  }

  function renderDone(profile) {
    show(`
      <div style="font-size:70px;">🎉</div>
      <div class="gate-title">Thank you</div>
      <div class="gate-hint">That really helps. Here's what I picked up:</div>

      ${profile ? `<div class="profile-summary">${E(profile.summary)}</div>` : ''}

      <div class="gate-hint" style="font-size:13px;">
        I'll keep learning how you like things as we go.
      </div>

      <div class="gate-actions">
        <button class="btn-primary" onclick="Onboard.done()">Let's start</button>
      </div>
    `);
    if (profile && State.settings?.read_aloud !== false) {
      Voice.browserSpeak("Thank you, that really helps.");
    }
  }

  async function skipAll() {
    /* Bailing out is allowed and costs nothing. Souly falls back to the
       middle-of-the-road pitch until live tutoring tells it more. */
    try { await Api.post('/api/me/onboarding/finish', {}); } catch (_) {}
    done();
  }

  function done() {
    Gate.hide();
    App.afterLogin();
  }

  return { start, next, advance, toggleInterest, saveInterests, answer,
           skipItem, replay, pickPreference, skipAll, done };
})();
