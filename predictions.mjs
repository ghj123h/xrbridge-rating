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
  getManifest = () => null, manifestUrl = './predictions/index.json', onDataLoaded = () => {}, avatarBase = './',
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
          <label><span>分组</span><select data-prediction-group><option value="">全部分组</option></select></label>
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
      <div class="prediction-blank prediction-note" data-prediction-swiss-blank></div>
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
  let catalogue = null;
  let cataloguePending = null;
  const snapshots = new Map();
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

  async function readJSON(url) {
    const response = await fetch(url, { cache: 'no-cache' });
    if (!response.ok) throw new Error('Prediction response failed');
    return response.json();
  }

  async function loadCatalogue() {
    if (catalogue) return catalogue;
    if (!cataloguePending) cataloguePending = readJSON(manifestUrl).then(value => {
      if (value.schema_version !== 'xinrui-prediction-manifest-v1'
        || typeof value.publication_version !== 'string' || !Array.isArray(value.items)
        || value.items.some(item => !item || typeof item.id !== 'string'
          || typeof item.family !== 'string' || typeof item.edition !== 'string'
          || typeof item.status !== 'string')) throw new Error('Prediction manifest mismatch');
      catalogue = value;
      return value;
    }).catch(error => { cataloguePending = null; throw error; });
    return cataloguePending;
  }

  function latestItem(items, family) {
    return items.filter(item => item.family === family).sort((a, b) =>
      Number(b.current === true) - Number(a.current === true)
      || b.edition.localeCompare(a.edition, 'en', { numeric: true })
      || b.id.localeCompare(a.id))[0];
  }

  function editionLabel(edition) {
    return /^\d{6}$/.test(edition)
      ? `${edition.slice(0, 4)} 年 ${Number(edition.slice(4))} 月` : edition;
  }

  async function loadSnapshot(item, index) {
    if (typeof item.path !== 'string' || !/^predictions\/[A-Za-z0-9][A-Za-z0-9._/-]*\.json$/.test(item.path)
      || item.path.split('/').some(part => part === '..' || part === '.')) throw new Error('Prediction path mismatch');
    const url = new URL(item.path, new URL(avatarBase, document.baseURI)).href;
    const key = `${index.publication_version}:${url}`;
    if (!snapshots.has(key)) snapshots.set(key, readJSON(url).then(value => {
      if (value.schema_version !== 'xinrui-predictions-v1' || value.event !== item.family
        || value.edition !== item.edition || value.publication_version !== index.publication_version
        || !Array.isArray(value.players) || !Array.isArray(value.groups)) throw new Error('Prediction format mismatch');
      return value;
    }).catch(error => { snapshots.delete(key); throw error; }));
    return snapshots.get(key);
  }

  function installSnapshot(value) {
    if (data === value) return;
    data = value;
    page = 0;
    ui.search.value = '';
    groupMap = new Map(value.groups.map(group => [group.id, group]));
    const allGroups = document.createElement('option');
    allGroups.value = '';
    allGroups.textContent = '全部分组';
    ui.group.replaceChildren(allGroups);
    for (const group of value.groups) {
      const option = document.createElement('option');
      option.value = group.id;
      option.textContent = `${group.name}（${format(group.count, 0)} 人）`;
      ui.group.append(option);
    }
    ui.group.value = '';
  }

  async function show(kind = 'monthly') {
    current = Object.hasOwn(TITLES, kind) ? kind : 'monthly';
    const family = current;
    const turn = ++sequence;
    ui.title.textContent = TITLES[family];
    ui.description.textContent = '正在读取赛事进度…';
    for (const link of container.querySelectorAll('[data-prediction-link]')) {
      const active = link.dataset.predictionLink === family;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    ui.edition.textContent = '';
    ui.monthly.hidden = true;
    ui.loading.hidden = false;
    ui.error.hidden = true;
    ui['swiss-blank'].hidden = true;
    ui['swiss-blank'].textContent = '';
    try {
      const index = await loadCatalogue();
      if (turn !== sequence) return;
      const item = latestItem(index.items, family);
      if (!item) {
        ui.description.textContent = '暂无已发布预测。';
        return;
      }
      ui.edition.textContent = editionLabel(item.edition);
      if (item.status === 'complete') {
        ui.description.textContent = '本届赛事已结束。';
        const completed = item.completed_swiss_rounds ?? item.completed_rounds;
        const remaining = item.remaining_swiss_rounds ?? item.remaining_rounds ?? 0;
        ui['swiss-blank'].hidden = false;
        ui['swiss-blank'].textContent = Number.isFinite(completed)
          ? `积分赛已完成 ${format(completed, 0)} 轮，剩余 ${format(remaining, 0)} 轮，无需模拟。`
          : '本届赛事已结束，无需模拟。';
        return;
      }
      if (!item.path) {
        if (item.status === 'unavailable') {
          ui.description.textContent = '本届历史资料不完整，暂无预测。';
          const progress = [
            ['已完成', item.completed_rounds ?? item.completed_swiss_rounds],
            ['总计', item.total_rounds ?? item.total_swiss_rounds],
            ['剩余', item.remaining_rounds ?? item.remaining_swiss_rounds],
          ].filter(([, rounds]) => Number.isFinite(rounds))
            .map(([label, rounds]) => `${label} ${format(rounds, 0)} 轮`);
          ui['swiss-blank'].hidden = false;
          ui['swiss-blank'].textContent = progress.length ? `${progress.join('，')}。` : '现有历史资料不足以生成预测。';
        } else {
          ui.description.textContent = '预测尚未发布。';
        }
        return;
      }
      const value = await loadSnapshot(item, index);
      if (turn !== sequence) return;
      installSnapshot(value);
      ui.description.textContent = '根据最新等级分和已完成轮次，估计升、保、降级概率。';
      ui.edition.textContent = `${editionLabel(data.edition)} · ${format(data.players.length, 0)} 人`;
      const remaining = data.remaining_rounds;
      const completed = data.completed_rounds;
      const cutoff = data.score_cutoff || data.rating_cutoff;
      const pairing = data.next_round_pairings === 'approximate'
        ? '后续按累计 VP 邻位近似编排并避免重遇。'
        : data.first_round_pairings === 'random' ? '首轮随机近似；后续按累计 VP 邻位编排并避免重遇。' : '';
      const scores = data.current_score_source === 'cloud_scores_with_local_official_completed_round_results'
        ? '云端已完成轮次比分与本地读取的官方结果已逐轮核对。'
        : data.current_score_source === 'cloud_scores_with_local_official_first_round_results'
          ? '云端首轮比分与本地读取的官方首轮结果已交叉核对。' : '';
      const withdrawals = data.withdrawn_player_count
        ? `已排除 ${format(data.withdrawn_player_count, 0)} 名退赛牌手。` : '';
      ui.note.textContent = `截至 ${cutoff}，已完成 ${format(completed, 0)} 轮；每组对剩余 ${format(remaining, 0)} 轮模拟 ${format(data.iterations, 0)} 次。${scores}${withdrawals}${pairing}假设正常参赛。`;
      onDataLoaded(data);
      ui.monthly.hidden = false;
      renderRows();
    } catch {
      if (turn === sequence) ui.error.hidden = false;
    } finally {
      if (turn === sequence) ui.loading.hidden = true;
    }
  }

  ui.search.addEventListener('input', () => { page = 0; renderRows(); });
  for (const key of ['group', 'sort', 'size']) ui[key].addEventListener('change', () => { page = 0; renderRows(); });
  ui.prev.addEventListener('click', () => { page--; renderRows(); });
  ui.next.addEventListener('click', () => { page++; renderRows(); });
  ui.retry.addEventListener('click', () => {
    catalogue = null; cataloguePending = null; snapshots.clear();
    return show(current);
  });
  return { show, setManifest(value) { manifest = value; renderRows(); } };
}
