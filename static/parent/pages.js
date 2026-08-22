/* =============================================================================
   pages.js — one function per screen. All the HTML in this app lives here.

   Every function takes data the server already sent and returns an HTML
   string. None of them fetch, and none of them hold state: app.js owns
   routing and actions, api.js owns the network. That split is the same one
   static/student/ uses, so a teammate who has worked on the student app can
   read this file without being told anything.

   EVERYTHING FROM THE SERVER IS ESCAPED. Teacher notes and messages are typed
   by people. `esc()` on every interpolation, without exception — including
   the ones that "obviously" cannot contain markup, because those are the ones
   that quietly stop being true.
   ============================================================================= */

const Pages = (() => {

  /* ===========================================================================
     Small helpers
     ========================================================================= */

  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmtDate(iso) {
    if (!iso) return '';
    const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
    if (isNaN(d)) return '';
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }

  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
    if (isNaN(d)) return '';
    return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }

  function ago(iso) {
    if (!iso) return '';
    const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
    if (isNaN(d)) return '';
    const mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
    const days = Math.round(hours / 24);
    if (days === 1) return 'yesterday';
    if (days < 7) return `${days} days ago`;
    return fmtDate(iso);
  }

  /* Colour a percentage the same way everywhere. */
  function fillFor(pct) {
    if (pct === null || pct === undefined) return 'fill-grey';
    if (pct >= 75) return 'fill-green';
    if (pct >= 45) return 'fill-purple';
    if (pct > 0)   return 'fill-amber';
    return 'fill-grey';
  }

  function robot(size = 60) {
    return `
      <svg width="${size}" height="${size}" viewBox="0 0 100 100" aria-hidden="true">
        <defs>
          <linearGradient id="rb-${size}" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#8b5cf6"/><stop offset="100%" stop-color="#7c3aed"/>
          </linearGradient>
          <linearGradient id="rh-${size}" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#a78bfa"/><stop offset="100%" stop-color="#8b5cf6"/>
          </linearGradient>
        </defs>
        <ellipse cx="50" cy="88" rx="25" ry="6" fill="#e5e7eb" opacity=".35"/>
        <rect x="30" y="45" width="40" height="38" rx="14" fill="url(#rb-${size})"/>
        <rect x="35" y="20" width="30" height="30" rx="12" fill="url(#rh-${size})"/>
        <circle cx="42" cy="33" r="5" fill="#fff"/><circle cx="42" cy="33" r="2.5" fill="#1a1a3e"/>
        <circle cx="58" cy="33" r="5" fill="#fff"/><circle cx="58" cy="33" r="2.5" fill="#1a1a3e"/>
        <rect x="44" y="40" width="12" height="4" rx="2" fill="#fbbf24"/>
        <rect x="22" y="55" width="10" height="22" rx="5" fill="#c4b5fd"/>
        <rect x="68" y="55" width="10" height="22" rx="5" fill="#c4b5fd"/>
        <circle cx="27" cy="78" r="5" fill="#8b5cf6"/><circle cx="73" cy="78" r="5" fill="#8b5cf6"/>
        <rect x="38" y="12" width="8" height="10" rx="2" fill="#a78bfa"/>
        <circle cx="42" cy="10" r="4" fill="#fbbf24"/>
        <rect x="54" y="12" width="8" height="10" rx="2" fill="#a78bfa"/>
        <circle cx="58" cy="10" r="4" fill="#fbbf24"/>
      </svg>`;
  }

  const ICONS = {
    home:   '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    chart:  '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    book:   '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
    note:   '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
    award:  '<circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/>',
    msg:    '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    heart:  '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
    chevron:'<polyline points="6 9 12 15 18 9"/>',
    left:   '<polyline points="15 18 9 12 15 6"/>',
    menu:   '<line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/>',
    x:      '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    out:    '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    clock:  '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    check:  '<polyline points="20 6 9 17 4 12"/>',
    up:     '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
    down:   '<polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  };

  function icon(name, size = 18) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round"
      stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
  }

  function loading(message = 'Loading…') {
    return `<div class="loading"><div class="spinner"></div>${esc(message)}</div>`;
  }

  function errorBox(message) {
    return `<div class="error-box"><strong>Something went wrong.</strong><br>${esc(message)}</div>`;
  }

  function empty(emoji, title, body) {
    return `<div class="empty">
      <span class="emoji">${emoji}</span>
      <h4>${esc(title)}</h4>
      <p>${esc(body)}</p>
    </div>`;
  }

  /* ===========================================================================
     Sign-in
     ========================================================================= */

  function gate(errorMessage) {
    return `
      <div class="gate-card">
        <div class="robot-float" style="display:flex;justify-content:center">${robot(72)}</div>
        <h1>Parents' Hub</h1>
        <p>Enter the access code your child's school gave you.</p>
        ${errorMessage ? `<div class="gate-error">${esc(errorMessage)}</div>` : ''}
        <form id="gate-form" autocomplete="off">
          <input class="input" id="gate-code" name="code" placeholder="SOULY-XXXX-XXXX"
                 autocapitalize="characters" autocorrect="off" spellcheck="false"
                 aria-label="Access code" required>
          <button class="btn btn-primary" type="submit" id="gate-submit"
                  style="width:100%;margin-top:14px">Open the hub</button>
        </form>
        <p class="gate-help">
          One code per family — it opens every child linked to you.<br>
          Lost it? Ask the school office; they can issue a new one.
        </p>
      </div>`;
  }

  /* ===========================================================================
     Sidebar, including the child switcher
     ========================================================================= */

  const NAV = [
    { id: 'home',         label: 'Home',            icon: 'home'  },
    { id: 'progress',     label: 'Progress',        icon: 'chart' },
    { id: 'subjects',     label: 'Subjects',        icon: 'book'  },
    { id: 'notes',        label: 'Teacher Notes',   icon: 'note',  badge: 'notes' },
    { id: 'messages',     label: 'Messages',        icon: 'msg',   badge: 'messages' },
    { id: 'achievements', label: 'Achievements',    icon: 'award' },
    { id: 'support',      label: 'Support Profile', icon: 'shield' },
  ];

  function sidebar(state) {
    const child = state.child;
    const badges = state.badges || {};

    const items = NAV.map(item => {
      let badge = '';
      if (item.badge === 'notes' && badges.notes > 0) {
        badge = `<span class="nav-badge">${badges.notes}</span>`;
      }
      if (item.badge === 'messages' && badges.messages > 0) {
        badge = `<span class="nav-badge">${badges.messages}</span>`;
      }
      return `
        <button class="nav-item ${state.page === item.id ? 'active' : ''}"
                data-nav="${item.id}">
          ${icon(item.icon)}
          <span class="nav-label">${esc(item.label)}</span>
          ${badge}
        </button>`;
    }).join('');

    return `
      <div class="sidebar-brand">
        <div class="robot-float">${robot(42)}</div>
        <div>
          <h1>Souly</h1>
          <p>Parents' Hub</p>
        </div>
      </div>

      ${switcher(state)}

      <nav class="nav">${items}</nav>

      <div class="sidebar-foot">
        <div class="sidebar-card">
          <div class="robot-float" style="display:flex;justify-content:center">${robot(64)}</div>
          <p>${child
              ? `Souly is here to support ${esc(child.display_name)} every step of the way.`
              : 'Souly is here to help.'}</p>
          <div style="display:flex;justify-content:center;margin-top:8px;color:#f87171">
            ${icon('heart', 14)}
          </div>
        </div>
      </div>`;
  }

  /* ---------------------------------------------------------------------------
     The switcher.

     With two or more children this is a dropdown listing all of them, each
     with its own unread dot. With one child it is the same plate with no
     chevron and no click target — a control that does nothing is worse than
     no control, because a parent will tap it and conclude the app is broken.
     ------------------------------------------------------------------------- */
  function switcher(state) {
    const child = state.child;
    if (!child) return '';

    const many = state.children.length > 1;
    const unread = child.unread_total > 0;

    const plate = `
      <button class="switcher-btn ${many ? '' : 'static'}"
              ${many ? 'data-switcher="toggle" aria-haspopup="listbox"' : 'tabindex="-1" aria-hidden="true"'}
              aria-expanded="${state.switcherOpen ? 'true' : 'false'}">
        <span class="switcher-avatar" style="background:${esc(child.avatar_color)}22">
          ${esc(child.avatar || '🙂')}
        </span>
        <span class="switcher-meta">
          <span class="switcher-name">${esc(child.display_name)}</span>
          <span class="switcher-sub">Grade ${esc(child.grade)}${many ? ' · tap to switch' : ''}</span>
        </span>
        ${unread ? '<span class="switcher-dot"></span>' : ''}
        ${many ? icon('chevron', 15) : ''}
      </button>`;

    const menu = (many && state.switcherOpen) ? `
      <div class="switcher-menu" role="listbox">
        ${state.children.map(c => `
          <button class="switcher-option ${c.external_id === child.external_id ? 'current' : ''}"
                  role="option" aria-selected="${c.external_id === child.external_id}"
                  data-switch-to="${esc(c.external_id)}">
            <span class="switcher-avatar" style="background:${esc(c.avatar_color)}22">
              ${esc(c.avatar || '🙂')}
            </span>
            <span class="switcher-meta">
              <span class="switcher-name">${esc(c.display_name)}</span>
              <span class="switcher-sub">Grade ${esc(c.grade)}${
                c.unread_total ? ` · ${c.unread_total} new` : ''}</span>
            </span>
            ${c.unread_total ? '<span class="switcher-dot"></span>' : ''}
          </button>`).join('')}
      </div>` : '';

    return `<div class="switcher">${plate}${menu}</div>`;
  }

  /* ===========================================================================
     Header
     ========================================================================= */

  function header(title, subtitle, parent) {
    const initials = parent ? parent.initials : '';
    return `
      <div class="header">
        <div class="flex gap">
          <button class="menu-btn" data-action="menu" aria-label="Menu">${icon('menu')}</button>
          <div>
            <h2>${esc(title)}</h2>
            ${subtitle ? `<p>${esc(subtitle)}</p>` : ''}
          </div>
        </div>
        <div class="header-actions">
          <div class="avatar-circle" title="${esc(parent ? parent.full_name : '')}">${esc(initials)}</div>
          <button class="icon-btn" data-action="logout" aria-label="Sign out"
                  title="Sign out">${icon('out')}</button>
        </div>
      </div>`;
  }

  /* ===========================================================================
     Shared blocks
     ========================================================================= */

  function stat({ icon: emoji, tint, value, label, pill, pillClass, pct, fill }) {
    return `
      <div class="stat">
        <div class="stat-top">
          <div class="stat-icon ${tint || 'tint-purple'}">${emoji}</div>
          ${pill ? `<span class="pill ${pillClass || 'pill-grey'}">${esc(pill)}</span>` : ''}
        </div>
        <p class="stat-value">${value}</p>
        <p class="stat-label">${esc(label)}</p>
        ${pct !== undefined && pct !== null ? `
          <div class="track thin" style="margin-top:12px">
            <div class="fill ${fill || fillFor(pct)}" style="width:${Math.max(0, Math.min(100, pct))}%"></div>
          </div>` : ''}
      </div>`;
  }

  /* A week of time-on-task as bars. Minutes, not a score: with the mastery
     table as sparse as it is, a score chart would be four points of noise,
     and "he worked on four days this week" is a thing a parent can act on. */
  function weekChart(days) {
    const peak = Math.max(1, ...days.map(d => d.minutes));
    const total = days.reduce((sum, d) => sum + d.minutes, 0);
    const worked = days.filter(d => d.minutes > 0).length;
    const summary = worked
      ? `${total} minutes across ${worked} day${worked === 1 ? '' : 's'}`
      : 'No lessons opened yet this week';
    return `
      <p class="small muted" style="margin-bottom:14px">${esc(summary)}</p>
      <div class="bar-chart">
        ${days.map(d => `
          <div class="bar-col" title="${esc(d.label)}: ${d.minutes} min">
            <div class="bar-shell">
              <div class="bar-value ${d.is_today ? 'today' : ''}"
                   style="height:${d.is_future ? 0 : Math.round(d.minutes / peak * 100)}%"></div>
            </div>
            <span class="bar-label" style="${d.is_today ? 'color:#10b981;font-weight:700' : ''}">
              ${esc(d.label)}
            </span>
          </div>`).join('')}
      </div>`;
  }

  function subjectRows(subjects, { clickable = true } = {}) {
    return subjects.map(s => `
      <div class="subject-row" ${clickable ? `data-subject="${esc(s.code)}" style="cursor:pointer"` : ''}>
        <div class="row-icon" style="background:${esc(s.color_from)}1a">${esc(s.icon)}</div>
        <div class="subject-meta">
          <div class="subject-line">
            <span class="subject-name">${esc(s.name)}</span>
            <span class="subject-pct" style="color:${esc(s.color_from)}">
              ${s.has_data ? s.progress_pct + '%' : '<span class="faint small">not started</span>'}
            </span>
          </div>
          <div class="track">
            <div class="fill ${s.has_data ? fillFor(s.progress_pct) : 'fill-grey'}"
                 style="width:${s.has_data ? s.progress_pct : 0}%"></div>
          </div>
          <p class="small faint" style="margin-top:5px">
            ${s.topics_started} of ${s.topic_count} lesson${s.topic_count === 1 ? '' : 's'} started
            ${s.seconds ? ` · ${esc(s.time_label)}` : ''}
          </p>
        </div>
      </div>`).join('');
  }

  /* ---------------------------------------------------------------------------
     The independence card.

     This is the one number in the hub that a generic school portal does not
     have, and it is the one a parent of a child with a learning difference
     actually asks about: is he needing less help than he was?

     Falling is good. The arrow and the colour are inverted from every other
     trend on the page for that reason, and the card says so in words, because
     a green down-arrow with no explanation reads as a warning.
     ------------------------------------------------------------------------- */
  function independenceCard(ind, childName) {
    if (!ind.has_data) {
      return `<div class="card">
        <div class="card-head"><h3>Working independently</h3></div>
        ${empty('🌱', 'Nothing measured yet',
          `Once ${childName} has worked through a few pages, this will show how often Souly was asked for help — and whether that is going down.`)}
      </div>`;
    }

    let verdict;
    if (!ind.comparable) {
      // No activity last week means the comparison is between a real number
      // and an absence. Saying "up from 0" would report a child as struggling
      // when what actually happened is that last week did not exist.
      verdict = `<span class="pill pill-grey">First week of data</span>`;
    } else if (ind.improving) {
      verdict = `<span class="pill pill-green">${icon('down', 12)} Needing less help</span>`;
    } else if (ind.change === 0) {
      verdict = `<span class="pill pill-blue">Steady</span>`;
    } else {
      verdict = `<span class="pill pill-amber">${icon('up', 12)} Asking more often</span>`;
    }

    const types = Object.entries(ind.by_type || {});
    const LABELS = {
      nudge: 'a gentle nudge',
      simpler: 'a simpler explanation',
      example: 'a worked example',
      another_way: 'a different approach',
      free_question: 'a question of their own',
      hint: 'a hint',
    };

    return `
      <div class="card">
        <div class="card-head">
          <h3>Working independently</h3>
          ${verdict}
        </div>

        <p class="stat-value" style="font-size:36px">${ind.help_this_week}</p>
        <p class="muted" style="margin-bottom:8px">
          time${ind.help_this_week === 1 ? '' : 's'} ${esc(childName)} asked Souly for help this week
        </p>
        ${ind.comparable
          ? `<p class="small muted">Last week: ${ind.help_last_week}. Fewer is better — it means more of the work was done alone.</p>`
          : `<p class="small muted">There is nothing from last week to compare against yet.</p>`}

        ${ind.unaided_pct !== null ? `
          <div style="margin-top:18px">
            <div class="flex-between" style="margin-bottom:6px">
              <span class="small muted">Pages finished with no help at all</span>
              <span class="bold">${ind.unaided_pct}%</span>
            </div>
            <div class="track">
              <div class="fill fill-green" style="width:${ind.unaided_pct}%"></div>
            </div>
            <p class="small faint" style="margin-top:5px">
              ${ind.pages_unaided} of ${ind.page_visits} page visits
            </p>
          </div>` : ''}

        ${types.length ? `
          <div style="margin-top:18px">
            <p class="small muted" style="margin-bottom:8px">What was asked for</p>
            <div class="flex wrap gap-sm">
              ${types.map(([k, n]) =>
                `<span class="pill pill-purple">${esc(LABELS[k] || k.replace(/_/g, ' '))} × ${n}</span>`
              ).join('')}
            </div>
          </div>` : ''}
      </div>`;
  }

  function noteCard(note, { compact = false } = {}) {
    const TONE = {
      praise:   { label: 'Went well',  cls: 'pill-green' },
      progress: { label: 'Progress',   cls: 'pill-blue'  },
      concern:  { label: 'Please read', cls: 'pill-amber' },
    };
    const tone = TONE[note.tone] || TONE.progress;
    return `
      <article class="note ${esc(note.tone)} ${note.read ? '' : 'unread'}"
               data-note="${note.id}">
        <div class="note-head">
          <span class="mono-avatar" style="background:${esc(note.avatar_color || '#7c3aed')}">
            ${esc(note.initials || '?')}
          </span>
          <div class="grow">
            <p class="note-teacher">${esc(note.teacher)}</p>
            <p class="note-meta">${esc(note.teacher_title || '')}${
              note.subject ? ` · ${esc(note.subject_icon || '')} ${esc(note.subject)}` : ''}</p>
          </div>
        </div>
        <p class="note-body">${esc(note.body)}</p>
        <div class="note-foot">
          <span class="pill ${tone.cls}">${esc(tone.label)}</span>
          <span class="note-meta">${esc(ago(note.created_at))}</span>
        </div>
      </article>`;
  }

  /* ===========================================================================
     HOME
     ========================================================================= */

  function home(data) {
    const child = data.child;
    const s = data.stats;
    const name = child.display_name;

    /* A child who has not started gets a different screen, not a screen full
       of zeroes. Aziz's mother should read "he has not begun yet", not a row
       of 0% bars that look like failure. */
    if (!data.started) {
      return `
        <div class="banner">
          <div class="banner-inner">
            <div class="robot-float">${robot(80)}</div>
            <div class="grow">
              <h2>${esc(name)} hasn't started yet</h2>
              <p>
                ${esc(name)}'s profile is set up and ready. Nothing has been recorded
                because no lessons have been opened yet — that is all this means.
                As soon as ${esc(name)} begins, this page fills in on its own.
              </p>
            </div>
          </div>
        </div>

        ${data.notes.length ? `
          <div class="card">
            <div class="card-head">
              <h3>From ${esc(name)}'s teachers</h3>
              <button class="link-btn" data-nav="notes">All notes</button>
            </div>
            ${data.notes.map(n => noteCard(n)).join('')}
          </div>` : ''}

        <div class="card">
          ${empty('📚', 'Nothing to show yet',
            'Progress, subjects and achievements will appear here once the first lesson is opened.')}
        </div>`;
    }

    const hour = new Date().getHours();
    const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
    const parentName = (Api.State.parent && Api.State.parent.full_name) || '';

    return `
      <div class="banner">
        <div class="banner-inner">
          <div class="robot-float">${robot(80)}</div>
          <div class="grow">
            <h2>${esc(greeting)}, ${esc(parentName)}</h2>
            <p>${esc(homeSummary(data))}</p>
            <div class="banner-actions">
              <button class="ghost-btn" data-nav="progress">See progress</button>
              <button class="ghost-btn" data-nav="notes">
                Teacher notes${data.unread_notes ? ` (${data.unread_notes} new)` : ''}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div class="grid grid-4">
        ${stat({
          icon: '⏱️', tint: 'tint-blue',
          value: esc(s.week_time),
          label: 'Time learning this week',
          pill: s.prev_week_seconds ? s.week_change_label : null,
          pillClass: s.week_change >= 0 ? 'pill-green' : 'pill-amber',
        })}
        ${stat({
          icon: '🔥', tint: 'tint-amber',
          value: `${s.day_streak} <small>day${s.day_streak === 1 ? '' : 's'}</small>`,
          label: 'Learning streak',
        })}
        ${stat({
          icon: '📄', tint: 'tint-purple',
          value: s.pages_worked,
          label: 'Lesson pages worked through',
        })}
        ${stat({
          icon: '⭐', tint: 'tint-green',
          value: s.stars,
          label: `Stars earned · level ${s.level}`,
        })}
      </div>

      <div class="grid grid-2-1">
        <div class="card">
          <div class="card-head">
            <h3>This week</h3>
            <span class="small faint">minutes per day</span>
          </div>
          ${weekChart(data.week)}
        </div>

        ${independenceCard(data.independence, name)}
      </div>

      <div class="grid grid-2-1">
        <div class="card">
          <div class="card-head">
            <h3>Subjects</h3>
            <button class="link-btn" data-nav="subjects">See all</button>
          </div>
          ${s.subjects_started
            ? subjectRows(data.subjects.filter(x => x.has_data))
            : empty('📘', 'No subject scores yet',
                `${name} has been working, but hasn't completed enough of any one lesson for a score to mean anything yet.`)}
        </div>

        <div class="card">
          <div class="card-head"><h3>Recent activity</h3></div>
          ${data.recent.length ? data.recent.map(a => `
            <div class="row">
              <div class="row-icon tint-grey">${a.icon}</div>
              <div class="row-body">
                <p class="row-title">${esc(a.label)}</p>
                <p class="row-sub">${a.subject ? esc(a.subject) + ' · ' : ''}${esc(ago(a.occurred_at))}</p>
              </div>
              ${a.stars ? `<span class="pill pill-amber">+${a.stars} ⭐</span>` : ''}
            </div>`).join('')
            : `<p class="muted small">Nothing recorded yet.</p>`}
        </div>
      </div>

      ${data.notes.length ? `
        <div class="card">
          <div class="card-head">
            <h3>From ${esc(name)}'s teachers</h3>
            <button class="link-btn" data-nav="notes">All notes</button>
          </div>
          ${data.notes.map(n => noteCard(n)).join('')}
        </div>` : ''}`;
  }

  /* One honest sentence about the week, built from what is actually there. */
  function homeSummary(data) {
    const name = data.child.display_name;
    const s = data.stats;
    const days = data.week.filter(d => d.minutes > 0).length;
    const bits = [];

    if (days) {
      bits.push(`${name} worked on ${days} day${days === 1 ? '' : 's'} this week, ${s.week_time} in total`);
    } else {
      bits.push(`${name} hasn't opened a lesson yet this week`);
    }
    if (s.pages_worked) bits.push(`${s.pages_worked} lesson page${s.pages_worked === 1 ? '' : 's'} covered so far`);
    if (data.unread_notes) {
      bits.push(`${data.unread_notes} new note${data.unread_notes === 1 ? '' : 's'} from teachers`);
    }
    return bits.join('. ') + '.';
  }

  /* ===========================================================================
     PROGRESS
     ========================================================================= */

  function progress(data) {
    const name = data.child.display_name;
    const peak = Math.max(1, ...data.trend.map(t => t.minutes));

    return `
      <div class="grid grid-4">
        ${stat({ icon: '⏱️', tint: 'tint-blue',  value: esc(data.total_time), label: 'Total time learning' })}
        ${stat({ icon: '🔥', tint: 'tint-amber', value: data.child.day_streak, label: 'Day streak' })}
        ${stat({ icon: '⭐', tint: 'tint-green', value: data.child.stars, label: 'Stars earned' })}
        ${stat({ icon: '📈', tint: 'tint-purple', value: `Level ${data.child.level}`, label: 'Current level' })}
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-head">
            <h3>This week</h3><span class="small faint">minutes per day</span>
          </div>
          ${weekChart(data.week)}
        </div>

        <div class="card">
          <div class="card-head">
            <h3>Last four weeks</h3><span class="small faint">minutes per week</span>
          </div>
          <div class="bar-chart">
            ${data.trend.map(t => `
              <div class="bar-col" title="${esc(t.label)}: ${t.minutes} min">
                <div class="bar-shell">
                  <div class="bar-value" style="height:${Math.round(t.minutes / peak * 100)}%"></div>
                </div>
                <span class="bar-label">${esc(t.label)}</span>
              </div>`).join('')}
          </div>
          <p class="hint-inline" style="margin-top:12px">
            Time spent, not marks. Marks need more finished work before they mean anything.
          </p>
        </div>
      </div>

      ${independenceCard(data.independence, name)}

      <div class="card">
        <div class="card-head">
          <h3>Subjects</h3>
          <button class="link-btn" data-nav="subjects">Open a subject</button>
        </div>
        ${subjectRows(data.subjects)}
      </div>

      ${data.topics.length ? `
        <div class="card">
          <div class="card-head"><h3>Lessons practised</h3></div>
          ${data.topics.map(t => `
            <div class="subject-row">
              <div class="row-icon tint-grey">${esc(t.icon || '📘')}</div>
              <div class="subject-meta">
                <div class="subject-line">
                  <span class="subject-name">${esc(t.title)}</span>
                  <span class="subject-pct">${t.level_pct}%</span>
                </div>
                <div class="track">
                  <div class="fill ${fillFor(t.level_pct)}" style="width:${t.level_pct}%"></div>
                </div>
                <p class="small faint" style="margin-top:5px">
                  ${t.correct} correct of ${t.attempts} · last practised ${esc(ago(t.last_practiced_at))}
                </p>
              </div>
            </div>`).join('')}
        </div>` : `
        <div class="card">
          ${empty('🧩', 'No lesson scores yet',
            `${name} is working through pages, but hasn't answered enough questions in any one lesson for a score yet.`)}
        </div>`}

      <div class="card">
        <div class="card-head"><h3>Everything ${esc(name)} has done</h3></div>
        ${data.recent.length ? data.recent.map(a => `
          <div class="row">
            <div class="row-icon tint-grey">${a.icon}</div>
            <div class="row-body">
              <p class="row-title">${esc(a.label)}</p>
              <p class="row-sub">${a.subject ? esc(a.subject) + ' · ' : ''}${esc(ago(a.occurred_at))}</p>
            </div>
            ${a.stars ? `<span class="pill pill-amber">+${a.stars} ⭐</span>` : ''}
          </div>`).join('') : `<p class="muted small">Nothing recorded yet.</p>`}
      </div>`;
  }

  /* ===========================================================================
     SUBJECTS
     ========================================================================= */

  function subjects(data) {
    return `
      <div class="grid grid-3">
        ${data.subjects.map(s => `
          <button class="subject-card card-hover" data-subject="${esc(s.code)}">
            <div class="subject-card-top">
              <div class="subject-card-icon" style="background:${esc(s.color_from)}1a">${esc(s.icon)}</div>
              <span class="subject-pct" style="font-size:22px;color:${esc(s.color_from)}">
                ${s.has_data ? s.progress_pct + '%' : '—'}
              </span>
            </div>
            <h4>${esc(s.name)}</h4>
            <p class="sub">
              ${s.topics_started} of ${s.topic_count} lesson${s.topic_count === 1 ? '' : 's'} started
              ${s.seconds ? ` · ${esc(s.time_label)}` : ''}
            </p>
            <div class="track">
              <div class="fill ${s.has_data ? fillFor(s.progress_pct) : 'fill-grey'}"
                   style="width:${s.has_data ? s.progress_pct : 0}%"></div>
            </div>
          </button>`).join('')}
      </div>`;
  }

  function subjectDetail(data) {
    const s = data.subject;
    const name = data.child.display_name;

    return `
      <div class="card">
        <div class="flex gap wrap" style="align-items:flex-start">
          <div class="subject-card-icon" style="background:${esc(s.color_from)}1a;width:64px;height:64px;font-size:32px">
            ${esc(s.icon)}
          </div>
          <div class="grow">
            <div class="flex gap" style="align-items:baseline">
              <p class="stat-value" style="font-size:40px">
                ${data.has_data ? data.progress_pct + '%' : '—'}
              </p>
              <p class="muted">${esc(s.name)}</p>
            </div>
            <div class="track thick" style="max-width:420px;margin-top:10px">
              <div class="fill ${data.has_data ? fillFor(data.progress_pct) : 'fill-grey'}"
                   style="width:${data.has_data ? data.progress_pct : 0}%"></div>
            </div>
            <p class="small muted" style="margin-top:10px">
              ${data.lessons_started} of ${data.lessons_total} lessons started
              ${data.seconds ? ` · ${esc(data.time_label)} spent` : ''}
            </p>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h3>Lessons</h3></div>
        ${data.lessons.length ? data.lessons.map(l => `
          <div class="subject-row">
            <div class="row-icon ${l.started ? 'tint-purple' : 'tint-grey'}">
              ${l.started ? '📖' : '⚪'}
            </div>
            <div class="subject-meta">
              <div class="subject-line">
                <span class="subject-name">${esc(l.title)}</span>
                <span class="subject-pct">
                  ${l.started ? l.level_pct + '%' : '<span class="faint small">not started</span>'}
                </span>
              </div>
              <div class="track">
                <div class="fill ${l.started ? fillFor(l.level_pct) : 'fill-grey'}"
                     style="width:${l.started ? l.level_pct : 0}%"></div>
              </div>
              ${l.started ? `<p class="small faint" style="margin-top:5px">
                ${l.correct} correct of ${l.attempts} · last practised ${esc(ago(l.last_practiced_at))}
              </p>` : ''}
            </div>
          </div>`).join('')
          : empty('📭', 'No lessons in this subject yet',
              'The curriculum for this subject has not been loaded into Souly.')}
      </div>

      ${data.notes.length ? `
        <div class="card">
          <div class="card-head"><h3>${esc(s.name)} notes about ${esc(name)}</h3></div>
          ${data.notes.map(n => noteCard(n)).join('')}
        </div>` : ''}`;
  }

  /* ===========================================================================
     TEACHER NOTES
     ========================================================================= */

  function notes(data) {
    const name = data.child.display_name;
    if (!data.notes.length) {
      return `<div class="card">${empty('📝', 'No notes yet',
        `${name}'s teachers haven't written anything yet. When they do, it appears here and you'll see a badge in the sidebar.`)}</div>`;
    }

    const counts = data.counts;
    return `
      <div class="grid grid-3">
        ${stat({ icon: '💚', tint: 'tint-green', value: counts.praise,   label: 'Went well' })}
        ${stat({ icon: '📘', tint: 'tint-blue',  value: counts.progress, label: 'Progress updates' })}
        ${stat({ icon: '📌', tint: 'tint-amber', value: counts.concern,  label: 'Asking something of you' })}
      </div>

      <div class="card">
        <div class="card-head">
          <h3>All notes about ${esc(name)}</h3>
          ${data.unread ? `<span class="pill pill-purple">${data.unread} unread</span>` : ''}
        </div>
        <p class="small muted" style="margin-bottom:16px">
          Opening a note marks it read, so ${esc(name)}'s teachers can see it reached you.
        </p>
        ${data.notes.map(n => noteCard(n)).join('')}
      </div>`;
  }

  /* ===========================================================================
     ACHIEVEMENTS
     ========================================================================= */

  function achievements(data) {
    const name = data.child.display_name;
    const earned = data.badges.filter(b => b.earned);
    const locked = data.badges.filter(b => !b.earned);

    return `
      <div class="banner">
        <div class="banner-inner">
          <div class="robot-float">${robot(70)}</div>
          <div class="grow">
            <h3>${data.earned} of ${data.total} badges</h3>
            <p>${earned.length
              ? `${esc(name)} has earned ${data.earned} badge${data.earned === 1 ? '' : 's'} so far. Badges are for effort and consistency, not for being top of the class.`
              : `${esc(name)} hasn't earned a badge yet. They come from turning up and keeping going, so the first one usually arrives in the first week.`}</p>
          </div>
        </div>
      </div>

      ${earned.length ? `
        <div class="card">
          <div class="card-head"><h3>Earned</h3></div>
          <div class="grid grid-4">
            ${earned.map(b => `
              <div class="badge-tile">
                <div class="badge-face">${esc(b.icon)}</div>
                <p class="badge-name">${esc(b.name)}</p>
                <p class="badge-when">${esc(fmtDate(b.unlocked_at))}</p>
              </div>`).join('')}
          </div>
        </div>` : ''}

      <div class="card">
        <div class="card-head">
          <h3>Still to earn</h3>
          <span class="small faint">${locked.length} to go</span>
        </div>
        <div class="grid grid-4">
          ${locked.map(b => `
            <div class="badge-tile locked" title="${esc(b.description || '')}">
              <div class="badge-face">${esc(b.icon)}</div>
              <p class="badge-name">${esc(b.name)}</p>
              <p class="badge-when">${esc(b.description || '')}</p>
            </div>`).join('')}
        </div>
      </div>`;
  }

  /* ===========================================================================
     SUPPORT PROFILE
     -----------------------------------------------------------------------
     The tab a generic school portal does not have. It is also the only place
     in the hub where a parent can change something, so the boundary is stated
     out loud: what they control, and what belongs to the school.
     ========================================================================= */

  const SUPPORT_LABELS = {
    adhd: 'ADHD',
    autism: 'Autistic',
    dyslexia: 'Dyslexia',
    visual_impairment: 'Visual impairment',
    hearing_impairment: 'Hearing impairment',
    none: 'No specific accommodations',
  };

  const SETTING_COPY = {
    read_aloud:      ['Read everything aloud', 'Souly speaks each page as well as showing it.'],
    high_contrast:   ['High contrast',         'Stronger colours and heavier text.'],
    larger_buttons:  ['Larger buttons',        'Bigger tap targets, easier on small hands.'],
    reduce_motion:   ['Reduce movement',       'Turns off animations that can distract or unsettle.'],
    closed_captions: ['Captions',              'Shows text for anything Souly says out loud.'],
  };

  function support(data) {
    const name = data.child.display_name;
    const lp = data.learner_profile;
    const st = data.settings;

    const NEED = {
      low: ['Works well with a light touch',
            `${name} usually gets there with a reminder to re-read the question.`],
      metacognitive: ['Does best when prompted to think it through',
            `${name} tends to solve it once nudged to slow down and check what is being asked.`],
      task_specific: ['Does best with a worked example first',
            `${name} gets further when shown one done first, then trying the next alone.`],
    };
    const need = lp && NEED[lp.instruction_need];

    return `
      <div class="card">
        <div class="card-head"><h3>What the school has recorded</h3></div>
        <div class="kv">
          <span class="k">Support profile</span>
          <span class="v">${esc(SUPPORT_LABELS[data.support_profile] || data.support_profile)}</span>
        </div>
        ${data.support_notes ? `
          <div style="margin-top:14px;padding:14px 16px;background:#fafafd;border-radius:12px">
            <p class="small muted" style="margin-bottom:5px">Note on file</p>
            <p style="font-size:14px;line-height:1.6">${esc(data.support_notes)}</p>
          </div>` : ''}
        <p class="hint-inline" style="margin-top:14px">
          This is set by the school. If something here is wrong, message ${esc(name)}'s
          teacher — it changes how Souly teaches, so it is worth getting right.
        </p>
      </div>

      ${lp ? `
        <div class="card">
          <div class="card-head">
            <h3>What Souly has worked out so far</h3>
            <span class="pill ${lp.confidence < 0.6 ? 'pill-amber' : 'pill-green'}">
              ${esc(lp.confidence_label)}
            </span>
          </div>

          ${lp.confidence < 0.6 ? `
            <div style="padding:13px 16px;background:#fffbeb;border-radius:12px;margin-bottom:18px">
              <p class="small" style="color:#92400e;line-height:1.6">
                This came from one short activity on ${esc(fmtDate(lp.created_at))}, and one
                short activity does not measure a child. Treat it as a starting guess that
                Souly keeps correcting as ${esc(name)} works — not as a finding.
              </p>
            </div>` : ''}

          ${need ? `
            <div class="kv"><span class="k">How ${esc(name)} learns best</span>
              <span class="v">${esc(need[0])}</span></div>
            <p class="small muted" style="margin:10px 4px 0">${esc(need[1])}</p>` : ''}

          ${lp.items_attempted ? `
            <div class="kv" style="margin-top:14px">
              <span class="k">Solved without help, in that activity</span>
              <span class="v">${lp.items_solved_unaided} of ${lp.items_attempted}</span>
            </div>` : ''}

          ${lp.modality_gap !== null && lp.modality_gap !== undefined ? `
            <div class="kv">
              <span class="k">Listening vs reading</span>
              <span class="v">${lp.modality_gap > 0
                ? 'Takes more in by listening'
                : lp.modality_gap < 0 ? 'Takes more in by reading' : 'About the same either way'}</span>
            </div>
            <p class="small muted" style="margin:10px 4px 0">
              A measured difference in how the words get in — not a "learning style".
              Souly leans on the stronger one while still practising the other.
            </p>` : ''}

          ${lp.interests ? `
            <div class="kv" style="margin-top:14px">
              <span class="k">Interests Souly uses in examples</span>
              <span class="v">${esc(safeInterests(lp.interests))}</span>
            </div>` : ''}
        </div>` : `
        <div class="card">
          ${empty('🧭', 'No learning profile yet',
            `${name} hasn't done the short entry activity yet. It takes about ten minutes and it is what lets Souly pitch lessons at the right level.`)}
        </div>`}

      <div class="card">
        <div class="card-head">
          <h3>Accessibility — you can change these</h3>
        </div>
        <p class="small muted" style="margin-bottom:16px">
          These follow ${esc(name)} to every screen: the robot, the tablet, the classroom
          display. Change it here and it is changed everywhere.
        </p>
        ${Object.keys(SETTING_COPY).map(key => `
          <div class="toggle-row">
            <div class="grow">
              <p class="tr-label">${esc(SETTING_COPY[key][0])}</p>
              <p class="tr-help">${esc(SETTING_COPY[key][1])}</p>
            </div>
            <button class="switch ${st[key] ? 'on' : ''}" data-setting="${key}"
                    role="switch" aria-checked="${st[key] ? 'true' : 'false'}"
                    aria-label="${esc(SETTING_COPY[key][0])}"></button>
          </div>`).join('')}

        <div class="toggle-row">
          <div class="grow">
            <p class="tr-label">Text size</p>
            <p class="tr-help">How large the words are on ${esc(name)}'s screen.</p>
          </div>
          <div class="flex gap-sm">
            ${['small', 'medium', 'large'].map(size => `
              <button class="btn ${st.font_size === size ? 'btn-primary' : 'btn-secondary'}"
                      style="padding:7px 13px;font-size:13px"
                      data-font="${size}">${size[0].toUpperCase() + size.slice(1)}</button>`).join('')}
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h3>Your details</h3></div>
        <div class="kv"><span class="k">Name</span>
          <span class="v">${esc(Api.State.parent.full_name)}</span></div>
        <div class="kv"><span class="k">Email</span>
          <span class="v">${esc(Api.State.parent.email || '—')}</span></div>
        <div class="kv"><span class="k">Phone</span>
          <span class="v">${esc(Api.State.parent.phone || '—')}</span></div>
        <div class="kv"><span class="k">Children linked to you</span>
          <span class="v">${esc(Api.State.children.map(c => c.display_name).join(', '))}</span></div>
      </div>`;
  }

  function safeInterests(raw) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed.join(', ');
    } catch (e) { /* stored as plain text */ }
    return raw;
  }

  /* ===========================================================================
     MESSAGES
     ========================================================================= */

  function messages(data, thread) {
    const list = data.conversations;

    if (!list.length) {
      return `<div class="card">${empty('💬', 'No conversations yet',
        "When a teacher writes to you, the thread appears here. You can also start one from the button on any teacher's note.")}
        <div class="center" style="padding-bottom:20px">
          <button class="btn btn-primary" data-action="new-thread">Message a teacher</button>
        </div></div>`;
    }

    return `
      <div class="flex-between" style="margin-bottom:4px">
        <p class="small muted">
          ${data.unread_total
            ? `${data.unread_total} unread message${data.unread_total === 1 ? '' : 's'}`
            : 'All caught up'}
        </p>
        <button class="btn btn-secondary" style="padding:8px 14px;font-size:13px"
                data-action="new-thread">Message a teacher</button>
      </div>

      <div class="messenger">
        <div class="thread-list">
          <div class="thread-list-head">
            <p class="small muted">Conversations</p>
          </div>
          <div class="thread-list-body">
            ${list.map(t => `
              <button class="thread-item ${thread && thread.id === t.id ? 'active' : ''}"
                      data-thread="${t.id}">
                <span class="mono-avatar" style="background:${esc(t.avatar_color || '#7c3aed')}">
                  ${esc(t.initials || '?')}
                </span>
                <span class="grow" style="min-width:0">
                  <span class="tl-top">
                    <span class="tl-name">${esc(t.teacher)}</span>
                    ${t.unread ? `<span class="nav-badge">${t.unread}</span>` : ''}
                  </span>
                  <!-- The child's name is on every row on purpose. Fayrouz may
                       have two threads with the same teacher, one per son. -->
                  <span class="tl-child">${esc(t.child_avatar || '')} ${esc(t.child)}</span>
                  <span class="tl-last">${esc(t.last_body || 'No messages yet')}</span>
                </span>
              </button>`).join('')}
          </div>
        </div>

        <div class="thread-pane">
          ${thread ? threadPane(thread) : `
            <div class="empty" style="margin:auto">
              <span class="emoji">👈</span>
              <h4>Pick a conversation</h4>
              <p>Choose a thread on the left to read it and reply.</p>
            </div>`}
        </div>
      </div>`;
  }

  function threadPane(thread) {
    let lastDay = null;
    const bubbles = thread.messages.map(m => {
      const day = fmtDate(m.created_at);
      const divider = day !== lastDay
        ? `<p class="day-divider">${esc(day)}</p>` : '';
      lastDay = day;
      return `${divider}
        <div class="bubble-row ${m.from === 'parent' ? 'mine' : ''}">
          ${m.from === 'teacher' ? `
            <span class="mono-avatar" style="background:${esc(thread.avatar_color || '#7c3aed')};width:30px;height:30px;font-size:11px">
              ${esc(thread.initials || '?')}
            </span>` : ''}
          <div class="bubble ${m.from === 'parent' ? 'mine' : 'theirs'}">
            ${esc(m.body)}
            <time>${esc(fmtTime(m.created_at))}</time>
          </div>
        </div>`;
    }).join('');

    return `
      <div class="thread-head">
        <span class="mono-avatar" style="background:${esc(thread.avatar_color || '#7c3aed')}">
          ${esc(thread.initials || '?')}
        </span>
        <div class="grow">
          <p class="note-teacher">${esc(thread.teacher)}</p>
          <p class="note-meta">${esc(thread.teacher_title || '')} · about ${esc(thread.child)}</p>
        </div>
      </div>

      <div class="thread-body" id="thread-body">
        ${bubbles || '<p class="muted small center">No messages yet — say hello.</p>'}
      </div>

      <form class="thread-foot" id="reply-form">
        <input class="input" id="reply-input" placeholder="Write a message…"
               autocomplete="off" aria-label="Message">
        <button class="btn btn-primary" type="submit">Send</button>
      </form>`;
  }

  function teacherPicker(teachers) {
    return `
      <div class="modal-overlay" data-action="close-modal">
        <div class="modal" data-stop>
          <div class="modal-head">
            <h3>Message a teacher</h3>
            <button class="icon-btn" data-action="close-modal" aria-label="Close">${icon('x')}</button>
          </div>
          <p class="small muted" style="margin-bottom:16px">
            About ${esc(Api.currentChild().display_name)}.
            Each conversation is about one child, so replies never get mixed up.
          </p>
          ${teachers.map(t => `
            <button class="row" style="width:100%" data-teacher="${t.id}">
              <span class="mono-avatar" style="background:${esc(t.avatar_color)}">${esc(t.initials || '?')}</span>
              <span class="row-body" style="text-align:left">
                <span class="row-title" style="display:block">${esc(t.full_name)}</span>
                <span class="row-sub" style="display:block">${esc(t.title || '')}${
                  t.subject ? ` · ${esc(t.subject)}` : ''}</span>
              </span>
            </button>`).join('')}
        </div>
      </div>`;
  }

  return {
    esc, icon, robot, loading, errorBox, empty, ago, fmtDate,
    gate, sidebar, header,
    home, progress, subjects, subjectDetail,
    notes, achievements, support, messages, teacherPicker,
  };
})();
