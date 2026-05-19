var sharedTooltip = (function() {
  var el = document.createElement('div');
  el.className = 'bar-tooltip';
  document.body.appendChild(el);
  return el;
})();

function showSharedTooltip(text, screenX, screenY) {
  sharedTooltip.textContent = text;
  sharedTooltip.style.top = screenY + 'px';
  sharedTooltip.classList.add('visible');
  // Clamp so the tooltip never overflows the viewport.
  var tipW = sharedTooltip.offsetWidth;
  var minX = tipW / 2 + 8;
  var maxX = window.innerWidth - tipW / 2 - 8;
  sharedTooltip.style.left = Math.max(minX, Math.min(screenX, maxX)) + 'px';
}
function hideSharedTooltip() { sharedTooltip.classList.remove('visible'); }
window.addEventListener('scroll', hideSharedTooltip, { passive: true });

var SVG_NS = 'http://www.w3.org/2000/svg';


function rangeBounds(range, dataMinTs, dataMaxTs) {
  var nowMs = Date.now();
  var nowTs = Math.floor(nowMs / 1000);
  if (range === 'All') {
    // Hug data span so a single point isn't lost in empty axis.
    if (dataMinTs == null) return { startTs: 0, endTs: nowTs };
    return { startTs: dataMinTs, endTs: dataMaxTs };
  }
  if (range === 'YTD') {
    var jan1 = new Date(new Date().getFullYear(), 0, 1);
    return { startTs: Math.floor(jan1.getTime() / 1000), endTs: nowTs };
  }
  if (range === '30D') return { startTs: nowTs - 30 * 86400, endTs: nowTs };
  if (range === '3M') {
    var d3 = new Date(); d3.setMonth(d3.getMonth() - 3);
    return { startTs: Math.floor(d3.getTime() / 1000), endTs: nowTs };
  }
  if (range === '6M') {
    var d6 = new Date(); d6.setMonth(d6.getMonth() - 6);
    return { startTs: Math.floor(d6.getTime() / 1000), endTs: nowTs };
  }
  return { startTs: 0, endTs: nowTs };
}


function buildNetworthGeometry(points, width, height, rangeStart, rangeEnd) {
  if (points.length === 0) return null;
  var padX = 4, padY = 10;
  var plotW = width - 2 * padX;
  var plotH = height - 2 * padY;
  var xSpan = Math.max(1, rangeEnd - rangeStart);

  var firstRealTs = points[0].ts;
  var hasSyntheticPrefix = firstRealTs > rangeStart;

  var pathPoints = points.slice();
  if (hasSyntheticPrefix) {
    pathPoints = [
      { ts: rangeStart, value: 0, synthetic: true },
      { ts: firstRealTs, value: 0, synthetic: true },
    ].concat(pathPoints);
  }

  var pathValues = pathPoints.map(function(p) { return p.value; });
  var yMin = Math.min.apply(null, pathValues);
  var yMax = Math.max.apply(null, pathValues);
  var ySpan = Math.max(1, yMax - yMin);

  function toX(ts) { return padX + (ts - rangeStart) / xSpan * plotW; }
  function toY(v) {
    if (yMax === yMin) return padY + plotH / 2;
    return padY + (yMax - v) / ySpan * plotH;
  }

  var rendered = pathPoints.map(function(p) {
    return {
      x: toX(p.ts), y: toY(p.value),
      ts: p.ts, value: p.value, label: p.label,
      synthetic: !!p.synthetic,
    };
  });

  function buildLinePath(slice) {
    if (slice.length < 2) return '';
    return 'M ' + slice.map(function(p) {
      return p.x.toFixed(2) + ',' + p.y.toFixed(2);
    }).join(' L ');
  }

  var baseY = padY + plotH;
  var areaPath = '';
  if (rendered.length >= 2) {
    areaPath = 'M ' + rendered[0].x.toFixed(2) + ',' + baseY.toFixed(2) + ' ' +
      rendered.map(function(p) {
        return 'L ' + p.x.toFixed(2) + ',' + p.y.toFixed(2);
      }).join(' ') +
      ' L ' + rendered[rendered.length - 1].x.toFixed(2) + ',' + baseY.toFixed(2) + ' Z';
  }

  // Include first real point in the synth slice so the spike-up is drawn green.
  var synthSlice = [], realSlice = [];
  for (var i = 0; i < rendered.length; i++) {
    if (rendered[i].synthetic) {
      synthSlice.push(rendered[i]);
    } else {
      if (synthSlice.length) synthSlice.push(rendered[i]);
      realSlice.push(rendered[i]);
    }
  }

  var hoverPoints = rendered.filter(function(p) { return !p.synthetic; });
  var realValues = points.map(function(p) { return p.value; });
  return {
    areaPath: areaPath,
    lineSynthPath: buildLinePath(synthSlice),
    lineRealPath: buildLinePath(realSlice),
    points: hoverPoints,
    // Red only when net worth itself is negative, not when it merely trended down.
    trend: realValues[realValues.length - 1] < 0 ? 'down' : 'up',
    hasSynthetic: hasSyntheticPrefix,
    rangeStart: rangeStart,
    rangeEnd: rangeEnd,
    firstRealTs: firstRealTs,
    baseY: baseY,
    padX: padX,
    plotW: plotW,
  };
}

