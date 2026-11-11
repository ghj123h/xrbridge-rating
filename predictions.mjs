import { formatRating } from './rating-core.mjs';

const TITLES = { monthly: '月赛预测', team: '团体赛预测', pair: '双人团预测' };
const DEFAULT_COLORS = {
  novice: '#6b7280', pupil: '#17813d', specialist: '#138a94', expert: '#2563eb',
  cm: '#a855f7', m: '#e78924', gm: '#dc2626',
};
const format = (value, digits = 1) => Number.isFinite(value)
  ? value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
const percent = value => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—';
export const beijingEdition = (date = new Date()) => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
}).formatToParts(date).filter(part => ['year', 'month'].includes(part.type)).map(part => part.value).join('');
export function probabilityText(value, applicable = true) { return applicable === false ? '-' : percent(value); }

/** Mount the three prediction pages; the site's router owns visibility and title. */
export function mountPredictions(container, {
  getManifest = () => null, manifestUrl = './predictions/index.json', onDataLoaded = () => {}, avatarBase = './',
  now = () => new Date(),
} = {}) {
  container.innerHTML = `
    <header class="prediction-intro">
      <p class="eyebrow"><span></span> THREE MAJOR EVENTS</p>
      <h1 data-prediction-title>月赛预测</h1>
      <p data-prediction-description></p>
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
        <div class="prediction-controls">
          <label class="prediction-search"><span data-prediction-search-label>搜索牌手</span><input type="search" data-prediction-search placeholder="输入牌手昵称" autocomplete="off"></label>
          <label><span>分组</span><select data-prediction-group><option value="">全部分组</option></select></label>
          <label><span>排序</span><select data-prediction-sort><option value="display_rating">等级分 ↓</option><option value="promotion_probability">升级概率 ↓</option><option value="retention_probability">保级概率 ↓</option><option value="relegation_probability">降级概率 ↓</option><option value="mean_rank">预计名次 ↑</option></select></label>
          <label><span>每页</span><select data-prediction-size><option>50</option><option>100</option><option>200</option></select></label>
        </div>
        <p class="prediction-result" data-prediction-result aria-live="polite"></p>
        <div class="table-scroll"><table class="prediction-table" data-prediction-table aria-label="全部牌手升降保级概率">
          <thead><tr data-prediction-columns><th scope="col">牌手</th><th scope="col">分组</th><th scope="col">等级分 / RD</th><th scope="col">升级</th><th scope="col">保级</th><th scope="col">降级</th><th scope="col">预计名次</th><th scope="col">90% 名次区间</th></tr></thead>
          <tbody data-prediction-players></tbody>
        </table></div>
        <div class="pagination prediction-pagination"><div class="page-controls"><button type="button" class="pagination-button" data-prediction-prev aria-label="预测名单上一页">←</button><span data-prediction-page></span><button type="button" class="pagination-button" data-prediction-next aria-label="预测名单下一页">→</button></div></div>
      </div>
      <div class="prediction-blank" data-prediction-swiss-blank></div>
    </section>
    <section class="panel prediction-section" aria-labelledby="prediction-knockout-title">
      <div class="panel-heading"><div class="section-title"><span class="section-kicker">KNOCKOUT STAGE</span><h2 id="prediction-knockout-title">淘汰赛预测</h2></div><span class="prediction-edition" data-prediction-ko-edition></span></div>
      <div class="prediction-blank" data-prediction-knockout></div>
      <div class="prediction-message" data-prediction-ko-error role="alert" hidden>预测暂时未能加载。<button type="button" class="button button-secondary" data-prediction-ko-retry>重新加载</button></div>
      <div data-prediction-ko-body hidden>
        <div class="prediction-controls">
          <label><span>搜索牌手</span><input type="search" data-prediction-ko-search placeholder="输入牌手昵称" autocomplete="off"></label>
          <label data-prediction-ko-group-control><span>分组</span><select data-prediction-ko-group><option value="">全部分组</option></select></label>
          <label><span>排序</span><select data-prediction-ko-sort><option value="champion_probability">冠军概率 ↓</option><option value="runner_up_probability">亚军概率 ↓</option><option value="bronze_probability">季军概率 ↓</option><option value="next_round_probability">晋级下一轮概率 ↓</option><option value="display_rating">等级分 ↓</option></select></label>
          <label><span>每页</span><select data-prediction-ko-size><option>50</option><option>100</option><option>200</option></select></label>
        </div>
        <p class="prediction-result" data-prediction-ko-result aria-live="polite"></p>
        <div class="table-scroll"><table class="prediction-table prediction-knockout-table" data-prediction-ko-table aria-label="存活牌手晋级与奖牌概率">
          <thead><tr><th scope="col">牌手</th><th scope="col" data-prediction-ko-group-column>分组</th><th scope="col">等级分</th><th scope="col">晋级下一轮</th><th scope="col" class="prediction-gold">冠军</th><th scope="col" class="prediction-silver">亚军</th><th scope="col" class="prediction-bronze">季军</th></tr></thead>
          <tbody data-prediction-ko-players></tbody>
        </table></div>
        <div class="pagination prediction-pagination"><div class="page-controls"><button type="button" class="pagination-button" data-prediction-ko-prev aria-label="淘汰赛名单上一页">←</button><span data-prediction-ko-page></span><button type="button" class="pagination-button" data-prediction-ko-next aria-label="淘汰赛名单下一页">→</button></div></div>
      </div>
    </section>`;

  const find = selector => container.querySelector(`[data-prediction-${selector}]`);
  const ui = Object.fromEntries(['title', 'description', 'edition', 'loading', 'error', 'retry',
    'monthly', 'search', 'group', 'sort', 'size', 'result', 'players', 'prev', 'next',
    'page', 'swiss-blank', 'columns', 'search-label', 'table', 'knockout',
    ...['edition', 'error', 'retry', 'body', 'search', 'group', 'group-control', 'group-column', 'table', 'sort', 'size', 'result', 'players', 'prev', 'next', 'page'].map(key => `ko-${key}`)]
    .map(key => [key, find(key)]));
  let data = null;
  let catalogue = null;
  let cataloguePending = null;
  const snapshots = new Map();
  let manifest = null;
  let current = 'monthly';
  let sequence = 0;
  let page = 0;
  let groupMap = new Map();
  let knockoutData = null;
  let knockoutPage = 0;
  const teamSnapshot = value => value?.schema_version === 'xinrui-team-swiss-predictions-v1';
  const rowsOf = value => teamSnapshot(value) ? value.teams : value.players;

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
  function playerIdentity(player, {showRating = false} = {}) {
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
      image.addEventListener('error', () => image.remove());
      avatar.append(image);
    }
    const label = document.createElement('span');
    label.className = 'prediction-player-label';
    label.innerHTML = globalThis.PlayerName.markup(player.nickname || '未命名牌手', {
      color: playerColor(player), rating: player.display_rating, provisional: player.provisional,
      profile_url: `#player/${player.id}`,
    }, {showRating});
    identity.append(avatar, label);
    return identity;
  }
  function renderKnockout() {
    if (!knockoutData) return;
    const showGroups = knockoutData.event !== 'monthly';
    const groups = new Map(knockoutData.groups.map(group => [group.id, group]));
    const needle = ui['ko-search'].value.trim().toLocaleLowerCase();
    const selected = knockoutData.players.filter(player => (!showGroups || !ui['ko-group'].value || player.group === ui['ko-group'].value)
      && (!needle || player.nickname.toLocaleLowerCase().includes(needle)));
    const key = ui['ko-sort'].value;
    selected.sort((a, b) => Number(b[key] ?? -1) - Number(a[key] ?? -1) || a.id.localeCompare(b.id));
    const size = Number(ui['ko-size'].value);
    const pages = Math.max(1, Math.ceil(selected.length / size));
    knockoutPage = Math.max(0, Math.min(knockoutPage, pages - 1));
    ui['ko-players'].replaceChildren();
    for (const player of selected.slice(knockoutPage * size, (knockoutPage + 1) * size)) {
      const tr = document.createElement('tr');
      cell(tr, '', 'prediction-player').append(playerIdentity(player));
      if (showGroups) cell(tr, groups.get(player.group)?.name || '—');
      cell(tr, formatRating(player.display_rating));
      cell(tr, percent(player.next_round_probability), 'prediction-qualify');
      cell(tr, percent(player.champion_probability), 'prediction-gold');
      cell(tr, percent(player.runner_up_probability), 'prediction-silver');
      cell(tr, percent(player.bronze_probability), 'prediction-bronze');
      ui['ko-players'].append(tr);
    }
    if (!selected.length) {
      const tr = document.createElement('tr');
      cell(tr, showGroups ? '没有符合条件的牌手，试试其他昵称或分组。' : '没有符合条件的牌手，试试其他昵称。', 'prediction-empty').colSpan = showGroups ? 7 : 6;
      ui['ko-players'].append(tr);
    }
    ui['ko-result'].textContent = `匹配 ${format(selected.length, 0)} 人 · 存活 ${format(knockoutData.players.length, 0)} 人`;
    ui['ko-page'].textContent = `第 ${knockoutPage + 1} / ${pages} 页`;
    ui['ko-prev'].disabled = knockoutPage === 0;
    ui['ko-next'].disabled = knockoutPage >= pages - 1;
  }
  function installKnockout(value) {
    const showGroups = value.event !== 'monthly';
    ui['ko-group-control'].hidden = !showGroups;
    ui['ko-group-column'].hidden = !showGroups;
    ui['ko-table'].classList.toggle('is-monthly', !showGroups);
    if (knockoutData !== value) {
      knockoutData = value;
      knockoutPage = 0;
      ui['ko-search'].value = '';
      ui['ko-sort'].value = 'champion_probability';
      const all = document.createElement('option'); all.value = ''; all.textContent = '全部分组';
      ui['ko-group'].replaceChildren(all);
      for (const group of value.groups) {
        const option = document.createElement('option'); option.value = group.id;
        option.textContent = `${group.name}（${group.count} 人）`; ui['ko-group'].append(option);
      }
      ui['ko-group'].value = '';
    }
    ui['ko-edition'].textContent = `${editionLabel(value.edition)} · ${format(value.players.length, 0)} 人`;
    ui.knockout.hidden = true;
    ui['ko-body'].hidden = false;
    renderKnockout();
    onDataLoaded(value);
  }
  function renderRows() {
    if (!data) return;
    const needle = ui.search.value.trim().toLocaleLowerCase();
    const teams = teamSnapshot(data);
    const rows = rowsOf(data);
    const selected = rows.filter(player => (!ui.group.value || player.group === ui.group.value)
      && (!needle || (teams ? [player.name, ...player.members.map(member => member.nickname)] : [player.nickname])
        .some(name => name.toLocaleLowerCase().includes(needle))));
    const key = ui.sort.value;
    const applicable = row => key === 'promotion_probability' ? groupMap.get(row.group)?.promotion_applicable !== false
      : key === 'relegation_probability' ? groupMap.get(row.group)?.relegation_applicable !== false : true;
    selected.sort((a, b) => Number(applicable(b)) - Number(applicable(a)) || (key === 'mean_rank' ? a[key] - b[key] : b[key] - a[key])
      || a.id.localeCompare(b.id));
    const size = Number(ui.size.value);
    const pages = Math.max(1, Math.ceil(selected.length / size));
    page = Math.max(0, Math.min(page, pages - 1));
    ui.players.replaceChildren();
    for (const player of selected.slice(page * size, (page + 1) * size)) {
      const tr = document.createElement('tr');
      const group = groupMap.get(player.group);
      if (teams) {
        cell(tr, player.name, 'prediction-player prediction-team-name');
        cell(tr, groupMap.get(player.group)?.name || '—', 'prediction-group-name');
        const members = cell(tr, '', 'prediction-team-members');
        for (const member of player.members) {
          members.append(playerIdentity(member, {showRating: true}));
        }
        cell(tr, percent(player.qualification_probability), 'prediction-qualify');
        cell(tr, probabilityText(player.promotion_probability, group?.promotion_applicable), 'prediction-up');
        cell(tr, percent(player.retention_probability), 'prediction-stay');
        cell(tr, probabilityText(player.relegation_probability, group?.relegation_applicable), 'prediction-down');
        cell(tr, format(player.mean_rank));
        cell(tr, `${format(player.rank_p05, 0)}–${format(player.rank_p95, 0)}`);
        ui.players.append(tr);
        continue;
      }
      const name = cell(tr, '', 'prediction-player');
      name.append(playerIdentity(player));
      cell(tr, groupMap.get(player.group)?.name || '—', 'prediction-group-name');
      cell(tr, `${formatRating(player.display_rating)} / ${format(player.rating_deviation, 0)}`);
      cell(tr, percent(player.qualification_probability), 'prediction-qualify');
      cell(tr, probabilityText(player.promotion_probability, group?.promotion_applicable), 'prediction-up');
      cell(tr, percent(player.retention_probability), 'prediction-stay');
      cell(tr, probabilityText(player.relegation_probability, group?.relegation_applicable), 'prediction-down');
      cell(tr, format(player.mean_rank));
      cell(tr, `${format(player.rank_p05, 0)}–${format(player.rank_p95, 0)}`);
      ui.players.append(tr);
    }
    if (!selected.length) {
      const tr = document.createElement('tr');
      const td = cell(tr, teams ? '没有符合条件的队伍，试试其他队名、队员昵称或分组。'
        : '没有符合条件的牌手，试试其他昵称或分组。', 'prediction-empty');
      td.colSpan = 9;
      ui.players.append(tr);
    }
    const unit = teams ? '队' : '人';
    ui.result.textContent = `匹配 ${format(selected.length, 0)} ${unit} · 共 ${format(rows.length, 0)} ${unit}`;
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
      if (!['xinrui-prediction-manifest-v1', 'xinrui-prediction-manifest-v2'].includes(value.schema_version)
        || typeof value.publication_version !== 'string' || !Array.isArray(value.items)
        || value.items.some(item => !item || typeof item.id !== 'string'
          || typeof item.family !== 'string' || typeof item.edition !== 'string'
          || !item.stages && typeof item.status !== 'string')) throw new Error('Prediction manifest mismatch');
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
      if (!['xinrui-predictions-v1', 'xinrui-team-swiss-predictions-v1', 'xinrui-knockout-predictions-v1'].includes(value.schema_version) || value.event !== item.family
        || value.edition !== item.edition || value.publication_version !== index.publication_version
        || (item.stage === 'knockout') !== (value.schema_version === 'xinrui-knockout-predictions-v1')
        || !Array.isArray(rowsOf(value)) || !Array.isArray(value.groups)
        || index.snapshot_id && value.snapshot_id !== index.snapshot_id
        || index.rating_cutoff && value.rating_cutoff !== index.rating_cutoff) throw new Error('Prediction format mismatch');
      return value;
    }).catch(error => { snapshots.delete(key); throw error; }));
    return snapshots.get(key);
  }

  function installSnapshot(value) {
    if (data === value) return;
    data = value;
    page = 0;
    ui.search.value = '';
    const teams = teamSnapshot(value);
    ui['search-label'].textContent = teams ? '搜索队伍或队员' : '搜索牌手';
    ui.search.placeholder = teams ? '输入队名或队员昵称' : '输入牌手昵称';
    ui.table.setAttribute('aria-label', teams ? '全部队伍晋级及升降保级概率' : '全部牌手升降保级概率');
    ui.columns.replaceChildren();
    for (const title of teams ? ['队伍', '分组', '队员', '晋级淘汰赛', '升级', '保级', '降级', '预计名次', '90% 名次区间']
      : ['牌手', '分组', '等级分 / RD', '进入决赛', '升级', '保级', '降级', '预计名次', '90% 名次区间']) {
      const th = document.createElement('th'); th.setAttribute('scope', 'col'); th.textContent = title;
      if (teams && title === '队员') th.className = 'prediction-members-heading';
      ui.columns.append(th);
    }
    ui.sort.replaceChildren();
    const sorts = teams ? [['mean_rank', '预计名次 ↑'], ['qualification_probability', '晋级淘汰赛概率 ↓'],
      ['promotion_probability', '升级概率 ↓'], ['retention_probability', '保级概率 ↓'], ['relegation_probability', '降级概率 ↓']]
      : [['display_rating', '等级分 ↓'], ['qualification_probability', '进入决赛概率 ↓'], ['promotion_probability', '升级概率 ↓'], ['retention_probability', '保级概率 ↓'],
        ['relegation_probability', '降级概率 ↓'], ['mean_rank', '预计名次 ↑']];
    for (const [key, title] of sorts) {
      const option = document.createElement('option'); option.value = key; option.textContent = title;
      ui.sort.append(option);
    }
    ui.sort.value = sorts[0][0];
    groupMap = new Map(value.groups.map(group => [group.id, group]));
    const allGroups = document.createElement('option');
    allGroups.value = '';
    allGroups.textContent = '全部分组';
    ui.group.replaceChildren(allGroups);
    for (const group of value.groups) {
      const option = document.createElement('option');
      option.value = group.id;
      option.textContent = `${group.name}（${format(group.count, 0)} ${teams ? '队' : '人'}）`;
      ui.group.append(option);
    }
    ui.group.value = '';
  }

  async function show(kind = 'monthly') {
    current = Object.hasOwn(TITLES, kind) ? kind : 'monthly';
    const family = current;
    const turn = ++sequence;
    ui.title.textContent = TITLES[family];
    ui.description.textContent = '';
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
    ui['ko-body'].hidden = true;
    ui['ko-error'].hidden = true;
    ui['ko-edition'].textContent = '';
    ui.knockout.hidden = false;
    ui.knockout.textContent = '';
    try {
      const index = await loadCatalogue();
      if (turn !== sequence) return;
      let item = latestItem(index.items, family);
      if (index.schema_version === 'xinrui-prediction-manifest-v2') {
        const month = index.preview === 'historical_knockout' ? index.edition : beijingEdition(now());
        item = index.items.find(entry => entry.family === family && entry.edition === month) || {
          family, edition: month, stages: { swiss: {status: 'not_started'}, knockout: {status: 'not_started'} },
        };
        ui.edition.textContent = editionLabel(month);
        const swiss = item.stages.swiss, knockout = item.stages.knockout;
        const stateText = status => status === 'ended' ? '赛事已结束' : status === 'not_started' ? '赛事未开始' : '';
        ui['ko-edition'].textContent = editionLabel(month);
        if (ui.knockout) ui.knockout.textContent = stateText(knockout.status);
        if (knockout.status === 'ongoing') {
          if (ui.knockout) ui.knockout.textContent = '预测正在加载…';
          try {
            if (!knockout.path) throw new Error('Ongoing knockout forecast missing');
            const value = await loadSnapshot({...item, ...knockout, stage: 'knockout'}, index);
            if (turn !== sequence) return;
            installKnockout(value);
          } catch {
            if (turn !== sequence) return;
            ui.knockout.hidden = true;
            ui['ko-error'].hidden = false;
          }
        }
        ui.description.textContent = '';
        if (swiss.status !== 'ongoing') {
          ui['swiss-blank'].hidden = false;
          ui['swiss-blank'].textContent = stateText(swiss.status);
          return;
        }
        if (!swiss.path) throw new Error('Ongoing forecast missing');
        item = {...item, ...swiss, status: 'forecast'};
      }
      if (!item) {
        ui.description.textContent = '暂无已发布预测。';
        return;
      }
      ui.edition.textContent = editionLabel(item.edition);
      if (typeof index.rating_cutoff === 'string') onDataLoaded({ event: family, rating_cutoff: index.rating_cutoff });
      if (item.status === 'complete') {
        ui.description.textContent = '';
        ui['swiss-blank'].hidden = false;
        ui['swiss-blank'].textContent = '赛事已结束';
        return;
      }
      if (!item.path) {
        if (item.status === 'unavailable') {
          ui.description.textContent = '';
          ui['swiss-blank'].hidden = false;
          ui['swiss-blank'].textContent = '资料不完整，暂无预测。';
        } else {
          ui.description.textContent = '预测尚未发布。';
        }
        return;
      }
      const value = await loadSnapshot(item, index);
      if (turn !== sequence) return;
      installSnapshot(value);
      const previewLabel = index.preview === 'synthetic_ui_test' ? '合成数据 · ' : '';
      if (teamSnapshot(data)) {
        ui.description.textContent = '';
        ui.edition.textContent = `${previewLabel}${editionLabel(data.edition)} · ${format(data.teams.length, 0)} 队`;
        onDataLoaded(data);
        ui.monthly.hidden = false;
        renderRows();
        return;
      }
      ui.description.textContent = '';
      ui.edition.textContent = `${previewLabel}${editionLabel(data.edition)} · ${format(data.players.length, 0)} 人`;
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
  ui['ko-retry'].addEventListener('click', () => {
    catalogue = null; cataloguePending = null; snapshots.clear(); return show(current);
  });
  ui['ko-search'].addEventListener('input', () => { knockoutPage = 0; renderKnockout(); });
  for (const key of ['group', 'sort', 'size']) ui[`ko-${key}`].addEventListener('change', () => { knockoutPage = 0; renderKnockout(); });
  ui['ko-prev'].addEventListener('click', () => { knockoutPage--; renderKnockout(); });
  ui['ko-next'].addEventListener('click', () => { knockoutPage++; renderKnockout(); });
  let shownMonth = beijingEdition(now());
  const monthTimer = setInterval(() => {
    const month = beijingEdition(now());
    if (month !== shownMonth) { shownMonth = month; catalogue = null; cataloguePending = null; snapshots.clear(); show(current); }
  }, 30000);
  monthTimer.unref?.();
  return { show, setManifest(value) { manifest = value; renderRows(); renderKnockout(); }, destroy() { clearInterval(monthTimer); } };
}
