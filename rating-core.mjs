export const DAY = 86400000;
export const dateNumber = value => Date.parse(`${value}T00:00:00Z`);
export const dateString = value => new Date(value).toISOString().slice(0, 10);
export const shiftDay = (value, days) => dateString(dateNumber(value) + days * DAY);

export function formatRating(value, digits = 2) {
  return Number(value).toLocaleString('zh-CN', { useGrouping: false, minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function boardChangeKey(board, period) {
  return `${board === 'gainers' || board === 'losers' ? 'gain' : 'delta'}_${period}d`;
}

export function historyMetrics(history) {
  let previous = 0;
  return new Map(history.points.map(point => {
    const value = point[1];
    const metric = { value, delta: value - previous, performance: point[2] ?? null };
    previous = value;
    return [point[0], metric];
  }));
}

export function rankingPage(rows, board, requestedPage, size = 30) {
  const totalPages = board === 'rating' ? Math.max(1, Math.ceil(rows.length / size)) : 1;
  const page = Math.max(0, Math.min(Math.trunc(requestedPage) || 0, totalPages - 1));
  return { page, totalPages, items: rows.slice(page * size, (page + 1) * size) };
}

export function rankingInfo(player, matureCount) {
  return player.provisional ? { rank: null, percentile: null } : {
    rank: player.rank,
    percentile: (matureCount - player.rank) / matureCount * 100,
  };
}

export function filterPlayers(players, aliases, filters, recentStart) {
  const needle = filters.query.trim().normalize('NFKC').toLocaleLowerCase();
  const deltaKey = boardChangeKey(filters.board, filters.period);
  return players.filter(player => {
    if (filters.board !== 'rating' && player.provisional) return false;
    if (filters.status === 'mature' && player.provisional) return false;
    if (filters.status === 'provisional' && !player.provisional) return false;
    if (filters.activity === '30' && (!player.last_rated_day || player.last_rated_day < recentStart)) return false;
    if (filters.tier !== 'all' && player.rating_tier !== filters.tier) return false;
    if (filters.board === 'gainers' && !(player[deltaKey] > 0)) return false;
    if (filters.board === 'losers' && !(player[deltaKey] < 0)) return false;
    if (!needle) return true;
    return [player.nickname || '', ...(aliases[player.id] || [])]
      .some(name => name.normalize('NFKC').toLocaleLowerCase().includes(needle));
  }).sort((a, b) => {
    if (filters.board === 'gainers') return b[deltaKey] - a[deltaKey] || a.rank - b.rank;
    if (filters.board === 'losers') return a[deltaKey] - b[deltaKey] || a.rank - b.rank;
    // Keep the published tie order, which also includes unranked provisional players.
    return b.display_rating - a.display_rating;
  });
}

export function axisRange(values) {
  const min = Math.min(...values), max = Math.max(...values);
  const span = Math.max(max - min, 80);
  const lo = min - span * 0.13, hi = max + span * 0.13;
  const rough = (hi - lo) / 5;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].find(value => value * magnitude >= rough) * magnitude;
  const lower = Math.floor(lo / step) * step;
  const upper = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let value = lower; value <= upper + step / 100; value += step) ticks.push(value);
  return { min: lower, max: upper, step, ticks };
}

export function curvePoints(points, period, endDay) {
  if (!points.length) return [];
  const start = period === 'all' ? points[0][0] : shiftDay(endDay, 1 - Number(period));
  let previous;
  const chosen = [];
  for (const point of points) {
    if (point[0] < start) previous = point;
    else if (point[0] <= endDay) chosen.push({ point, synthetic: false });
  }
  if (previous && (!chosen.length || chosen[0].point[0] > start)) {
    chosen.unshift({ point: [start, ...previous.slice(1)], synthetic: true });
  }
  if (chosen.length && chosen.at(-1).point[0] < endDay) {
    chosen.push({ point: [endDay, ...chosen.at(-1).point.slice(1)], synthetic: true });
  }
  return chosen;
}

export function parseRoute(hash) {
  if (hash === '#rules') return { view: 'rules' };
  const match = /^#player\/([a-f0-9]{24})$/.exec(hash);
  return match ? { view: 'player', id: match[1] } : { view: 'home' };
}

export function nearestPointIndex(times, target) {
  if (!times.length) return -1;
  let lo = 0, hi = times.length - 1;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (times[mid] < target) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && target - times[lo - 1] <= times[lo] - target) return lo - 1;
  return lo;
}

export function selectionRange(times, start, end) {
  if (times.length < 2) return null;
  let first = nearestPointIndex(times, Math.min(start, end));
  let last = nearestPointIndex(times, Math.max(start, end));
  if (first === last) {
    if (last < times.length - 1) last++;
    else first--;
  }
  return { min: times[first], max: times[last] };
}