(function() {
  var data = window.NETWORTH_CHART;
  if (!data || !data.points || data.points.length < 1) return;
  var card = document.querySelector('.networth-chart');
  var svg = card && card.querySelector('svg');
  if (!svg) return;

  var areaPath = svg.querySelector('.networth-area');
  // Two line elements: synth segment (always green) + real segment (trend-colored).
  var existingLine = svg.querySelector('.networth-line');
  if (existingLine) existingLine.remove();
  var lineSynth = document.createElementNS(SVG_NS, 'path');
  lineSynth.setAttribute('class', 'networth-line-synth');
  lineSynth.setAttribute('fill', 'none');
  lineSynth.setAttribute('stroke-width', '2');
  lineSynth.setAttribute('stroke-linecap', 'round');
  lineSynth.setAttribute('stroke-linejoin', 'round');
  lineSynth.setAttribute('vector-effect', 'non-scaling-stroke');
  svg.appendChild(lineSynth);
  var lineReal = document.createElementNS(SVG_NS, 'path');
  lineReal.setAttribute('class', 'networth-line-real');
  lineReal.setAttribute('fill', 'none');
  lineReal.setAttribute('stroke-width', '2');
  lineReal.setAttribute('stroke-linecap', 'round');
  lineReal.setAttribute('stroke-linejoin', 'round');
  lineReal.setAttribute('vector-effect', 'non-scaling-stroke');
  svg.appendChild(lineReal);

  var dot = document.createElement('div');
  dot.className = 'networth-dot';
  dot.style.display = 'none';
  document.body.appendChild(dot);

  var renderedPoints = [];
  var renderedGeo = null;

  function dataMinMaxTs() {
    var tsList = data.points.map(function(p) { return p.ts; });
    return { min: Math.min.apply(null, tsList), max: Math.max.apply(null, tsList) };
  }

  function updateDelta(points, hasSynthetic) {
    var el = document.getElementById('networth-delta');
    if (!el) return;
    el.classList.remove('delta-up', 'delta-down');
    if (!points || points.length === 0) { el.textContent = ''; return; }
    // With a synthetic 0 prefix, compare against that baseline; otherwise need
    // two real points to compute a meaningful delta.
    if (!hasSynthetic && points.length < 2) { el.textContent = ''; return; }
    var start = hasSynthetic ? 0 : points[0].value;
    var delta = points[points.length - 1].value - start;
    if (delta === 0) { el.textContent = ''; return; }
    var sign = delta > 0 ? '+' : '−';
    el.textContent = sign + '$' + Math.abs(delta).toLocaleString('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
    el.classList.add(delta > 0 ? 'delta-up' : 'delta-down');
  }

  function render(range) {
    var dm = dataMinMaxTs();
    var b = rangeBounds(range, dm.min, dm.max);
    var filtered = data.points.filter(function(p) {
      return p.ts >= b.startTs && p.ts <= b.endTs;
    });
    var geo = buildNetworthGeometry(filtered, data.width, data.height, b.startTs, b.endTs);
    if (!geo) {
      areaPath.setAttribute('d', '');
      lineSynth.setAttribute('d', '');
      lineReal.setAttribute('d', '');
      renderedPoints = [];
      renderedGeo = null;
      updateDelta(null, false);
      return;
    }
    areaPath.setAttribute('d', geo.areaPath);
    lineSynth.setAttribute('d', geo.lineSynthPath);
    lineReal.setAttribute('d', geo.lineRealPath);
    card.classList.remove('trend-up', 'trend-down');
    card.classList.add('trend-' + geo.trend);
    renderedPoints = geo.points;
    renderedGeo = geo;
    updateDelta(filtered, geo.hasSynthetic);
  }

  function nearest(svgX) {
    if (renderedPoints.length === 0) return null;
    var n = renderedPoints[0];
    var minDist = Math.abs(n.x - svgX);
    for (var i = 1; i < renderedPoints.length; i++) {
      var d = Math.abs(renderedPoints[i].x - svgX);
      if (d < minDist) { minDist = d; n = renderedPoints[i]; }
    }
    return n;
  }

  function syntheticDateLabel(ts) {
    var d = new Date(ts * 1000);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function handleHoverAt(clientX) {
    if (!renderedGeo) return;
    var rect = svg.getBoundingClientRect();
    if (rect.width === 0) return;
    var svgX = (clientX - rect.left) / rect.width * data.width;

    var cursorTs = renderedGeo.rangeStart +
      (svgX - renderedGeo.padX) / renderedGeo.plotW *
      (renderedGeo.rangeEnd - renderedGeo.rangeStart);

    var px, py, label, value, isSynthetic;
    if (renderedGeo.hasSynthetic && cursorTs < renderedGeo.firstRealTs) {
      isSynthetic = true;
      px = svgX;
      py = renderedGeo.baseY;
      value = 0;
      var dispTs = Math.max(cursorTs, renderedGeo.rangeStart);
      label = syntheticDateLabel(Math.floor(dispTs));
    } else {
      var p = nearest(svgX);
      if (!p) return;
      isSynthetic = false;
      px = p.x; py = p.y; value = p.value; label = p.label;
    }

    var screenX = rect.left + (px / data.width) * rect.width;
    var screenY = rect.top + (py / data.height) * rect.height;
    dot.style.left = screenX + 'px';
    dot.style.top = screenY + 'px';
    dot.style.display = '';
    dot.style.borderColor = isSynthetic
      ? 'var(--positive)'
      : (renderedGeo.trend === 'up' ? 'var(--positive)' : 'var(--negative)');
    showSharedTooltip(
      label + ': $' + value.toLocaleString('en-US', {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      }),
      screenX, screenY
    );
  }

  function hideHover() {
    dot.style.display = 'none';
    hideSharedTooltip();
  }

  svg.addEventListener('mousemove', function(e) { handleHoverAt(e.clientX); });
  svg.addEventListener('mouseleave', hideHover);

  svg.addEventListener('touchstart', function(e) {
    if (e.touches.length > 0) handleHoverAt(e.touches[0].clientX);
  }, { passive: true });
  svg.addEventListener('touchmove', function(e) {
    if (e.touches.length > 0) {
      handleHoverAt(e.touches[0].clientX);
      // preventDefault stops page-scroll while scrubbing.
      e.preventDefault();
    }
  }, { passive: false });
  svg.addEventListener('touchend', hideHover);
  svg.addEventListener('touchcancel', hideHover);
  window.addEventListener('scroll', hideHover, { passive: true });

  var filter = card.querySelector('.chart-range-filter');
  if (filter) {
    filter.addEventListener('click', function(e) {
      var btn = e.target.closest('.chart-range-btn');
      if (!btn) return;
      filter.querySelectorAll('.chart-range-btn').forEach(function(b) {
        b.classList.remove('active');
      });
      btn.classList.add('active');
      render(btn.dataset.range);
    });
    var initial = filter.querySelector('.chart-range-btn.active');
    render(initial ? initial.dataset.range : '30D');
  } else {
    render('30D');
  }
})();


function roundedTopPath(x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h));
  return 'M ' + x.toFixed(2) + ',' + (y + r).toFixed(2) +
    ' Q ' + x.toFixed(2) + ',' + y.toFixed(2) + ' ' + (x + r).toFixed(2) + ',' + y.toFixed(2) +
    ' L ' + (x + w - r).toFixed(2) + ',' + y.toFixed(2) +
    ' Q ' + (x + w).toFixed(2) + ',' + y.toFixed(2) + ' ' + (x + w).toFixed(2) + ',' + (y + r).toFixed(2) +
    ' L ' + (x + w).toFixed(2) + ',' + (y + h).toFixed(2) +
    ' L ' + x.toFixed(2) + ',' + (y + h).toFixed(2) + ' Z';
}

