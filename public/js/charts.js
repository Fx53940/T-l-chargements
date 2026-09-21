// Mini charting SVG, sans dépendance externe (dashboard auto-hébergé, pas de CDN requis).

function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function fmtDay(day) {
  const d = new Date(day + 'T00:00:00');
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' });
}

function makeTooltip(container) {
  let el = container.querySelector(':scope > .chart-tooltip');
  if (!el) {
    el = document.createElement('div');
    el.className = 'chart-tooltip';
    container.appendChild(el);
  }
  return el;
}

// data: [{ x: isoString, y: number }]
function renderLineChart(container, data, { width = 480, height = 160, yFormat = (v) => v, xFormat = fmtTime } = {}) {
  container.innerHTML = '';
  container.classList.add('chart-wrap');

  if (!data.length) {
    container.innerHTML = '<div class="empty-state">Pas encore de données</div>';
    return;
  }

  const padding = { top: 10, right: 10, bottom: 20, left: 36 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const ys = data.map((d) => d.y ?? 0);
  const minY = Math.min(0, ...ys);
  const maxY = Math.max(...ys, 1);

  const xAt = (i) => padding.left + (i / Math.max(data.length - 1, 1)) * innerW;
  const yAt = (v) => padding.top + innerH - ((v - minY) / (maxY - minY || 1)) * innerH;

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('class', 'chart');

  const gridCount = 3;
  for (let g = 0; g <= gridCount; g++) {
    const v = minY + ((maxY - minY) * g) / gridCount;
    const y = yAt(v);
    const line = document.createElementNS(svgNS, 'line');
    line.setAttribute('x1', padding.left);
    line.setAttribute('x2', width - padding.right);
    line.setAttribute('y1', y);
    line.setAttribute('y2', y);
    line.setAttribute('class', 'gridline');
    svg.appendChild(line);

    const label = document.createElementNS(svgNS, 'text');
    label.setAttribute('x', 4);
    label.setAttribute('y', y + 3);
    label.setAttribute('class', 'axis-label');
    label.textContent = yFormat(v);
    svg.appendChild(label);
  }

  const pathD = data.map((d, i) => `${i === 0 ? 'M' : 'L'} ${xAt(i)} ${yAt(d.y ?? 0)}`).join(' ');
  const areaD = `${pathD} L ${xAt(data.length - 1)} ${yAt(minY)} L ${xAt(0)} ${yAt(minY)} Z`;

  const area = document.createElementNS(svgNS, 'path');
  area.setAttribute('d', areaD);
  area.setAttribute('class', 'area-series');
  svg.appendChild(area);

  const path = document.createElementNS(svgNS, 'path');
  path.setAttribute('d', pathD);
  path.setAttribute('class', 'line-series');
  svg.appendChild(path);

  const tooltip = makeTooltip(container);
  const hitLayer = document.createElementNS(svgNS, 'g');

  data.forEach((d, i) => {
    const cx = xAt(i);
    const cy = yAt(d.y ?? 0);
    const hit = document.createElementNS(svgNS, 'rect');
    const slice = innerW / data.length;
    hit.setAttribute('x', padding.left + i * slice);
    hit.setAttribute('y', padding.top);
    hit.setAttribute('width', Math.max(slice, 1));
    hit.setAttribute('height', innerH);
    hit.setAttribute('fill', 'transparent');
    hit.addEventListener('mouseenter', () => {
      const dot = document.createElementNS(svgNS, 'circle');
      dot.setAttribute('cx', cx);
      dot.setAttribute('cy', cy);
      dot.setAttribute('r', 4);
      dot.setAttribute('class', 'dot');
      dot.setAttribute('data-hover-dot', '1');
      svg.appendChild(dot);
      tooltip.style.display = 'block';
      tooltip.style.left = `${(cx / width) * 100}%`;
      tooltip.style.top = `${(cy / height) * 100}%`;
      tooltip.innerHTML = `<strong>${yFormat(d.y ?? 0)}</strong><br>${xFormat(d.x)}`;
    });
    hit.addEventListener('mouseleave', () => {
      svg.querySelectorAll('[data-hover-dot]').forEach((n) => n.remove());
      tooltip.style.display = 'none';
    });
    hitLayer.appendChild(hit);
  });
  svg.appendChild(hitLayer);

  container.appendChild(svg);
}

// data: [{ label: string, value: number }]
function renderBarChart(container, data, { width = 480, height = 160, yFormat = (v) => v } = {}) {
  container.innerHTML = '';
  container.classList.add('chart-wrap');

  if (!data.length) {
    container.innerHTML = '<div class="empty-state">Pas encore de données</div>';
    return;
  }

  const padding = { top: 10, right: 10, bottom: 26, left: 36 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const maxV = Math.max(...data.map((d) => d.value), 1);
  const slice = innerW / data.length;
  const barW = Math.max(Math.min(slice * 0.6, 36), 4);

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('class', 'chart');

  const baseline = document.createElementNS(svgNS, 'line');
  baseline.setAttribute('x1', padding.left);
  baseline.setAttribute('x2', width - padding.right);
  baseline.setAttribute('y1', padding.top + innerH);
  baseline.setAttribute('y2', padding.top + innerH);
  baseline.setAttribute('class', 'baseline');
  svg.appendChild(baseline);

  const tooltip = makeTooltip(container);

  data.forEach((d, i) => {
    const h = (d.value / maxV) * innerH;
    const x = padding.left + i * slice + (slice - barW) / 2;
    const y = padding.top + innerH - h;

    const rect = document.createElementNS(svgNS, 'rect');
    rect.setAttribute('x', x);
    rect.setAttribute('y', y);
    rect.setAttribute('width', barW);
    rect.setAttribute('height', Math.max(h, 1));
    rect.setAttribute('rx', 3);
    rect.setAttribute('class', 'bar');
    rect.addEventListener('mouseenter', () => {
      tooltip.style.display = 'block';
      tooltip.style.left = `${((x + barW / 2) / width) * 100}%`;
      tooltip.style.top = `${(y / height) * 100}%`;
      tooltip.innerHTML = `<strong>${yFormat(d.value)}</strong><br>${d.label}`;
    });
    rect.addEventListener('mouseleave', () => { tooltip.style.display = 'none'; });
    svg.appendChild(rect);

    if (data.length <= 12) {
      const label = document.createElementNS(svgNS, 'text');
      label.setAttribute('x', x + barW / 2);
      label.setAttribute('y', height - 6);
      label.setAttribute('text-anchor', 'middle');
      label.setAttribute('class', 'axis-label');
      label.textContent = d.label.length > 10 ? d.label.slice(0, 9) + '…' : d.label;
      svg.appendChild(label);
    }
  });

  container.appendChild(svg);
}
