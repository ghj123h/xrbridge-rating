import { dateString, shiftDay, filterPlayers, parseRoute, formatRating, matchScoreDisplay, historyMetrics, rankingPage, boardChangeKey, rankingInfo } from './rating-core.mjs';
import { drawScoreChart } from './score-chart.mjs';
import { mountPredictions } from './predictions.mjs';
import './player-name.js';

const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const number = (value, digits = 0) => Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const signed = value => `${value > 0 ? '+' : ''}${formatRating(value)}`;
const changeClass = value => value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral';
const state = { manifest: null, index: null, board: 'rating', page: 0, pageSize: 30, history: new Map(), recent: new Map(), recentLimit: 60, routeSequence: 0 };
const predictions = mountPredictions($('predictions-view'), {
  getManifest: () => state.manifest,
  onDataLoaded(data) {
    const target = parseRoute(location.hash);
    if (target.view === 'predictions' && target.kind === data.event && typeof data.rating_cutoff === 'string') {
      $('header-update').textContent = `数据截至 ${shiftDay(data.rating_cutoff.slice(0, 10), -1)}`;
    }
  },
});

function dateTime(value) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));
}
function nameColor(player) {
  if (player.provisional) return '#000000';
  const item = state.manifest.rating_tier_policy.tiers.find(item => item.code === player.rating_tier);
  return item?.color || '#6b7280';
}
function personName(player, {link = true, ...options} = {}) {
  return globalThis.PlayerName.markup(player.nickname || '未命名牌手', {
    color: nameColor(player), rating: player.display_rating, provisional: player.provisional,
    profile_url: link ? `#player/${player.id}` : null,
  }, options);
}
function playerAvatar(player, variant = '') {
  const path = typeof player.avatar === 'string' && /^avatars\/[a-f0-9]{24}\.(?:jpg|jpeg|png|gif|webp)$/.test(player.avatar) ? player.avatar : null;
  return `<span class="player-avatar ${variant}" style="--player-color:${nameColor(player)}" aria-hidden="true"><span>${escape(Array.from(player.nickname || '桥')[0])}</span>${path ? `<img src="./${escape(path)}" alt="" loading="${variant === 'avatar' ? 'eager' : 'lazy'}" decoding="async" data-player-avatar>` : ''}</span>`;
}
function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $('toast').hidden = true, 2400);
}
async function fetchJSON(path, compressed = false) {
  const response = await fetch(path, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`数据读取失败（${response.status}）`);
  if (!compressed) return response.json();
  if (!globalThis.DecompressionStream) throw new Error('请使用支持 gzip 解压的新版 Chrome、Edge、Firefox 或 Safari。');
  return new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).json();
}
async function shard(kind, player) {
  const bucket = player.bucket;
  if (!state[kind].has(bucket)) {
    const promise = fetchJSON(state.manifest.paths[kind].replace('{bucket}', String(bucket).padStart(3, '0')), true);
    state[kind].set(bucket, promise);
    promise.catch(() => state[kind].delete(bucket));
  }
  return (await state[kind].get(bucket)).players[player.id];
}
function renderStats() {
  const stats = state.manifest.stats;
  const cards = [
    ['已计分牌手', number(stats.players), `${number(stats.mature_players)} 人已成熟`],
    ['近 30 日活跃牌手', number(stats.active_30d), ''],
    ['近 30 日有效对局', number(stats.valid_matches_30d), `共 ${number(stats.boards_30d)} 副`],
    ['成熟牌手中位等级分', stats.median_mature_rating == null ? '—' : formatRating(stats.median_mature_rating, 0), ''],
  ];
  $('site-stats').innerHTML = cards.map(([label, value, detail]) => `<div class="stat-card"><div class="stat-label">${label}</div><div class="stat-value">${value}</div>${detail ? `<div class="stat-detail">${detail}</div>` : ''}</div>`).join('');
  $('site-overview').open = window.matchMedia('(min-width: 860px)').matches;
  $('header-update').textContent = `数据截至 ${shiftDay(state.manifest.cutoff.slice(0, 10), -1)}`;
  $('footer-meta').textContent = `北京时间 · 最后成功发布 ${dateTime(state.manifest.generated_at)} · ${state.manifest.algorithm}`;
}
function filters() {
  return { query: $('search-input').value, board: state.board, period: $('period-filter').value, status: $('status-filter').value, activity: $('activity-filter').value, tier: 'all' };
}
function renderRanking(reset = false) {
  if (!state.index) return;
  if (reset) state.page = 0;
  const f = filters();
  const rows = filterPlayers(state.index.players, state.index.aliases || {}, f, state.manifest.recent_start);
  const page = rankingPage(rows, state.board, state.page, state.pageSize);
  state.page = page.page;
  document.querySelectorAll('[data-board]').forEach(button => { const active = button.dataset.board === state.board; button.classList.toggle('active', active); button.setAttribute('aria-pressed', active); });
  $('change-column').textContent = f.period === '1' ? '昨日变化' : `近 ${f.period} 日变化`;
  $('ranking-note').textContent = state.board === 'rating' ? '仅成熟牌手有排名' : '成熟牌手前 30 名 · 仅计日初已完成前六场的计分日';
  $('search-status').textContent = f.query.trim() ? `匹配 ${number(rows.length)} 名牌手` : '';
  const deltaKey = boardChangeKey(state.board, f.period);
  $('ranking-body').innerHTML = page.items.map((player, i) => `<tr>
    <td><span class="rank ${state.board === 'rating' && player.rank != null && player.rank <= 3 ? 'rank-top' : ''}">${player.provisional ? '—' : state.board === 'rating' ? player.rank : state.page * state.pageSize + i + 1}</span></td>
    <td><div class="table-player">${playerAvatar(player)}${personName(player)}</div></td>
    <td class="rating-cell">${formatRating(player.display_rating)}</td>
    <td class="${changeClass(player[deltaKey])}">${signed(player[deltaKey])}</td>
    <td>${number(player.rated_match_count)}</td></tr>`).join('');
  $('ranking-empty').hidden = rows.length > 0;
  $('page-label').textContent = state.board === 'rating' ? `第 ${state.page + 1} / ${page.totalPages} 页 · ${number(rows.length)} 人` : `前 ${page.items.length} 名`;
  $('prev-page').hidden = $('next-page').hidden = $('page-jump').hidden = state.board !== 'rating';
  $('prev-page').disabled = state.page === 0;
  $('next-page').disabled = state.page + 1 >= page.totalPages;
  $('page-number').max = page.totalPages;
  $('page-number').value = state.page + 1;
}
function overviewCard(label, value, note, extra = '') {
  return `<div class="overview-card"><div class="card-label">${label}</div><div class="card-value ${extra}">${value}</div>${note ? `<div class="card-note">${note}</div>` : ''}</div>`;
}
function renderOverview(player, history = null) {
  const lastPlayed = player.last_rated_day ? ` · 最近参赛 ${escape(player.last_rated_day)}` : '';
  $('player-heading').innerHTML = `${playerAvatar(player, 'avatar')}
    <div class="profile-title">
      <div class="profile-name-row">${personName(player, {link: false, tag: 'h1', className: ''})}</div>
      <p>${player.provisional ? '暂定牌手' : '成熟牌手'}${lastPlayed}</p>
    </div>`;
  const { rank, percentile } = rankingInfo(player, state.manifest.stats.mature_players);
  const peak = history?.points.reduce((best, point) => !best || point[1] > best[1] ? point : best, null);
  const ratingNote = player.provisional && player.rating_deviation != null ? `不确定度 RD ${number(player.rating_deviation, 1)}` : '';
  $('player-overview').innerHTML = [
    overviewCard('当前等级分', formatRating(player.display_rating), ratingNote),
    overviewCard('全站排名', rank == null ? '#-' : `#${number(rank)}`, percentile == null ? '仅成熟牌手参与排名' : `超过 ${number(percentile, 2)}% 的成熟牌手`),
    overviewCard('近 30 日变化', signed(player.delta_30d), `近 7 日 ${signed(player.delta_7d)}`, changeClass(player.delta_30d)),
    overviewCard('历史最高分', formatRating(player.peak_display_rating), escape(peak?.[0] || '')),
    overviewCard('有效对局', `${number(player.rated_match_count)} <small>场</small>`, `累计 ${number(player.rated_board_count)} 副`),
    overviewCard('近 30 日场均 VP', player.avg_vp_30d == null ? '—' : number(player.avg_vp_30d, 2), `${number(player.recent_match_count)} 场 · 20 VP 制`),
  ].join('');
}
let currentHistory, currentRecent = [], chartZoom = null;
function renderCurve() {
  if (!currentHistory) return;
  const endDay = shiftDay(state.manifest.cutoff.slice(0, 10), -1);
  drawScoreChart($('rating-chart'), {
    history: currentHistory,
    tiers: state.manifest.rating_tier_policy.tiers, period: $('curve-period').value,
    endDay, zoom: chartZoom,
    onZoom: next => { chartZoom = next; renderCurve(); },
  });
  $('reset-zoom').hidden = !chartZoom;
  const start = chartZoom ? dateString(chartZoom.min)
    : $('curve-period').value === 'all' ? currentHistory.points[0]?.[0] : shiftDay(endDay, 1 - Number($('curve-period').value));
  $('chart-caption').textContent = start ? `${start} 至 ${chartZoom ? dateString(chartZoom.max) : endDay}` : '';
}
function renderMatches(reset = false) {
  if (reset) state.recentLimit = 60;
  const needle = $('match-filter').value.trim().toLocaleLowerCase();
  const filtered = currentRecent.filter(match => {
    const opponent = state.byId.get(match.opponent_id);
    return !needle || [state.index.competitions[match.competition_id]?.title || '', opponent?.nickname || '', ...(state.index.aliases?.[match.opponent_id] || [])].some(value => value.toLocaleLowerCase().includes(needle));
  });
  const dates = `${state.manifest.recent_start} 至 ${shiftDay(state.manifest.recent_end_exclusive, -1)}`;
  const count = needle ? `匹配 ${number(filtered.length)} / ${number(currentRecent.length)} 场` : `共 ${number(currentRecent.length)} 场`;
  $('recent-summary').textContent = `${dates} · ${count}`;
  if (!filtered.length) {
    $('match-list').innerHTML = `<div class="empty-state">${currentRecent.length ? '没有匹配的赛事或对手' : '近 30 日暂无计分对局'}</div>`;
    $('more-matches').hidden = true;
    return;
  }
  const days = new Map();
  filtered.slice(0, state.recentLimit).forEach(match => { if (!days.has(match.day)) days.set(match.day, []); days.get(match.day).push(match); });
  const scoreByDay = historyMetrics(currentHistory);
  $('match-list').innerHTML = [...days].map(([day, matches]) => `<section class="day-group"><div class="day-header"><h3>${day}</h3><span class="day-count">当日 ${number(currentRecent.filter(item => item.day === day).length)} 场</span><span class="day-change ${changeClass(scoreByDay.get(day)?.delta)}">当日变化 ${scoreByDay.has(day) ? signed(scoreByDay.get(day).delta) : '—'}</span></div>${matches.map(match => {
    const opponent = state.byId.get(match.opponent_id);
    const beforeOpponent = opponent && { ...opponent, avatar: match.opponent_avatar ?? opponent.avatar, display_rating: match.opponent_display_rating_before,
      provisional: match.opponent_provisional_before, rating_tier: match.opponent_rating_tier_before };
    const event = state.index.competitions[match.competition_id]?.title || match.competition_id;
    const score = matchScoreDisplay(match);
    return `<div class="match-row">
      <div class="event-info"><div class="event-title">${escape(event)}</div><div class="match-meta">第 ${match.round} 轮 · 第 ${match.table} 桌${match.pairing != null ? ` · 第 ${match.pairing} 台` : ''}</div></div>
      <div class="opponent"><span class="match-meta">对手</span>${beforeOpponent ? `${playerAvatar(beforeOpponent, 'avatar-opponent')}<span class="opponent-name" title="该场计分前的等级分">${personName(beforeOpponent, {showRating: true, ratingClass: 'opponent-rating'})}</span>` : personName({provisional: true}, {link: false})}</div>
      <div class="score">${score.score} <span>:</span> ${score.opponentScore}<small>${score.label}</small></div>
      <div class="board-count">${match.boards} 副</div>
    </div>`;
  }).join('')}</section>`).join('');
  $('more-matches').hidden = filtered.length <= state.recentLimit;
  if (!$('more-matches').hidden) $('more-matches').textContent = `再显示 ${Math.min(60, filtered.length - state.recentLimit)} 场`;
}
function renderRules() {
  const publication = state.manifest;
  $('rules-content').innerHTML = `
    <div class="rule-grid">
      <section class="rule-card">
        <h2>哪些对局计分</h2>
        <p>只计入已确认由新睿主办的官方赛事。轮次须在数据截止前结束，并通过完整校验。赛事仍在进行时，已完成的轮次可以计分。</p>
        <p>计分时排除轮空、无效账号、弃权和异常无效比分。个人页收录近 30 日内的全部有效计分对局。</p>
      </section>
      <section class="rule-card">
        <h2>初始分与成熟条件</h2>
        <p>新牌手的显示分从 0 起步，前六场有效对局逐步释放初始分。个人概览、曲线和每日涨跌都包含这部分变化。</p>
        <p>所有牌手以暂定状态起步。累计至少 <strong>400 副</strong>有效牌，且最近计分日结束时<strong>不确定度 RD ≤ 225</strong>，才达到成熟门槛。</p>
        <p>长期未参赛会使 RD 回升。再次计分后若 RD 仍高于 225，状态会转回暂定。两次计分之间沿用上次状态。</p>
      </section>
    </div>
    <div class="rule-grid">
      <section class="rule-card">
        <h2>榜单与排名</h2>
        <p><strong>排行榜</strong>按等级分从高到低排列，同分时沿用固定次序。名次和分位均以全站成熟牌手为准，筛选和翻页后保留原名次。暂定牌手用黑色姓名，榜上名次显示为“—”，个人页显示“#-”。成熟牌手的姓名颜色表示等级分档。</p>
        <p><strong>上分榜与下分榜</strong>各显示所选条件下净涨跌最多的 30 名成熟牌手。只累计日初已完成前六场有效对局的计分日，第六场当天的涨跌整日排除。</p>
        <p><strong>近 30 日活跃</strong>指这段时间内至少有一场有效计分对局。昨日、近 7 日、近 30 日均按发布截止点和北京时间自然日计算。</p>
      </section>
      <section class="rule-card">
        <h2>每日结算与比分</h2>
        <p>同一天的对局在日末统一结算，个人页按日显示总变化。对手的等级分、成熟状态和姓名颜色取比赛日的日初记录。</p>
        <p>个人淘汰赛展示原始 IMP，其他对局展示归一化 20 VP。评级统一使用归一化 VP，近 30 日场均 VP 为期间有效对局的算术平均值。</p>
        <p>当日表现分按对手等级分和对局权重计算。计算表现分时，每场的 20.00 VP 取 19.99，0.00 VP 取 0.01。实际等级分变化按原成绩计算。</p>
      </section>
    </div>
    <div class="rule-grid">
      <section class="rule-card">
        <h2>等级分曲线</h2>
        <p>曲线连接每日结算的等级分，背景色表示分档，纵轴随当前日期范围调整。选中记录可查当天的分数、涨跌与表现分。</p>
        <p>拖选一段日期可放大，选择“恢复范围”可回到原视图。历史曲线及最高分按当前算法重算，比分修订或算法更新都可能改变历史数值。</p>
      </section>
      <section class="rule-card">
        <h2>数据覆盖与发布</h2>
        <dl class="rule-facts">
          <dt>计分日期</dt><dd>${escape(publication.history_start)} 至 ${escape(publication.history_end)}</dd>
          <dt>数据截止</dt><dd>${dateTime(publication.cutoff)}（北京时间，不含该时刻）</dd>
          <dt>最近发布</dt><dd>${dateTime(publication.generated_at)}（北京时间）</dd>
          <dt>算法版本</dt><dd>${escape(publication.algorithm)}</dd>
        </dl>
        <p>采集或计分未通过校验时，网站沿用上一次成功发布的数据。</p>
      </section>
    </div>`;
}
async function route() {
  const sequence = ++state.routeSequence;
  const target = parseRoute(location.hash);
  $('home-view').hidden = target.view !== 'home';
  $('player-view').hidden = target.view !== 'player';
  $('rules-view').hidden = target.view !== 'rules';
  $('predictions-view').hidden = target.view !== 'predictions';
  $('error-state').hidden = true;
  document.querySelectorAll('.main-nav .nav-link').forEach(link => {
    const active = link.hash.startsWith('#predictions') ? target.view === 'predictions'
      : link.hash === '#rules' ? target.view === 'rules' : ['home', 'player'].includes(target.view);
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  if (target.view === 'predictions') {
    $('loading-state').hidden = true;
    $('header-update').textContent = state.manifest ? `数据截至 ${shiftDay(state.manifest.cutoff.slice(0, 10), -1)}` : '三大赛预测';
    document.title = `${{ monthly: '月赛预测', team: '团体赛预测', pair: '双人团预测' }[target.kind]} · 新睿等级分`;
    await predictions.show(target.kind);
    return;
  }
  if (!state.index) { await boot(); return; }
  $('header-update').textContent = `数据截至 ${shiftDay(state.manifest.cutoff.slice(0, 10), -1)}`;
  if (target.view === 'rules') { renderRules(); document.title = '等级分规则 · 新睿等级分'; }
  if (target.view === 'home') { renderRanking(); document.title = '新睿等级分 · 桥牌排行榜'; }
  if (target.view !== 'player') return;
  const player = state.byId.get(target.id);
  currentHistory = null; currentRecent = [];
  document.querySelectorAll('#player-view .chart-panel, #player-view .matches-panel').forEach(panel => panel.hidden = !player);
  if (!player) { $('player-heading').textContent = '未找到这位牌手的计分记录'; $('player-overview').innerHTML = ''; $('rating-chart').innerHTML = ''; $('match-list').innerHTML = ''; $('recent-summary').textContent = ''; $('reset-zoom').hidden = true; return; }
  currentHistory = null; currentRecent = []; state.recentLimit = 60;
  $('match-filter').value = ''; $('curve-period').value = 'all'; chartZoom = null;
  renderOverview(player);
  document.title = `${player.nickname || '未命名牌手'} · 新睿等级分`;
  $('rating-chart').innerHTML = '<div class="empty-state">正在读取等级分轨迹…</div>';
  $('match-list').innerHTML = '<div class="empty-state">正在读取近 30 日记录…</div>';
  try {
    const [history, recent] = await Promise.all([shard('history', player), shard('recent', player)]);
    if (sequence !== state.routeSequence) return;
    if (!history) throw new Error('该牌手的历史分片缺少记录。');
    currentHistory = history; currentRecent = recent || [];
    renderOverview(player, history);
    renderCurve(); renderMatches();
  } catch (error) { if (sequence === state.routeSequence) showError(error); }
}
function showError(error) {
  $('error-state').hidden = false;
  $('error-message').textContent = error.message;
}
async function boot() {
  if (parseRoute(location.hash).view === 'predictions') { await route(); return; }
  $('loading-state').hidden = false;
  $('error-state').hidden = true;
  try {
    state.manifest = await fetchJSON('./data/manifest.json');
    if (state.manifest.schema_version !== 'xinrui-rating-web-v3') throw new Error('网页与数据版本不一致，请刷新页面或重新导出网页数据。');
    state.index = await fetchJSON(state.manifest.paths.index, true);
    state.byId = new Map(state.index.players.map(player => [player.id, player]));
    renderStats(); renderRanking();
    await route();
  } catch (error) { showError(error); }
  finally { $('loading-state').hidden = true; }
}
let searchTimer;
document.addEventListener('error', event => {
  if (event.target instanceof HTMLImageElement && event.target.hasAttribute('data-player-avatar')) event.target.remove();
}, true);
$('search-input').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => renderRanking(true), 120); });
['period-filter', 'status-filter', 'activity-filter'].forEach(id => $(id).addEventListener('change', () => renderRanking(true)));
document.querySelectorAll('[data-board]').forEach(button => button.addEventListener('click', () => { state.board = button.dataset.board; renderRanking(true); }));
$('prev-page').addEventListener('click', () => { state.page--; renderRanking(); });
$('next-page').addEventListener('click', () => { state.page++; renderRanking(); });
$('page-jump').addEventListener('submit', event => { event.preventDefault(); state.page = Number($('page-number').value) - 1; renderRanking(); });
$('back-home').addEventListener('click', () => location.hash = '');
$('copy-link').addEventListener('click', async () => { try { await navigator.clipboard.writeText(location.href); toast('个人链接已复制'); } catch { toast('可直接复制浏览器地址分享个人页'); } });
$('curve-period').addEventListener('change', () => { chartZoom = null; renderCurve(); });
$('reset-zoom').addEventListener('click', () => { chartZoom = null; renderCurve(); });
$('match-filter').addEventListener('input', () => renderMatches(true));
$('more-matches').addEventListener('click', () => { state.recentLimit += 60; renderMatches(); });
$('retry-load').addEventListener('click', () => state.index ? route() : boot());
window.addEventListener('hashchange', () => { route(); window.scrollTo({ top: 0 }); });
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (!$('player-view').hidden) renderCurve(); }, 100); });
boot();
