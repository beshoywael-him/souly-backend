/* =============================================================================
   Souly — playable mini-games.

   Two engines cover all six games in the catalogue. Both draw their content
   from the same verified question bank the quiz uses, so a game can never ask
   a child something factually wrong — which is a real risk once you start
   generating content, and the reason games aren't just decoration here.

   Every finished game POSTs its score, which awards stars and ticks the
   daily challenge.
   ============================================================================= */

const Games = (() => {
  'use strict';

  const E = Util.esc;

  let session = null;   // { game, questions, index, score, timer, startedAt }

  function stop() {
    if (session && session.timer) clearInterval(session.timer);
    session = null;
  }

  /* ==========================================================================
     Engine 1 — Quick fire: answer as many as you can before the clock runs out
     ========================================================================== */

  async function startSprint(game) {
    const data = await Api.gameQuestions(game.id, 10);
    if (!data.questions.length) {
      App.toast('This game has no questions yet.', 'error');
      return;
    }

    session = {
      game,
      questions: data.questions,
      index: 0,
      score: 0,
      secondsLeft: 60,
      startedAt: Date.now(),
      engine: 'sprint'
    };

    renderSprint();
    session.timer = setInterval(() => {
      session.secondsLeft -= 1;
      const bar = document.getElementById('gameTimerFill');
      if (bar) {
        const pct = (session.secondsLeft / 60) * 100;
        bar.style.width = pct + '%';
        bar.className = 'game-timer-fill' + (pct < 25 ? ' low' : '');
      }
      const label = document.getElementById('gameTimerLabel');
      if (label) label.textContent = session.secondsLeft + 's';
      if (session.secondsLeft <= 0) finish();
    }, 1000);
  }

  function renderSprint() {
    const q = session.questions[session.index];
    if (!q) { finish(); return; }

    document.getElementById('page-games').innerHTML = `
      ${Util.topBar(session.game.icon, session.game.name, 'Answer as many as you can!', 'games')}

      <div class="glass-card">
        <div style="display:flex; justify-content:space-between; margin-bottom:10px;">
          <div style="font-size:13px; font-weight:800; color:#7C3AED;">Score: ${session.score}</div>
          <div style="font-size:13px; font-weight:800; color:#7C3AED;" id="gameTimerLabel">${session.secondsLeft}s</div>
        </div>
        <div class="game-timer-bar">
          <div class="game-timer-fill" id="gameTimerFill" style="width:${(session.secondsLeft / 60) * 100}%"></div>
        </div>
      </div>

      <div class="glass-card game-play-area">
        <div class="game-prompt">${E(q.prompt)}</div>
        <div id="gameOptions">
          ${q.options.map((opt, i) => `
            <button class="quiz-option" onclick="Games.answer(${i})">
              <span class="opt-letter">${String.fromCharCode(65 + i)}</span>
              <span>${E(opt)}</span>
            </button>`).join('')}
        </div>
      </div>

      <button class="btn-secondary" style="width:100%;" onclick="Games.quit()">End Game</button>
    `;
  }

  function answer(index) {
    if (!session) return;
    const q = session.questions[session.index];
    const correct = index === q.correct_index;

    document.querySelectorAll('#gameOptions .quiz-option').forEach((el, i) => {
      el.style.pointerEvents = 'none';
      if (i === q.correct_index) el.classList.add('correct');
      else if (i === index) el.classList.add('wrong');
    });

    if (correct) {
      session.score += 10;
      App.toast('Correct! +10', 'star');
    }

    setTimeout(() => {
      if (!session) return;
      session.index += 1;
      if (session.index >= session.questions.length) finish();
      else renderSprint();
    }, 700);
  }

  /* ==========================================================================
     Engine 2 — Memory match
     ========================================================================== */

  const MEMORY_ICONS = ['🍎', '🚀', '🌟', '🐘', '🎵', '🌈', '⚽', '🍕'];

  function startMemory(game) {
    const pairs = MEMORY_ICONS.slice(0, 8);
    const tiles = [...pairs, ...pairs]
      .map((icon, i) => ({ icon, id: i, flipped: false, matched: false }));

    // Fisher-Yates, so the board differs every play.
    for (let i = tiles.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [tiles[i], tiles[j]] = [tiles[j], tiles[i]];
    }

    session = {
      game, tiles, first: null, busy: false,
      matches: 0, moves: 0, startedAt: Date.now(), engine: 'memory'
    };
    renderMemory();
  }

  function renderMemory() {
    document.getElementById('page-games').innerHTML = `
      ${Util.topBar(session.game.icon, session.game.name, 'Find all the matching pairs!', 'games')}

      <div class="glass-card">
        <div style="display:flex; justify-content:space-around; text-align:center;">
          <div><div style="font-size:19px; font-weight:800; color:#7C3AED;">${session.matches}/8</div><div style="font-size:11px; color:#a78bfa; font-weight:600;">Pairs</div></div>
          <div><div style="font-size:19px; font-weight:800; color:#7C3AED;">${session.moves}</div><div style="font-size:11px; color:#a78bfa; font-weight:600;">Moves</div></div>
        </div>
      </div>

      <div class="glass-card">
        <div class="memory-grid">
          ${session.tiles.map((t, i) => `
            <button class="memory-tile ${t.matched ? 'matched' : (t.flipped ? 'flipped' : '')}"
                    onclick="Games.flip(${i})" aria-label="${t.flipped || t.matched ? t.icon : 'Hidden tile'}">
              ${t.flipped || t.matched ? t.icon : '?'}
            </button>`).join('')}
        </div>
      </div>

      <button class="btn-secondary" style="width:100%;" onclick="Games.quit()">End Game</button>
    `;
  }

  function flip(index) {
    if (!session || session.busy) return;
    const tile = session.tiles[index];
    if (tile.flipped || tile.matched) return;

    tile.flipped = true;

    if (session.first === null) {
      session.first = index;
      renderMemory();
      return;
    }

    session.moves += 1;
    const first = session.tiles[session.first];

    if (first.icon === tile.icon) {
      first.matched = tile.matched = true;
      session.matches += 1;
      session.first = null;
      renderMemory();
      App.toast('Match!', 'star');
      if (session.matches === 8) setTimeout(finish, 600);
    } else {
      session.busy = true;
      renderMemory();
      setTimeout(() => {
        if (!session) return;
        first.flipped = tile.flipped = false;
        session.first = null;
        session.busy = false;
        renderMemory();
      }, 850);
    }
  }

  /* ==========================================================================
     Finish
     ========================================================================== */

  async function finish() {
    if (!session) return;
    const current = session;
    if (current.timer) clearInterval(current.timer);
    session = null;

    const duration = Math.round((Date.now() - current.startedAt) / 1000);
    const score = current.engine === 'memory'
      ? Math.max(0, 100 - (current.moves - 8) * 5)
      : current.score;
    const maxScore = current.engine === 'memory'
      ? 100
      : current.questions.length * 10;

    let result;
    try {
      result = await Api.gameResult(current.game.id, {
        score, max_score: maxScore, duration_s: duration
      });
    } catch (err) {
      App.toast('Could not save your score: ' + err.message, 'error');
      App.go('games');
      return;
    }

    State.applyAward(result.award);
    App.syncCounters();

    document.getElementById('page-games').innerHTML = `
      ${Util.topBar('🎉', 'Game Over!', E(current.game.name), 'games')}
      <div class="glass-card" style="text-align:center;">
        <div style="font-size:64px;">${result.is_win ? '🏆' : '💪'}</div>
        <div style="font-size:22px; font-weight:800; color:#4c1d95; margin-top:8px;">
          ${result.score} / ${result.max_score}
        </div>
        <div style="font-size:14px; color:#a78bfa; font-weight:600; margin-top:4px;">
          ${result.accuracy_pct}% ${result.is_personal_best ? '· New personal best! 🌟' : ''}
        </div>
      </div>
      <div class="glass-card" style="background:linear-gradient(135deg,#7C3AED,#A855F7); color:#fff; text-align:center;">
        <div style="font-size:16px; font-weight:800;">⭐ +${result.award.stars_delta} Stars</div>
        <div style="font-size:13px; opacity:0.9; margin-top:4px;">Total: ${Util.num(result.award.total_stars)}</div>
      </div>
      <div style="display:flex; gap:10px;">
        <button class="btn-secondary" style="flex:1;" onclick="App.go('games')">Back to Games</button>
        <button class="btn-primary" style="flex:1; justify-content:center;" onclick="App.playGame(${current.game.id})">Play Again</button>
      </div>
    `;

    if (result.award.new_badges.length) App.celebrateBadges(result.award.new_badges);
    if (result.award.leveled_up) App.celebrateLevel(result.award);
  }

  function quit() {
    stop();
    App.go('games');
  }

  async function play(game) {
    if (game.engine === 'memory_match') startMemory(game);
    else await startSprint(game);
  }

  return { play, answer, flip, quit, finish, stop };
})();
