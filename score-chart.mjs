import { DAY, dateNumber, dateString, axisRange, curvePoints, nearestPointIndex, selectionRange, historyMetrics } from './rating-core.mjs';

const formatScore = value => Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function drawScoreChart(container, { history, tiers, period, endDay, zoom, onZoom }) {
  const score = point => point[1];
  const metrics = historyMetrics(history);
  const source = curvePoints(history.points, period, endDay).map(item => ({ ...item, time: dateNumber(item.point[0]), value: score(item.point) }));
  const selected = zoom ? source.filter(item => item.time >= zoom.min && item.time <= zoom.max) : source;
  if (!selected.length) {
    container.innerHTML = '<div class="empty-state">这一时段尚无等级分记录。</div>';
    return;
  }
  const values = selected.map(item => item.value), times = selected.map(item => item.time);
  const range = axisRange(values);
  const compact = container.clientWidth < 600;
  const W = compact ? Math.max(360, Math.round(container.clientWidth)) : 1000;
  const H = compact ? 300 : 340, left = compact ? 45 : 63, right = compact ? 24 : 32, top = 32, bottom = 45;
  const width = W - left - right, height = H - top - bottom;
  let xMin = selected[0].time, xMax = selected.at(-1).time;
  if (xMin === xMax) { xMin -= DAY / 2; xMax += DAY / 2; }
  const x = time => left + (time - xMin) / (xMax - xMin) * width;
  const y = value => top + (range.max - value) / (range.max - range.min) * height;
  const bands = tiers.map(tier => {
    const lo = Math.max(range.min, tier.lower_bound ?? range.min);
    const hi = Math.min(range.max, tier.upper_bound ?? range.max);
    return lo < hi ? `<rect x="${left}" y="${y(hi)}" width="${width}" height="${y(lo) - y(hi)}" fill="${tier.color}" fill-opacity="0.09"/>` : '';
  }).join('');
  const grid = range.ticks.map(tick => `<line class="chart-grid" x1="${left}" x2="${W - right}" y1="${y(tick)}" y2="${y(tick)}"/><text class="chart-axis" x="${left - 12}" y="${y(tick) + 4}" text-anchor="end">${Number(tick).toLocaleString('zh-CN')}</text>`).join('');
  const boundaries = tiers.filter(tier => tier.lower_bound != null && tier.lower_bound > range.min && tier.lower_bound < range.max)
    .map(tier => `<line x1="${left}" x2="${W - right}" y1="${y(tier.lower_bound)}" y2="${y(tier.lower_bound)}" stroke="${tier.color}" stroke-opacity="0.35" stroke-dasharray="5 4"/>`).join('');
  const tickCount = compact ? 3 : 5;
  const tickDays = [...new Set(Array.from({ length: tickCount }, (_, i) => dateString(Math.round((xMin + (xMax - xMin) * i / (tickCount - 1)) / DAY) * DAY)))].filter(day => dateNumber(day) >= xMin && dateNumber(day) <= xMax);
  const xGrid = tickDays.map(day => `<line class="chart-grid" x1="${x(dateNumber(day))}" x2="${x(dateNumber(day))}" y1="${top}" y2="${H - bottom}"/><text class="chart-axis" x="${x(dateNumber(day))}" y="${H - bottom + 28}" text-anchor="middle">${day.slice(5).replace('-', '/')}</text>`).join('');
  const path = selected.map((item, i) => `${i ? 'L' : 'M'} ${x(item.time)} ${y(item.value)}`).join(' ');
  const highest = history.points.reduce((best, point) => !best || score(point) > score(best) ? point : best, null);
  let peak = '';
  if (highest && dateNumber(highest[0]) >= xMin && dateNumber(highest[0]) <= xMax) {
    const px = x(dateNumber(highest[0])), py = y(score(highest));
    peak = `<path class="chart-peak-marker" d="M ${px} ${py - 5} L ${px + 5} ${py + 4} L ${px - 5} ${py + 4} Z"/>`;
  }
  container.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="等级分曲线，纵轴 ${range.min} 至 ${range.max}" data-y-min="${range.min}" data-y-max="${range.max}" data-x-min="${xMin}" data-x-max="${xMax}" data-point-count="${selected.length}">${bands}${grid}${xGrid}${boundaries}<path class="chart-line" d="${path}"/>${peak}<rect id="chart-selection" class="chart-selection" x="${left}" y="${top}" width="0" height="${height}" hidden/><line id="chart-hover-line" x1="0" x2="0" y1="${top}" y2="${H - bottom}" stroke="#1d5665" stroke-dasharray="3 4" hidden/></svg><div class="chart-tooltip" hidden></div>`;
  const svg = container.querySelector('svg'), tooltip = container.querySelector('.chart-tooltip');
  const hover = container.querySelector('#chart-hover-line'), selection = container.querySelector('#chart-selection');
  const position = event => {
    const rect = svg.getBoundingClientRect();
    return { x: (event.clientX - rect.left) / rect.width * W, y: (event.clientY - rect.top) / rect.height * H, rect };
  };
  const inside = pos => pos.x >= left && pos.x <= W - right && pos.y >= top && pos.y <= H - bottom;
  const clampX = value => Math.max(left, Math.min(W - right, value));
  const timeAt = value => xMin + (clampX(value) - left) / width * (xMax - xMin);
  const hideHover = () => { tooltip.hidden = true; hover.setAttribute('hidden', ''); };
  const showHover = event => {
    const pos = position(event);
    if (!inside(pos)) { hideHover(); return; }
    const item = selected[nearestPointIndex(times, timeAt(pos.x))], pointX = x(item.time);
    hover.removeAttribute('hidden');
    hover.setAttribute('x1', pointX); hover.setAttribute('x2', pointX);
    hover.dataset.day = item.point[0];
    const metric = item.synthetic ? { delta: 0, performance: null } : metrics.get(item.point[0]);
    const change = `${metric.delta > 0 ? '+' : ''}${formatScore(metric.delta)}`;
    tooltip.innerHTML = `<strong>${item.point[0]}</strong><div><b>${formatScore(item.value)}</b> (${change})</div><div>表现分：${metric.performance == null ? '—' : formatScore(metric.performance)}</div>`;
    tooltip.hidden = false;
    const screenX = pointX / W * pos.rect.width;
    tooltip.style.left = `${Math.min(Math.max(screenX + 12, 8), Math.max(8, pos.rect.width - tooltip.offsetWidth - 10))}px`;
    tooltip.style.top = '18px';
  };
  let drag = null;
  svg.addEventListener('pointerdown', event => {
    const pos = position(event);
    if (event.button !== 0 || event.pointerType === 'touch' || !inside(pos) || times.length < 2) return;
    event.preventDefault();
    drag = { start: clampX(pos.x), current: clampX(pos.x), id: event.pointerId };
    svg.setPointerCapture(event.pointerId);
    hideHover(); selection.removeAttribute('hidden');
    selection.setAttribute('x', drag.start); selection.setAttribute('width', 0);
  });
  svg.addEventListener('pointermove', event => {
    if (!drag) { showHover(event); return; }
    drag.current = clampX(position(event).x);
    selection.setAttribute('x', Math.min(drag.start, drag.current));
    selection.setAttribute('width', Math.abs(drag.current - drag.start));
  });
  svg.addEventListener('pointerup', event => {
    if (!drag) return;
    const finished = drag; drag = null;
    if (svg.hasPointerCapture(finished.id)) svg.releasePointerCapture(finished.id);
    selection.setAttribute('hidden', '');
    if (Math.abs(finished.current - finished.start) > 8) {
      const next = selectionRange(times, timeAt(finished.start), timeAt(finished.current));
      if (next && (next.min !== xMin || next.max !== xMax)) onZoom(next);
    } else showHover(event);
  });
  svg.addEventListener('pointercancel', () => { drag = null; selection.setAttribute('hidden', ''); hideHover(); });
  svg.addEventListener('pointerleave', () => { if (!drag) hideHover(); });
}
