import { DAY, dateNumber, dateString, axisRange, curvePoints, nearestPointIndex, selectionRange, historyMetrics, formatRating } from './rating-core.mjs';

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
  const W = Math.max(240, Math.round(container.clientWidth));
  const H = compact ? 244 : 292, left = compact ? 40 : 50, right = compact ? 21 : 30, top = 16, bottom = 36;
  const width = W - left - right, height = H - top - bottom;
  let xMin = selected[0].time, xMax = selected.at(-1).time;
  if (xMin === xMax) { xMin -= DAY / 2; xMax += DAY / 2; }
  const x = time => left + (time - xMin) / (xMax - xMin) * width;
  const y = value => top + (range.max - value) / (range.max - range.min) * height;
  const bands = tiers.map(tier => {
    const lo = Math.max(range.min, tier.lower_bound ?? range.min);
    const hi = Math.min(range.max, tier.upper_bound ?? range.max);
    return lo < hi ? `<rect x="${left}" y="${y(hi)}" width="${width}" height="${y(lo) - y(hi)}" fill="${tier.color}" fill-opacity=".13"/>` : '';
  }).join('');
  const yLabels = range.ticks.map(tick => `
    <line x1="${left}" x2="${W - right}" y1="${y(tick)}" y2="${y(tick)}" stroke="#195644" stroke-opacity=".14" stroke-dasharray="2 4"/>
    <text class="chart-axis" x="${left - 8}" y="${y(tick) + 4}" text-anchor="end">${Number(tick).toLocaleString('zh-CN', { useGrouping: false })}</text>`).join('');
  const tickCount = compact ? 3 : 5;
  const tickDays = [...new Set(Array.from({ length: tickCount }, (_, i) => dateString(Math.round((xMin + (xMax - xMin) * i / (tickCount - 1)) / DAY) * DAY)))].filter(day => dateNumber(day) >= xMin && dateNumber(day) <= xMax);
  const spansYears = dateString(xMin).slice(0, 4) !== dateString(xMax).slice(0, 4);
  const xLabels = tickDays.map(day => `<text class="chart-axis" x="${x(dateNumber(day))}" y="${H - bottom + 22}" text-anchor="middle">${spansYears ? day.slice(0, 7) : day.slice(5).replace('-', '/')}</text>`).join('');
  const path = selected.map((item, i) => `${i ? 'L' : 'M'} ${x(item.time)} ${y(item.value)}`).join(' ');
  const highest = history.points.reduce((best, point) => !best || score(point) > score(best) ? point : best, null);
  let peak = '';
  if (highest && dateNumber(highest[0]) >= xMin && dateNumber(highest[0]) <= xMax) {
    const px = x(dateNumber(highest[0])), py = y(score(highest));
    peak = `<path class="chart-peak-marker" d="M ${px} ${py - 5} L ${px + 5} ${py + 4} L ${px - 5} ${py + 4} Z"/>`;
  }
  const last = selected.at(-1);
  container.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" tabindex="0" role="img" aria-label="等级分曲线，纵轴 ${range.min} 至 ${range.max}。用左右方向键查看逐日记录。" aria-describedby="chart-point-detail" data-y-min="${range.min}" data-y-max="${range.max}" data-x-min="${xMin}" data-x-max="${xMax}" data-point-count="${selected.length}">
      <rect x="${left}" y="${top}" width="${width}" height="${height}" fill="#faf9f5"/>
      ${bands}${yLabels}${xLabels}
      <path class="chart-line" d="${path}"/>
      <circle cx="${x(last.time)}" cy="${y(last.value)}" r="3.5" fill="#195644" stroke="white" stroke-width="1.5"/>${peak}
      <rect id="chart-selection" class="chart-selection" x="${left}" y="${top}" width="0" height="${height}" hidden/>
      <g id="chart-cursor" hidden>
        <line id="chart-hover-line" x1="0" x2="0" y1="${top}" y2="${H - bottom}" stroke="#195644" stroke-dasharray="3 4"/>
        <circle id="chart-hover-point" r="4" fill="white" stroke="#195644" stroke-width="2"/>
      </g>
    </svg>
    <div id="chart-point-detail" class="chart-tooltip" role="status" hidden></div>`;
  const svg = container.querySelector('svg'), tooltip = container.querySelector('.chart-tooltip');
  const hover = container.querySelector('#chart-hover-line'), selection = container.querySelector('#chart-selection');
  const cursor = container.querySelector('#chart-cursor'), marker = container.querySelector('#chart-hover-point');
  const position = event => {
    const rect = svg.getBoundingClientRect();
    return { x: (event.clientX - rect.left) / rect.width * W, y: (event.clientY - rect.top) / rect.height * H, rect };
  };
  const inside = pos => pos.x >= left && pos.x <= W - right && pos.y >= top && pos.y <= H - bottom;
  const clampX = value => Math.max(left, Math.min(W - right, value));
  const timeAt = value => xMin + (clampX(value) - left) / width * (xMax - xMin);
  let active = -1;
  const hideHover = () => { active = -1; tooltip.hidden = true; cursor.setAttribute('hidden', ''); };
  const showPoint = index => {
    if (index === active) return;
    active = index;
    const item = selected[index], pointX = x(item.time);
    cursor.removeAttribute('hidden');
    hover.setAttribute('x1', pointX); hover.setAttribute('x2', pointX);
    marker.setAttribute('cx', pointX); marker.setAttribute('cy', y(item.value));
    hover.dataset.day = item.point[0];
    const metric = item.synthetic ? { delta: 0, performance: null } : metrics.get(item.point[0]);
    const change = `${metric.delta > 0 ? '+' : ''}${formatRating(metric.delta)}`;
    tooltip.innerHTML = `<strong>${item.point[0]}</strong><div>等级分 <b>${formatRating(item.value)}</b></div><div>当日变化 ${change}</div><div>表现分 ${formatRating(metric.performance)}</div>`;
    tooltip.hidden = false;
    const chartWidth = svg.getBoundingClientRect().width;
    const screenX = pointX / W * chartWidth;
    const tooltipX = screenX + 12 + tooltip.offsetWidth <= chartWidth ? screenX + 12 : screenX - tooltip.offsetWidth - 12;
    tooltip.style.left = `${Math.max(0, tooltipX)}px`;
    tooltip.style.top = `${top}px`;
  };
  const showHover = event => {
    const pos = position(event);
    if (inside(pos)) showPoint(nearestPointIndex(times, timeAt(pos.x)));
    else hideHover();
  };
  svg.addEventListener('blur', hideHover);
  svg.addEventListener('keydown', event => {
    let next;
    switch (event.key) {
      case 'ArrowLeft': next = active < 0 ? selected.length - 1 : Math.max(0, active - 1); break;
      case 'ArrowRight': next = Math.min(selected.length - 1, active + 1); break;
      case 'Home': next = 0; break;
      case 'End': next = selected.length - 1; break;
      case 'Escape': hideHover(); return;
      default: return;
    }
    event.preventDefault();
    showPoint(next);
  });
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
    if (!drag) { if (event.pointerType !== 'touch') showHover(event); return; }
    drag.current = clampX(position(event).x);
    selection.setAttribute('x', Math.min(drag.start, drag.current));
    selection.setAttribute('width', Math.abs(drag.current - drag.start));
  });
  svg.addEventListener('pointerup', event => {
    if (!drag) { if (event.pointerType === 'touch') showHover(event); return; }
    const finished = drag; drag = null;
    if (svg.hasPointerCapture(finished.id)) svg.releasePointerCapture(finished.id);
    selection.setAttribute('hidden', '');
    if (Math.abs(finished.current - finished.start) > 8) {
      const next = selectionRange(times, timeAt(finished.start), timeAt(finished.current));
      if (next && (next.min !== xMin || next.max !== xMax)) onZoom(next);
    } else showHover(event);
  });
  svg.addEventListener('pointercancel', () => { drag = null; selection.setAttribute('hidden', ''); hideHover(); });
  svg.addEventListener('pointerleave', event => { if (!drag && event.pointerType !== 'touch') hideHover(); });
}
