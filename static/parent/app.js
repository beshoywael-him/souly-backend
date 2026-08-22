/* =============================================================================
   app.js — routing, every user action, and the render loop.

   Same shape as static/student/app.js: one `go()` that fetches and draws, and
   one delegated click handler at the document level rather than inline
   onclick attributes. Delegation matters here because pages.js rebuilds whole
   screens as HTML strings — anything bound directly to an element would be
   thrown away on the next render.
   ============================================================================= */

const App = (() => {

  /* ---------------------------------------------------------------------------
     UI state. Not data — data is fetched fresh on every navigation.
     ------------------------------------------------------------------------- */
  const ui = {
    page: 'home',
    subject: null,        // subject code, when the Subjects tab is drilled in
    subjectName: null,    // ...and its name, so the header can say it
    thread: null,         // open conversation id
    switcherOpen: false,
    menuOpen: false,
    busy: false,
  };

  const el = {
    boot:    () => document.getElementById('boot'),
    gate:    () => document.getElementById('gate'),
    shell:   () => document.getElementById('shell'),
    sidebar: () => document.getElementById('sidebar'),
    header:  () => document.getElementById('header'),
    page:    () => document.getElementById('page'),
    scrim:   () => document.getElementById('scrim'),
    modal:   () => document.getElementById('modal'),
    toasts:  () => document.getElementById('toasts'),
  };

  const TITLES = {
    home:         (c) => ['Home', `How ${c} is getting on`],
    progress:     (c) => ['Progress', `${c}'s learning over time`],
    subjects:     (c) => ['Subjects', `Every subject ${c} is studying`],
    subject:      (c) => [ui.subjectName || 'Subject', `${c}'s work in ${ui.subjectName || 'this subject'}`],
    notes:        (c) => ['Teacher Notes', `What ${c}'s teachers have written`],
    messages:     ( ) => ['Messages', 'Talk to your child\'s teachers'],
    achievements: (c) => ['Achievements', `Badges ${c} has earned`],
    support:      (c) => ['Support Profile', `How Souly adapts for ${c}`],
  };

  /* ===========================================================================
     Toasts
     ========================================================================= */

  function toast(message, kind = 'info') {
    const node = document.createElement('div');
    node.className = `toast ${kind}`;
    node.textContent = message;
    el.toasts().appendChild(node);
    setTimeout(() => {
      node.style.transition = 'opacity .3s, transform .3s';
      node.style.opacity = '0';
      node.style.transform = 'translateX(120%)';
      setTimeout(() => node.remove(), 320);
    }, 3200);
  }

  /* ===========================================================================
     Boot
     ========================================================================= */

  async function init() {
    document.addEventListener('click', onClick);
    document.addEventListener('submit', onSubmit);
    document.addEventListener('keydown', onKey);

    try {
      const session = await Api.resume();
      if (session && Api.State.child) {
        showApp();
        await go('home');
        return;
      }
    } catch (e) {
      // An expired token is the normal case here, not an error worth showing.
      if (!(e instanceof Api.ApiError) || e.status !== 401) {
        console.warn('resume failed', e);
      }
    }
    showGate();
  }

  function showGate(errorMessage) {
    el.boot().classList.add('hidden');
    el.shell().classList.add('hidden');
    const gate = el.gate();
    gate.classList.remove('hidden');
    gate.innerHTML = Pages.gate(errorMessage);
    const input = document.getElementById('gate-code');
    if (input) input.focus();
  }

  function showApp() {
    el.boot().classList.add('hidden');
    el.gate().classList.add('hidden');
    el.shell().classList.remove('hidden');
  }

  /* ===========================================================================
     Render
     ========================================================================= */

  function drawChrome() {
    const child = Api.currentChild();
    const badges = child
      ? { notes: child.unread_notes, messages: child.unread_messages }
      : {};

    el.sidebar().innerHTML = Pages.sidebar({
      page: ui.page === 'subject' ? 'subjects' : ui.page,
      child,
      children: Api.State.children,
      switcherOpen: ui.switcherOpen,
      badges,
    });
    el.sidebar().classList.toggle('open', ui.menuOpen);
    el.scrim().classList.toggle('hidden', !ui.menuOpen);

    const name = child ? child.display_name : '';
    const [title, subtitle] = (TITLES[ui.page] || TITLES.home)(name);
    el.header().innerHTML = Pages.header(title, subtitle, Api.State.parent);
  }

  /* The one navigation function. Fetches what the screen needs, then draws. */
  async function go(page, arg) {
    if (ui.busy) return;
    ui.busy = true;
    ui.page = page;
    ui.switcherOpen = false;
    ui.menuOpen = false;

    if (page === 'subject') {
      if (arg && arg !== ui.subject) ui.subjectName = null;
      ui.subject = arg || ui.subject;
    } else {
      ui.subjectName = null;
    }
    if (page !== 'messages') ui.thread = null;

    drawChrome();
    el.page().innerHTML = Pages.loading();
    el.page().classList.remove('page-enter');

    try {
      const html = await renderPage(page);
      el.page().innerHTML = html;
      el.page().classList.add('page-enter');
      document.getElementById('main').scrollTop = 0;
      scrollThreadToEnd();
    } catch (e) {
      if (e instanceof Api.ApiError && e.status === 401) {
        showGate('Your session ended. Please sign in again.');
        return;
      }
      el.page().innerHTML = Pages.errorBox(e.message || 'Could not load this screen.');
    } finally {
      ui.busy = false;
    }
  }

  async function renderPage(page) {
    switch (page) {
      case 'home':         return Pages.home(await Api.overview());
      case 'progress':     return Pages.progress(await Api.progress());
      case 'subjects':     return Pages.subjects(await Api.subjects());
      case 'subject': {
        const data = await Api.subject(ui.subject);
        // The header is drawn before the fetch, so it says "Subject" for one
        // frame. Set the real name and redraw the chrome once we have it.
        ui.subjectName = data.subject.name;
        drawChrome();
        return Pages.subjectDetail(data);
      }
      case 'notes': {
        const data = await Api.notes();
        // Opening the tab is what marks them read — a parent who has the notes
        // on screen has been told. Fire and forget: a failed mark should not
        // stop the notes rendering.
        markVisibleNotesRead(data.notes);
        return Pages.notes(data);
      }
      case 'achievements': return Pages.achievements(await Api.achievements());
      case 'support':      return Pages.support(await Api.support());
      case 'messages': {
        const list = await Api.conversations();
        // Land on the thread with something unread, then the most recent.
        if (!ui.thread && list.conversations.length) {
          const unread = list.conversations.find(c => c.unread > 0);
          ui.thread = (unread || list.conversations[0]).id;
        }
        const thread = ui.thread ? await Api.conversation(ui.thread) : null;
        refreshBadgesSoon();
        return Pages.messages(list, thread);
      }
      default:             return Pages.home(await Api.overview());
    }
  }

  async function markVisibleNotesRead(notes) {
    const unread = notes.filter(n => !n.read);
    if (!unread.length) return;
    try {
      await Promise.all(unread.map(n => Api.markNoteRead(n.id)));
      refreshBadgesSoon();
    } catch (e) { /* the badge will correct itself on the next load */ }
  }

  /* Unread counts live on the children list, so refresh it after anything
     that could change them and redraw the sidebar. */
  let badgeTimer = null;
  function refreshBadgesSoon() {
    clearTimeout(badgeTimer);
    badgeTimer = setTimeout(async () => {
      try {
        await Api.refreshChildren();
        drawChrome();
      } catch (e) { /* not worth interrupting anyone over */ }
    }, 400);
  }

  function scrollThreadToEnd() {
    const body = document.getElementById('thread-body');
    if (body) body.scrollTop = body.scrollHeight;
  }

  /* ===========================================================================
     Switching child
     -------------------------------------------------------------------------
     Everything on screen is about one child, so switching re-fetches the
     current screen rather than dropping the parent back to Home. If Fayrouz
     is comparing her two sons' progress, being bounced to Home on every
     switch would make that impossible.

     The one exception is a subject drill-down: Atef is in Grade 4 and Beshoy
     in Grade 5, so the subject that was open may not exist for the sibling.
     That case steps back to the subject list instead of 404-ing.
     ------------------------------------------------------------------------- */
  async function switchChild(extId) {
    if (extId === Api.State.child) { ui.switcherOpen = false; drawChrome(); return; }
    Api.rememberChild(extId);
    ui.thread = null;
    const child = Api.currentChild();
    toast(`Now showing ${child ? child.display_name : 'your child'}`, 'info');
    await go(ui.page === 'subject' ? 'subjects' : ui.page);
  }

  /* ===========================================================================
     Events
     ========================================================================= */

  async function onClick(event) {
    const target = (sel) => event.target.closest(sel);

    // --- chrome -------------------------------------------------------------
    const nav = target('[data-nav]');
    if (nav) { await go(nav.dataset.nav); return; }

    if (target('[data-switcher]')) {
      ui.switcherOpen = !ui.switcherOpen;
      drawChrome();
      return;
    }

    const switchTo = target('[data-switch-to]');
    if (switchTo) { await switchChild(switchTo.dataset.switchTo); return; }

    // A click anywhere else closes the switcher.
    if (ui.switcherOpen && !target('.switcher')) {
      ui.switcherOpen = false;
      drawChrome();
    }

    const action = target('[data-action]');
    if (action) {
      switch (action.dataset.action) {
        case 'menu':
          ui.menuOpen = !ui.menuOpen;
          drawChrome();
          return;
        case 'logout':
          await Api.logout();
          ui.page = 'home';
          showGate();
          return;
        case 'new-thread':
          await openTeacherPicker();
          return;
        case 'close-modal':
          el.modal().innerHTML = '';
          return;
      }
    }

    if (event.target.id === 'scrim' || target('#scrim')) {
      ui.menuOpen = false;
      drawChrome();
      return;
    }

    // --- page content -------------------------------------------------------
    const subject = target('[data-subject]');
    if (subject) { await go('subject', subject.dataset.subject); return; }

    const thread = target('[data-thread]');
    if (thread) {
      ui.thread = Number(thread.dataset.thread);
      await go('messages');
      return;
    }

    const teacher = target('[data-teacher]');
    if (teacher) { await startThread(Number(teacher.dataset.teacher)); return; }

    const toggle = target('[data-setting]');
    if (toggle) { await flipSetting(toggle); return; }

    const font = target('[data-font]');
    if (font) { await setFont(font.dataset.font); return; }
  }

  async function onSubmit(event) {
    if (event.target.id === 'gate-form') {
      event.preventDefault();
      await signIn();
      return;
    }
    if (event.target.id === 'reply-form') {
      event.preventDefault();
      await sendReply();
      return;
    }
  }

  function onKey(event) {
    if (event.key !== 'Escape') return;
    if (el.modal().innerHTML) { el.modal().innerHTML = ''; return; }
    if (ui.switcherOpen) { ui.switcherOpen = false; drawChrome(); }
    if (ui.menuOpen) { ui.menuOpen = false; drawChrome(); }
  }

  /* ===========================================================================
     Actions
     ========================================================================= */

  async function signIn() {
    const input = document.getElementById('gate-code');
    const button = document.getElementById('gate-submit');
    const code = (input.value || '').trim();
    if (!code) return;

    button.disabled = true;
    button.textContent = 'Checking…';
    try {
      await Api.login(code);
      if (!Api.State.children.length) {
        showGate('That code works, but no children are linked to it yet. Ask the school office.');
        return;
      }
      showApp();
      await go('home');
      const child = Api.currentChild();
      toast(`Welcome back, ${Api.State.parent.full_name}`, 'success');
      if (Api.State.children.length > 1 && child) {
        // Say which child is showing, once, so a parent of two is never
        // reading one son's week under the other's name.
        setTimeout(() => toast(`Showing ${child.display_name} — tap the name to switch`, 'info'), 900);
      }
    } catch (e) {
      showGate(e.message || 'That did not work. Try again.');
    }
  }

  async function sendReply() {
    const input = document.getElementById('reply-input');
    const body = (input.value || '').trim();
    if (!body || !ui.thread) return;

    input.value = '';
    try {
      await Api.sendMessage(ui.thread, body);
      await go('messages');
    } catch (e) {
      input.value = body;   // give them their words back
      toast(e.message || 'Message not sent.', 'error');
    }
  }

  async function openTeacherPicker() {
    try {
      const data = await Api.teachers();
      el.modal().innerHTML = Pages.teacherPicker(data.teachers);
    } catch (e) {
      toast(e.message || 'Could not load the teacher list.', 'error');
    }
  }

  async function startThread(teacherId) {
    try {
      const result = await Api.startConversation(teacherId);
      el.modal().innerHTML = '';
      ui.thread = result.id;
      await go('messages');
      if (result.created) toast('Conversation started.', 'success');
    } catch (e) {
      toast(e.message || 'Could not start the conversation.', 'error');
    }
  }

  /* Optimistic: flip the switch first, then save. A toggle that waits for a
     round trip before moving feels broken on a phone. If the save fails we
     redraw from the server, which puts it back. */
  async function flipSetting(button) {
    const key = button.dataset.setting;
    const next = !button.classList.contains('on');
    button.classList.toggle('on', next);
    button.setAttribute('aria-checked', next ? 'true' : 'false');
    try {
      await Api.saveSettings({ [key]: next ? 1 : 0 });
      toast('Saved — this applies on every screen.', 'success');
    } catch (e) {
      toast(e.message || 'Could not save that.', 'error');
      await go('support');
    }
  }

  async function setFont(size) {
    try {
      await Api.saveSettings({ font_size: size });
      await go('support');
      toast('Text size updated.', 'success');
    } catch (e) {
      toast(e.message || 'Could not save that.', 'error');
    }
  }

  return { init, go, toast };
})();

document.addEventListener('DOMContentLoaded', App.init);
