/* =============================================================================
   The classroom screen: sign in, poll, draw, act.

   Two decisions worth knowing about before reading the code.

   POLLING, NOT PUSH. Flags arrive by asking every two seconds rather than
   over a socket. Our latency requirement is "on the teacher's screen within
   about five seconds", and a two-second poll meets it with room to spare
   while adding no connection state to lose when the router blinks. When a
   live push layer lands, only `startPolling` changes.

   THE SCREEN NEVER RE-DRAWS ITSELF UNDER THE TEACHER'S FINGER. A refresh
   that arrives while a card is being acted on is held until the action
   finishes. Nothing is worse than a button moving as somebody reaches for it,
   and in a classroom the teacher is not looking at the screen while they tap.
   ============================================================================= */

const App = (() => {
  'use strict';

  const POLL_MS = 2000;
  const FRESH_S = 30;          // a flag this new is highlighted

  let timer = null;
  let busy = false;            // an approve/dismiss is in flight
  let lastGood = 0;            // when the last successful poll landed
  let latest = null;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* ------------------------------------------------------------------ words */

  /* Every flag type gets a plain-English sentence. A teacher reading this
     between sentences should not have to translate `prolonged_inactivity`. */
  const PHRASE = {
    gaze_away: 'looked away from the board',
    head_turn: 'turned away from the board',
    absent: 'not in their seat',
    prolonged_inactivity: 'has not moved on for a while',
    repeated_error: 'kept getting the same thing wrong',
    help_requested: 'asked for help',
  };

  const STATE_WORD = {
    attending: 'settled',
    drifting: 'drifted recently',
    flagged: 'needs you',
    settled: 'settled',
  };

  function ago(seconds) {
    if (seconds == null) return '';
    if (seconds < 60) return seconds + 's ago';
    if (seconds < 3600) return Math.round(seconds / 60) + 'm ago';
    return Math.round(seconds / 3600) + 'h ago';
  }

  function seconds(ms) {
    if (!ms) return '';
    return (ms / 1000).toFixed(1) + 's';
  }

  function toast(message) {
    const el = $('toast');
    el.textContent = message;
    el.hidden = false;
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.hidden = true; }, 2600);
  }

  /* ------------------------------------------------------------------ gate */

  function showGate(message) {
    $('app').hidden = true;
    $('gate').classList.add('active');
    const err = $('gate-error');
    if (message) { err.textContent = message; err.hidden = false; }
    else { err.hidden = true; }
    stopPolling();
  }

  function showApp() {
    $('gate').classList.remove('active');
    $('app').hidden = false;
  }

  async function signIn(event) {
    event.preventDefault();
    const button = $('gate-submit');
    const email = $('gate-email').value.trim();
    const password = $('gate-password').value;
    if (!email || !password) return;

    button.disabled = true;
    button.textContent = 'Checking…';
    try {
      await Api.login(email, password);
      $('gate-password').value = '';
      showApp();
      await refresh();
      startPolling();
    } catch (e) {
      showGate(e.message || 'That did not work.');
    } finally {
      button.disabled = false;
      button.textContent = 'Open the classroom';
    }
  }

  async function signOut() {
    stopPolling();
    await Api.logout();
    showGate(null);
  }

  /* --------------------------------------------------------------- drawing */

  function renderCounts(counts) {
    const cells = [
      ['pending', 'waiting for you'],
      ['approved', 'approved today'],
      ['dismissed', 'dismissed today'],
      ['done', 'worked through at home'],
      ['total', 'detections today'],
    ];
    $('counts').innerHTML = cells.map(([key, label]) =>
      `<div class="count"><b>${counts[key] || 0}</b><span>${label}</span></div>`
    ).join('');
  }

  function renderQueue(queue) {
    const box = $('queue');
    $('queue-count').textContent = queue.length
      ? `${queue.length} waiting` : '';

    if (!queue.length) {
      box.innerHTML = `
        <div class="empty">
          <strong>Nothing needs you right now.</strong>
          The camera is watching. Anything it notices appears here within a
          few seconds.
        </div>`;
      return;
    }

    box.innerHTML = queue.map(f => {
      const fresh = (f.seconds_ago != null && f.seconds_ago < FRESH_S) ? ' fresh' : '';
      /* 'none' is a real value in the database meaning no support profile is
         recorded. Printing it as a badge says something about a child that
         nobody entered. */
      const support = (f.support_profile && f.support_profile !== 'none')
        ? `<span class="chip chip-support">${esc(f.support_profile.replace(/_/g, ' '))}</span>`
        : '';
      /* Without a topic the flag still matters to the teacher; it just
         cannot start a lesson at home by itself, and saying so is more
         useful than leaving the sentence hanging. */
      const topic = f.topic_title
        ? `during ${esc(f.topic_title)}`
        : '— no lesson was set on the camera';
      const conf = f.confidence != null
        ? `confidence ${Math.round(f.confidence * 100)}%` : 'no confidence given';

      return `
      <article class="flagcard${fresh}" data-flag="${f.id}" data-name="${esc(f.student_name)}">
        <div class="face" style="background:${esc(f.avatar_color || '#F1EAFE')}22">
          ${esc(f.avatar || '🙂')}
        </div>
        <div class="flagbody">
          <h3>${esc(f.student_name)} ${support}</h3>
          <div class="flagwhat">
            <span class="chip chip-flag">${esc(f.flag_type.replace(/_/g, ' '))}</span>
            ${esc(PHRASE[f.flag_type] || f.flag_type)} ${esc(topic)}
          </div>
          <div class="flagmeta">
            ${f.duration_ms ? `<span>${esc(seconds(f.duration_ms))} of drift</span>` : ''}
            <span>${esc(conf)}</span>
            <span>${esc(ago(f.seconds_ago))}</span>
          </div>
        </div>
        <div class="actions">
          <button class="btn-approve" data-act="approved" data-flag="${f.id}">Approve</button>
          <button class="btn-dismiss" data-act="dismissed" data-flag="${f.id}">Dismiss</button>
        </div>
      </article>`;
    }).join('');
  }

  function renderRoster(roster) {
    $('roster-note').textContent = `${roster.length} children`;
    $('roster').innerHTML = roster.map(c => `
      <div class="child ${esc(c.state)}">
        <div class="face" style="background:${esc(c.avatar_color || '#F1EAFE')}22">
          ${esc(c.avatar || '🙂')}
        </div>
        <div>
          <div class="child-name">${esc(c.display_name)}</div>
          <div class="child-state">${esc(STATE_WORD[c.state] || c.state)}</div>
        </div>
      </div>`).join('');
  }

  function renderHandled(handled) {
    const box = $('handled');
    if (!handled.length) {
      box.innerHTML = `<div class="empty">Nothing yet today.</div>`;
      return;
    }
    box.innerHTML = handled.map(h => `
      <div class="handled-row">
        <span class="st st-${esc(h.status)}">${esc(h.status.replace(/_/g, ' '))}</span>
        <span>${esc(h.student_name)}</span>
        <span class="ago">${esc(ago(h.seconds_ago))}</span>
      </div>`).join('');
  }

  function renderLive() {
    const el = $('live');
    const text = $('live-text');
    const age = lastGood ? Math.round((Date.now() - lastGood) / 1000) : null;

    el.classList.remove('stale', 'down');
    if (!Api.State.online) {
      el.classList.add('down');
      text.textContent = 'offline — retrying';
    } else if (age == null) {
      text.textContent = 'connecting…';
    } else if (age > 12) {
      el.classList.add('stale');
      text.textContent = `last update ${age}s ago`;
    } else {
      text.textContent = 'live';
    }
  }

  function render(board) {
    $('who-name').textContent = board.teacher.full_name || '';
    $('who-initials').textContent = board.teacher.initials || '?';
    renderCounts(board.counts);
    renderQueue(board.queue);
    renderRoster(board.roster);
    renderHandled(board.handled);
  }

  /* --------------------------------------------------------------- polling */

  async function refresh() {
    /* Never re-draw while the teacher is mid-tap. The next poll picks it up
       two seconds later, which nobody notices; a button moving under a
       finger is something everybody notices. */
    if (busy) return;
    try {
      latest = await Api.board();
      lastGood = Date.now();
      render(latest);
    } catch (e) {
      if (e.status === 401) { showGate('Session expired. Sign in again.'); return; }
      /* Anything else: leave the last good screen up. Stale and labelled
         stale is more useful mid-lesson than empty. */
    } finally {
      renderLive();
    }
  }

  function startPolling() {
    stopPolling();
    timer = setInterval(refresh, POLL_MS);
  }

  function stopPolling() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  /* --------------------------------------------------------------- actions */

  async function act(flagId, status, button) {
    busy = true;
    const card = document.querySelector(`.flagcard[data-flag="${flagId}"]`);
    card && card.querySelectorAll('button').forEach(b => { b.disabled = true; });
    button.textContent = status === 'approved' ? 'Approving…' : 'Dismissing…';

    try {
      await Api.review(flagId, status);
      const name = (card && card.dataset.name) || 'That flag';
      toast(status === 'approved'
        ? `Approved — ${name} will get this topic again at home tonight.`
        : `Dismissed — nothing will be sent home.`);
      card && card.remove();
    } catch (e) {
      if (e.status === 401) { showGate('Session expired. Sign in again.'); return; }
      toast(e.message || 'That did not go through.');
      card && card.querySelectorAll('button').forEach(b => { b.disabled = false; });
      button.textContent = status === 'approved' ? 'Approve' : 'Dismiss';
    } finally {
      busy = false;
      refresh();
    }
  }

  function onClick(event) {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    act(Number(button.dataset.flag), button.dataset.act, button);
  }

  /* ------------------------------------------------------------------ boot */

  async function init() {
    $('gate-form').addEventListener('submit', signIn);
    $('sign-out').addEventListener('click', signOut);
    $('queue').addEventListener('click', onClick);

    /* A laptop lid closing pauses the polling; opening it refreshes at once,
       so a teacher never reads a screen that stopped ten minutes ago. */
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { stopPolling(); }
      else if (Api.State.token) { refresh(); startPolling(); }
    });

    setInterval(renderLive, 1000);

    try {
      const resumed = await Api.resume();
      if (resumed) {
        showApp();
        await refresh();
        startPolling();
        return;
      }
    } catch (e) { /* fall through to the gate */ }
    showGate(null);
  }

  return { init };
})();

document.addEventListener('DOMContentLoaded', App.init);
