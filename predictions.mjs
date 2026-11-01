import './player-name.js';

const TITLES = { monthly: '月赛预测', team: '团体赛预测', pair: '双人团预测' };
const DEFAULT_COLORS = {
  novice: '#6b7280', pupil: '#17813d', specialist: '#138a94', expert: '#2563eb',
  cm: '#a855f7', m: '#e78924', gm: '#dc2626',
};
const format = (value, digits = 1) => Number.isFinite(value)
  ? value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
const percent = value => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—';

/** Mount the three prediction pages; the site's router owns visibility and title. */
export function mountPredictions(container, {
  getManifest = () => null, dataUrl = './predictions/monthly-202611.json', onDataLoaded = () => {}, avatarBase = './',
} = {}) {
  container.innerHTML = `
    <header class="prediction-intro">
      <p class="eyebrow"><span></span> THREE MAJOR EVENTS</p>
      <h1 data-prediction-title>月赛预测</h1>
      <p data-prediction-description>根据现有等级分，估计升、保、降级概率。</p>
    </header>
    <nav class="prediction-nav" aria-label="三大赛预测">
      <a href="#predictions/monthly" data-prediction-link="monthly">月赛预测</a>
      <a href="#predictions/team" data-prediction-link="team">团体赛预测</a>
      <a href="#predictions/pair" data-prediction-link="pair">双人团预测</a>
    </nav>
    <section class="panel prediction-section" aria-labelledby="prediction-swiss-title">
      <div class="panel-heading"><div class="section-title"><span class="section-kicker">SWISS STAGE</span><h2 id="prediction-swiss-title">积分赛预测</h2></div><span class="prediction-edition" data-prediction-edition></span></div>
      <div class="prediction-message" data-prediction-loading role="status" hidden>正在读取预测…</div>
      <div class="prediction-message" data-prediction-error role="alert" hidden><p>预测暂时未能加载，请重试。</p><button type="button" class="button button-secondary" data-prediction-retry>重新加载</button></div>
      <div data-prediction-monthly hidden>
        <p class="prediction-note" data-prediction-note></p>
        <div class="prediction-controls">
          <label class="prediction-search"><span>搜索牌手</span><input type="search" data-prediction-search placeholder="输入牌手昵称" autocomplete="off"></label>
          <label><span>分组</span><select data-prediction-group><option value="">全部五组</option></select></label>
          <label><span>排序</span><select data-prediction-sort><option value="display_rating">等级分 ↓</option><option value="promotion_probability">升级概率 ↓</option><option value="retention_probability">保级概率 ↓</option><option value="relegation_probability">降级概率 ↓</option><option value="mean_rank">预计名次 ↑</option></select></label>
          <label><span>每页</span><select data-prediction-size><option>50</option><option>100</option><option>200</option></select></label>
        </div>
        <p class="prediction-result" data-prediction-result aria-live="polite"></p>
        <div class="table-scroll"><table class="prediction-table" aria-label="全部牌手升降保级概率">
          <thead><tr><th scope="col">牌手</th><th scope="col">分组</th><th scope="col">等级分 / RD</th><th scope="col">升级</th><th scope="col">保级</th><th scope="col">降级</th><th scope="col">预计名次</th><th scope="col">90% 名次区间</th></tr></thead>
          <tbody data-prediction-players></tbody>
        </table></div>
        <div class="pagination prediction-pagination"><span class="pagination-caption">保级指留在本级，不含升级；三项概率合计 100%。</span><div class="page-controls"><button type="button" class="pagination-button" data-prediction-prev aria-label="预测名单上一页">←</button><span data-prediction-page></span><button type="button" class="pagination-button" data-prediction-next aria-label="预测名单下一页">→</button></div></div>
      </div>
      <div class="prediction-blank" data-prediction-swiss-blank></div>
    </section>
    <section class="panel prediction-section" aria-labelledby="prediction-knockout-title">
      <div class="panel-heading"><div class="section-title"><span class="section-kicker">KNOCKOUT STAGE</span><h2 id="prediction-knockout-title">淘汰赛预测</h2></div></div>
      <div class="prediction-blank" aria-label="淘汰赛预测内容留空"></div>
    </section>`;

  const find = selector => container.querySelector(`[data-prediction-${selector}]`);
  const ui = Object.fromEntries(['title', 'description', 'edition', 'loading', 'error', 'retry',
    'monthly', 'note', 'search', 'group', 'sort', 'size', 'result', 'players', 'prev', 'next',
    'page', 'swiss-blank'].map(key => [key, find(key)]));
  let data = null;
  let pending = null;
  let manifest = null;
  let current = 'monthly';
  let sequence = 0;
  let page = 0;
  let groupMap = new Map();

  function playerColor(player) {
    if (player.provisional) return '#000000';
    const tiers = (manifest || getManifest())?.rating_tier_policy?.tiers;
    return tiers?.find(tier => tier.code === player.rating_tier)?.color
      || DEFAULT_COLORS[player.rating_tier] || '#6b7280';
  }
  function cell(row, text, className = '') {
    const td = document.createElement('td');
    td.textContent = text;
    td.className = className;
    row.append(td);
    return td;
  }
  function renderRows() {
    if (!data) return;
    const needle = ui.search.value.trim().toLocaleLowerCase();
    const selected = data.players.filter(player => (!ui.group.value || player.group === ui.group.value)
      && (!needle || player.nickname.toLocaleLowerCase().includes(needle)));
    const key = ui.sort.value;
    selected.sort((a, b) => (key === 'mean_rank' ? a[key] - b[key] : b[key] - a[key])
      || a.id.localeCompare(b.id));
    const size = Number(ui.size.value);
    const pages = Math.max(1, Math.ceil(selected.length / size));
    page = Math.max(0, Math.min(page, pages - 1));
    ui.players.replaceChildren();
    for (const player of selected.slice(page * size, (page + 1) * size)) {
      const tr = document.createElement('tr');
      const name = cell(tr, '', 'prediction-player');
      const identity = document.createElement('div');
      identity.className = 'table-player';
      const avatar = document.createElement('span');
      avatar.className = 'player-avatar';
      avatar.style.setProperty('--player-color', playerColor(player));
      avatar.setAttribute('aria-hidden', 'true');
      const initial = document.createElement('span');
      initial.textContent = Array.from(player.nickname || '桥')[0];
      avatar.append(initial);
      if (typeof player.avatar === 'string' && /^avatars\/[a-f0-9]{24}\.(?:jpg|jpeg|png|gif|webp)$/.test(player.avatar)) {
        const image = document.createElement('img');
        image.src = new URL(player.avatar, new URL(avatarBase, document.baseURI)).href;
        image.alt = '';
        image.loading = 'lazy';
        image.decoding = 'async';
        image.setAttribute('data-player-avatar', '');
        avatar.append(image);
      }
      const label = document.createElement('span');
      label.className = 'prediction-player-label';
      label.innerHTML = globalThis.PlayerName.markup(player.nickname || '未命名牌手', {
        color: playerColor(player), rating: player.display_rating, provisional: player.provisional,
        profile_url: `#player/${player.id}`,
      });
      if (player.provisional) {
        const status = document.createElement('span');
        status.className = 'prediction-provisional';
        status.textContent = '暂定';
        label.append(status);
      }
      identity.append(avatar, label);
      name.append(identity);
      cell(tr, groupMap.get(player.group)?.name || '—', 'prediction-group-name');
      cell(tr, `${format(player.display_rating)} / ${format(player.rating_deviation, 0)}`);
      cell(tr, percent(player.promotion_probability), 'prediction-up');
      cell(tr, percent(player.retention_probability), 'prediction-stay');
      cell(tr, percent(player.relegation_probability), 'prediction-down');
      cell(tr, format(player.mean_rank));
      cell(tr, `${format(player.rank_p05, 0)}–${format(player.rank_p95, 0)}`);
      ui.players.append(tr);
    }
    if (!selected.length) {
      const tr = document.createElement('tr');
      const td = cell(tr, '没有符合条件的牌手，试试其他昵称或分组。', 'prediction-empty');
      td.colSpan = 8;
      ui.players.append(tr);
    }
    ui.result.textContent = `匹配 ${format(selected.length, 0)} 人 · 共 ${format(data.players.length, 0)} 人`;
    ui.page.textContent = `第 ${page + 1} / ${pages} 页`;
    ui.prev.disabled = page === 0;
    ui.next.disabled = page >= pages - 1;
  }

  async function load() {
    if (data) return data;
    if (!pending) pending = fetch(dataUrl).then(response => {
      if (!response.ok) throw new Error('Prediction response failed');
      return response.json();
    }).then(value => {
      if (value.schema_version !== 'xinrui-predictions-v1' || value.event !== 'monthly'
        || !Array.isArray(value.players) || !Array.isArray(value.groups)) throw new Error('Prediction format mismatch');
      data = value;
      groupMap = new Map(value.groups.map(group => [group.id, group]));
      for (const group of value.groups) {
        const option = document.createElement('option');
        option.value = group.id;
        option.textContent = `${group.name}（${format(group.count, 0)} 人）`;
        ui.group.append(option);
      }
      return data;
    }).catch(error => { pending = null; throw error; });
    return pending;
  }

  async function show(kind = 'monthly') {
    current = Object.hasOwn(TITLES, kind) ? kind : 'monthly';
    const turn = ++sequence;
    ui.title.textContent = TITLES[current];
    ui.description.textContent = current === 'monthly'
      ? '根据现有等级分，估计升、保、降级概率。' : '暂未启用';
    for (const link of container.querySelectorAll('[data-prediction-link]')) {
      const active = link.dataset.predictionLink === current;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    ui.edition.textContent = '';
    ui.monthly.hidden = true;
    ui.loading.hidden = true;
    ui.error.hidden = true;
    ui['swiss-blank'].hidden = current === 'monthly';
    if (current !== 'monthly') return;
    ui.loading.hidden = Boolean(data);
    try {
      await load();
      if (turn !== sequence || current !== 'monthly') return;
      ui.edition.textContent = `${data.edition.slice(0, 4)} 年 ${Number(data.edition.slice(4))} 月 · ${format(data.players.length, 0)} 人`;
      ui.note.textContent = `每组 ${format(data.iterations, 0)} 次九轮模拟 · 假设正常参赛，未纳入当日实时比分。`;
      onDataLoaded(data);
      ui.monthly.hidden = false;
      renderRows();
    } catch {
      if (turn === sequence && current === 'monthly') ui.error.hidden = false;
    } finally {
      if (turn === sequence) ui.loading.hidden = true;
    }
  }

  ui.search.addEventListener('input', () => { page = 0; renderRows(); });
  for (const key of ['group', 'sort', 'size']) ui[key].addEventListener('change', () => { page = 0; renderRows(); });
  ui.prev.addEventListener('click', () => { page--; renderRows(); });
  ui.next.addEventListener('click', () => { page++; renderRows(); });
  ui.retry.addEventListener('click', () => show('monthly'));
  return { show, setManifest(value) { manifest = value; renderRows(); } };
}