function roundedBottomPath(x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h));
  return 'M ' + x.toFixed(2) + ',' + y.toFixed(2) +
    ' L ' + (x + w).toFixed(2) + ',' + y.toFixed(2) +
    ' L ' + (x + w).toFixed(2) + ',' + (y + h - r).toFixed(2) +
    ' Q ' + (x + w).toFixed(2) + ',' + (y + h).toFixed(2) + ' ' + (x + w - r).toFixed(2) + ',' + (y + h).toFixed(2) +
    ' L ' + (x + r).toFixed(2) + ',' + (y + h).toFixed(2) +
    ' Q ' + x.toFixed(2) + ',' + (y + h).toFixed(2) + ' ' + x.toFixed(2) + ',' + (y + h - r).toFixed(2) +
    ' Z';
}

function buildDivergingGeometry(totals, width, height) {
  if (!totals.length) return { bars: [], zeroY: 0 };
  var padX = 4, padY = 12;
  var plotW = width - 2 * padX;
  var plotH = height - 2 * padY;

  var maxIncome = 0, maxSpend = 0;
  totals.forEach(function(t) {
    if (t.income > maxIncome) maxIncome = t.income;
    if (t.spend > maxSpend) maxSpend = t.spend;
  });
  if (maxIncome === 0 && maxSpend === 0) { maxIncome = 1; }

  var yMax = maxIncome;
  var yMin = -maxSpend;
  var ySpan = Math.max(1, yMax - yMin);
  function toY(v) { return padY + (yMax - v) / ySpan * plotH; }
  var zeroY = toY(0);

  var n = totals.length;
  var barGap = 8;
  var barW = (plotW - barGap * (n - 1)) / n;

  var bars = [];
  totals.forEach(function(t, i) {
    var x = padX + i * (barW + barGap);
    if (t.income > 0) {
      var topY = toY(t.income);
      var h = zeroY - topY;
      bars.push({
        path: roundedTopPath(x, topY, barW, h, 6),
        kind: 'income',
        label: t.label,
        month: t.month,
        amount: t.income,
      });
    }
    if (t.spend > 0) {
      var bottomY = toY(-t.spend);
      var h2 = bottomY - zeroY;
      bars.push({
        path: roundedBottomPath(x, zeroY, barW, h2, 6),
        kind: 'spend',
        label: t.label,
        month: t.month,
        amount: t.spend,
      });
    }
    // Net overlay paints on top of the gross bar.
    var net = t.income - t.spend;
    if (net > 0) {
      var nTopY = toY(net);
      var nH = zeroY - nTopY;
      bars.push({
        path: roundedTopPath(x, nTopY, barW, nH, 6),
        kind: 'net-positive',
        label: t.label,
        month: t.month,
        amount: net,
      });
    } else if (net < 0) {
      var nBottomY = toY(net);
      var nH2 = nBottomY - zeroY;
      bars.push({
        path: roundedBottomPath(x, zeroY, barW, nH2, 6),
        kind: 'net-negative',
        label: t.label,
        month: t.month,
        amount: -net,
      });
    }
  });
  return { bars: bars, zeroY: zeroY, width: width, height: height };
}

