import { dateString, shiftDay, filterPlayers, parseRoute, formatRating, historyMetrics, rankingPage, boardChangeKey, rankingInfo } from './rating-core.mjs';
import { drawScoreChart } from './score-chart.mjs';

const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const number = (value, digits = 0) => Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const signed = value => `${value > 0 ? '+' : ''}${number(value, 2)}`;
const changeClass = value => value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral';
const state = { manifest: null, index: null, board: 'rating', page: 0, pageSize: 30, history: new Map(), recent: new Map(), recentLimit: 60, routeSequence: 0 };

function dateTime(value) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));
}
function nameColor(player) {
  if (player.provisional) return '#000000';
  const item = state.manifest.rating_tier_policy.tiers.find(item => item.code === player.rating_tier);
  return item?.color || '#6b7280';
}
function personLink(player, className = 'player-name') {
  return `<a class="${className}" style="--player-color:${nameColor(player)}" href="#player/${player.id}">${escape(player.nickname || '未命名牌手')}</a>`;
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
    ['近 30 日活跃', number(stats.active_30d), '至少一场有效计分对局'],
    ['近 30 日对局', number(stats.valid_matches_30d), `${number(stats.boards_30d)} 副 · 每局只计一次`],
    ['成熟牌手中位等级分', stats.median_mature_rating == null ? '—' : number(stats.median_mature_rating, 0), '当前已成熟牌手'],
  ];
  $('site-stats').innerHTML = cards.map(([label, value, detail]) => `<div class="stat-card"><div class="stat-label">${label}</div><div class="stat-value">${value}</div><div class="stat-detail">${detail}</div></div>`).join('');
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
  document.querySelectorAll('[data-board]').forEach(button => { const active = button.dataset.board === state.board; button.classList.toggle('active', active); button.setAttribute('aria-selected', active); });
  $('change-column').textContent = f.period === '1' ? '昨日变化' : `近 ${f.period} 日变化`;
  const periodLabel = f.period === '1' ? '昨日' : `近 ${f.period} 日`;
  $('ranking-note').textContent = state.board === 'rating' ? '按等级分排序；仅成熟牌手有排名，暂定牌手以黑色用户名列出、不赋予名次。' : `按${periodLabel}等级分净变化排序，仅限成熟牌手，整日排除日初仍有隐藏分的计分日，展示前 30 名。`;
  $('search-status').textContent = f.query.trim() ? `找到 ${number(rows.length)} 名牌手，支持当前昵称和历史昵称` : '搜索自己、对手，或一位熟悉的牌友';
  const deltaKey = boardChangeKey(state.board, f.period);
  $('ranking-body').innerHTML = page.items.map((player, i) => `<tr>
    <td><span class="rank ${state.board === 'rating' && player.rank != null && player.rank <= 3 ? 'rank-top' : ''}">${player.provisional ? '—' : state.board === 'rating' ? player.rank : state.page * state.pageSize + i + 1}</span></td>
    <td><div class="table-player">${playerAvatar(player)}${personLink(player)}</div></td>
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
  return `<div class="overview-card"><div class="card-label">${label}</div><div class="card-value ${extra}">${value}</div><div class="card-note">${note}</div></div>`;
}
function renderOverview(player) {
  $('player-heading').innerHTML = `${playerAvatar(player, 'avatar')}<div class="profile-title"><div class="profile-name-row"><h1 style="--player-color:${nameColor(player)}">${escape(player.nickname || '未命名牌手')}</h1></div><p>最近参赛 ${escape(player.last_rated_day || '—')}</p></div>`;
  const { rank, percentile } = rankingInfo(player, state.manifest.stats.mature_players);
  const matureNote = player.provisional ? `累计 ${number(player.rated_board_count)} 副 · RD ${number(player.rating_deviation, 1)}；成熟需 ≥400 副且 RD≤225` : `累计 ${number(player.rated_board_count)} 副有效牌`;
  $('player-overview').innerHTML = [
    overviewCard('等级分', formatRating(player.display_rating), `最近更新 ${escape(player.last_rated_day || '—')}`),
    overviewCard('全站排名', rank == null ? '#-' : `#${number(rank)}`, percentile == null ? '暂定牌手暂不参与排名' : `超过 ${number(percentile, 2)}% 的成熟牌手`),
    overviewCard('近 30 日变化', signed(player.delta_30d), `近 7 日 ${signed(player.delta_7d)}`, changeClass(player.delta_30d)),
    overviewCard('历史最高等级分', number(player.peak_display_rating, 2), `当前算法 · 覆盖自 ${state.manifest.history_start}`),
    overviewCard('有效计分对局', number(player.rated_match_count), matureNote),
    overviewCard('30 日场均 VP', player.avg_vp_30d == null ? '—' : number(player.avg_vp_30d, 2), `近 30 日 ${number(player.recent_match_count)} 场 · 归一化 20 VP`),
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
  $('chart-caption').textContent = `${chartZoom ? `${dateString(chartZoom.min)} — ${dateString(chartZoom.max)} · ` : ''}按日结等级分连接。拖动框选横轴可放大；悬浮查看最近记录。`;
}
function renderMatches(reset = false) {
  if (reset) state.recentLimit = 60;
  const needle = $('match-filter').value.trim().toLocaleLowerCase();
  const filtered = currentRecent.filter(match => {
    const opponent = state.byId.get(match.opponent_id);
    return !needle || [state.index.competitions[match.competition_id]?.title || '', opponent?.nickname || '', ...(state.index.aliases?.[match.opponent_id] || [])].some(value => value.toLocaleLowerCase().includes(needle));
  });
  $('recent-summary').textContent = `${state.manifest.recent_start} — ${shiftDay(state.manifest.recent_end_exclusive, -1)} · 共 ${number(currentRecent.length)} 场有效计分对局${needle ? ` · 筛选后 ${number(filtered.length)} 场` : ''} · 对手等级分与颜色取计分前状态`;
  if (!filtered.length) { $('match-list').innerHTML = '<div class="empty-state">没有符合条件的计分对局。</div>'; $('more-matches').hidden = true; return; }
  const days = new Map();
  filtered.slice(0, state.recentLimit).forEach(match => { if (!days.has(match.day)) days.set(match.day, []); days.get(match.day).push(match); });
  const scoreByDay = historyMetrics(currentHistory);
  $('match-list').innerHTML = [...days].map(([day, matches]) => `<section class="day-group"><div class="day-header"><h3>${day}</h3><span class="day-count">${number(currentRecent.filter(item => item.day === day).length)} 场</span><span class="day-change ${changeClass(scoreByDay.get(day)?.delta || 0)}">当日等级分 ${signed(scoreByDay.get(day)?.delta || 0)}</span></div>${matches.map(match => {
    const opponent = state.byId.get(match.opponent_id);
    const beforeOpponent = opponent && { ...opponent, avatar: match.opponent_avatar ?? opponent.avatar, display_rating: match.opponent_display_rating_before,
      provisional: match.opponent_provisional_before, rating_tier: match.opponent_rating_tier_before };
    const event = state.index.competitions[match.competition_id]?.title || match.competition_id;
    return `<div class="match-row"><div class="event-info"><div class="event-title">${escape(event)}</div><div class="match-meta">第 ${match.round} 轮 · 第 ${match.table} 桌${match.pairing != null ? ` · 第 ${match.pairing} 台` : ''}</div></div><div class="opponent"><span class="match-meta">对手</span>${beforeOpponent ? `${playerAvatar(beforeOpponent, 'avatar-opponent')}<span class="opponent-name">${personLink(beforeOpponent)} <span class="opponent-rating">(${formatRating(beforeOpponent.display_rating)})</span></span>` : '<span class="player-name">未命名牌手</span>'}</div><div class="score">${number(match.score, 2)} <span>:</span> ${number(match.opponent_score, 2)}<small>归一化 VP</small></div><div class="board-count">${match.boards} 副</div></div>`;
  }).join('')}</section>`).join('');
  $('more-matches').hidden = filtered.length <= state.recentLimit;
  $('more-matches').textContent = `再显示 ${Math.min(60, filtered.length - state.recentLimit)} 场`;
}
function renderRules() {
  $('rules-content').innerHTML = `<div class="rule-card"><h2>哪些比赛计入等级分</h2><p>仅计入已确认由新睿主办的官方赛事，以及在发布截止时间之前结束、已完整校验的轮次。赛事整体仍在进行时，已结束的完整轮次也可以计入。</p><p>轮空、无效账号、弃权和异常无效比分不进入计分。近 30 日记录展示所有已纳入本次计分的有效对局。</p></div>
  <div class="rule-grid"><div class="rule-card"><h2>等级分与涨跌</h2><p>新牌手的等级分从 0 起步，前六场有效对局逐步释放初始分数。个人概览、历史曲线和每日变化保留这一过程；上分榜和下分榜整日排除日初仍有隐藏分的计分日，即使当天完成了第六场，也从下一计分日开始累计。</p><p>成熟牌手的用户名颜色表示分档；暂定牌手使用黑色。</p></div><div class="rule-card"><h2>每日结算</h2><p>同一天的对局在日末统一汇总。比赛详情按日列出等级分总变化；每场比分显示为计分使用的归一化 20 VP。对手的等级分、暂定状态和颜色均取该场计分前的冻结状态，同一天采用相同的日初状态。</p><p>昨日、近 7 日、近 30 日均按北京时间自然日计算，以当前发布截止时间为准。表现分来自当日结算，按对手分数与对局权重计算。</p></div></div>
  <div class="rule-card"><h2>暂定与成熟</h2><p>所有牌手最初为暂定状态。累计至少 400 副有效牌，且最后计分时 RD≤225，才转为成熟。六场初始分数释放完成与计分成熟是两个独立条件。</p><p>曲线背景按分数区间划分，纵轴随当前查看范围调整。可拖动框选横轴放大，再点击“恢复范围”返回。</p></div>
  <div class="rule-grid"><div class="rule-card"><h2>排名与筛选</h2><p>仅成熟牌手参与排名，按等级分降序排列，分数相同时沿用固定次序。暂定牌手可出现在排行榜中，名次显示为“—”；个人页显示“#-”，不计算分位数。成熟牌手的全站排名与分位数均以全站成熟牌手为统计范围。</p><p>排行榜筛选后保留全站成熟牌手排名，支持翻页和跳页；上分榜和下分榜仅显示符合所选条件的成熟牌手前 30 名。</p><p>活跃牌手指近 30 日至少有一场有效计分对局的人。场均 VP 为近 30 日有效对局归一化 VP 的算术平均。</p></div><div class="rule-card"><h2>数据覆盖与更新</h2><p>当前历史覆盖 ${state.manifest.history_start} 至 ${state.manifest.history_end}。数据排他截止点：${dateTime(state.manifest.cutoff)}（北京时间）。最后成功发布：${dateTime(state.manifest.generated_at)}。</p><p>历史曲线与最高分使用当前算法重算的结果。比分修订或算法调整可能改变历史；采集或计分未通过校验时，网页保留上一份成功发布的数据。</p><p>算法 ${escape(state.manifest.algorithm)}</p></div></div>`;
}
async function route() {
  if (!state.index) return;
  const sequence = ++state.routeSequence;
  const target = parseRoute(location.hash);
  $('home-view').hidden = target.view !== 'home';
  $('player-view').hidden = target.view !== 'player';
  $('rules-view').hidden = target.view !== 'rules';
  $('error-state').hidden = true;
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
    renderCurve(); renderMatches();
  } catch (error) { if (sequence === state.routeSequence) showError(error); }
}
function showError(error) {
  $('error-state').hidden = false;
  $('error-message').textContent = error.message;
}
async function boot() {
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