(function() {
  var data = window.MONTHLY_CHART;
  if (!data || !data.totals) return;
  var card = document.querySelector('.monthly-spend-chart');
  var svg = card && card.querySelector('svg');
  if (!svg) return;

  function render(range) {
    var b = rangeBounds(range, null, null);
    var filtered = data.totals.filter(function(t) {
      return t.ts >= b.startTs && (t.spend > 0 || t.income > 0);
    });
    var geo = buildDivergingGeometry(filtered, data.width, data.height);
    svg.querySelectorAll('.bar, .zero-line').forEach(function(el) { el.remove(); });

    if (filtered.length) {
      var zero = document.createElementNS(SVG_NS, 'line');
      zero.setAttribute('class', 'zero-line');
      zero.setAttribute('x1', 0);
      zero.setAttribute('x2', geo.width);
      zero.setAttribute('y1', geo.zeroY);
      zero.setAttribute('y2', geo.zeroY);
      zero.setAttribute('vector-effect', 'non-scaling-stroke');
      svg.appendChild(zero);
    }

    geo.bars.forEach(function(bar) {
      var path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('class', 'bar bar-' + bar.kind);
      path.setAttribute('d', bar.path);
      var sign = '';
      var suffix;
      if (bar.kind === 'income') suffix = 'Earned';
      else if (bar.kind === 'spend') suffix = 'Spent';
      else if (bar.kind === 'net-positive') { suffix = 'Net'; sign = '+'; }
      else { suffix = 'Net'; sign = '−'; }
      var amount = sign + '$' + bar.amount.toLocaleString('en-US', {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      });
      path.setAttribute('data-tooltip', bar.label + ': ' + amount + ' ' + suffix);
      if (bar.kind === 'income' || bar.kind === 'spend') {
        path.dataset.kind = bar.kind;
        path.dataset.month = bar.month;
        path.style.cursor = 'pointer';
      }
      svg.appendChild(path);
    });
  }

  card.addEventListener('click', function(e) {
    var bar = e.target.closest('.bar-income, .bar-spend');
    if (!bar || !bar.dataset.month) return;
    var page = bar.dataset.kind === 'income' ? '/income' : '/spending';
    window.location.href = page + '?month=' + encodeURIComponent(bar.dataset.month);
  });

  card.addEventListener('mouseover', function(e) {
    var bar = e.target.closest('.bar');
    if (!bar) {
      // Moving within the card but off a bar — tooltip should drop.
      hideSharedTooltip();
      return;
    }
    var rect = bar.getBoundingClientRect();
    showSharedTooltip(
      bar.dataset.tooltip,
      rect.left + rect.width / 2,
      rect.top
    );
  });
  card.addEventListener('mouseleave', hideSharedTooltip);

  var filter = card.querySelector('.chart-range-filter');
  if (filter) {
    filter.addEventListener('click', function(e) {
      var btn = e.target.closest('.chart-range-btn');
      if (!btn) return;
      filter.querySelectorAll('.chart-range-btn').forEach(function(b) {
        b.classList.remove('active');
      });
      btn.classList.add('active');
      render(btn.dataset.range);
    });
    var initial = filter.querySelector('.chart-range-btn.active');
    render(initial ? initial.dataset.range : '6M');
  } else {
    render('6M');
  }
})();
