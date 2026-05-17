(function() {
  var svg = document.getElementById('planning-svg');
  var trigger = document.querySelector('.planning-target-trigger');
  var triggerLabel = trigger && trigger.querySelector('.inline-dropdown-label');
  var summaryEl = document.getElementById('planning-summary');
  var inputs = document.querySelectorAll('.planning-rate-input');
  var horizonFilter = document.querySelector('.planning-horizon-filter');
  var incomeInput = document.getElementById('planning-monthly-income');
  var spendInput = document.getElementById('planning-monthly-spend');
  var netFlowEl = document.getElementById('planning-net-flow');
  if (!svg || !trigger || !inputs.length) return;

  var SVG_NS = 'http://www.w3.org/2000/svg';

  function buildTargetOptions() {
    var opts = [{ value: '__net__', label: 'Total net worth' }];
    inputs.forEach(function(input) {
      var row = input.closest('tr');
      var name = row.querySelector('.planning-acct-name');
      opts.push({
        value: input.dataset.account,
        label: name ? name.textContent.trim() : input.dataset.account,
      });
    });
    return opts;
  }

  trigger.addEventListener('click', function(e) {
    e.stopPropagation();
    if (trigger.getAttribute('aria-expanded') === 'true') {
      window.closeInlineDropdowns();
      return;
    }
    window.openInlineDropdown(trigger, buildTargetOptions(), trigger.dataset.value, function(opt) {
      trigger.dataset.value = opt.value;
      triggerLabel.textContent = opt.label;
      render();
    });
  });

  function activeTarget() { return trigger.dataset.value; }
  var WIDTH = 1000, HEIGHT = 220;
  var PAD_X = 8, PAD_Y = 14;

  function activeYears() {
    var btn = horizonFilter.querySelector('.chart-range-btn.active');
    return btn ? parseInt(btn.dataset.years, 10) : 10;
  }

  function snapshotAccounts() {
    return Array.prototype.map.call(inputs, function(input) {
      var rate = parseFloat(input.value);
      if (isNaN(rate)) rate = 0;
      return {
        id: input.dataset.account,
        balance: parseFloat(input.dataset.balance),
        sign: parseInt(input.dataset.sign, 10),  // +1 asset, -1 debt
        rateAnnual: rate,
      };
    });
  }

  function projectSeries(startValue, annualRatePct, months, monthlyContribution) {
    // (1 + r)^(1/12) keeps "annual rate" semantics honest vs. r/12.
    // Contribution applies after growth each month.
    var out = [startValue];
    var monthly = Math.pow(1 + annualRatePct / 100, 1 / 12) - 1;
    var contrib = monthlyContribution || 0;
    var v = startValue;
    for (var m = 1; m <= months; m++) {
      v = v * (1 + monthly) + contrib;
      out.push(v);
    }
    return out;
  }

  function buildSeriesFor(targetId, accounts, months, netMonthly) {
    if (targetId === '__net__') {
      // Net flow rolls into the aggregate, not any one account.
      var series = new Array(months + 1).fill(0);
      accounts.forEach(function(a) {
        var p = projectSeries(a.balance, a.rateAnnual, months, 0);
        for (var i = 0; i <= months; i++) {
          series[i] += a.sign * p[i];
        }
      });
      for (var j = 1; j <= months; j++) {
        series[j] += netMonthly * j;
      }
      return series;
    }
    var acct = accounts.find(function(a) { return a.id === targetId; });
    if (!acct) return [];
    return projectSeries(acct.balance, acct.rateAnnual, months, netMonthly);
  }

  function pathFromSeries(series) {
    if (series.length < 2) return { line: '', area: '' };
    var n = series.length;
    var plotW = WIDTH - 2 * PAD_X;
    var plotH = HEIGHT - 2 * PAD_Y;
    var yMin = Math.min.apply(null, series);
    var yMax = Math.max.apply(null, series);
    var pad = Math.max(1, (yMax - yMin) * 0.05);
    yMin -= pad;
    yMax += pad;
    var ySpan = Math.max(1, yMax - yMin);

    var points = series.map(function(v, i) {
      var x = PAD_X + (i / (n - 1)) * plotW;
      var y = PAD_Y + (yMax - v) / ySpan * plotH;
      return { x: x, y: y, value: v, monthIdx: i };
    });
    var baseY = PAD_Y + plotH;
    var line = 'M ' + points.map(function(p) {
      return p.x.toFixed(2) + ',' + p.y.toFixed(2);
    }).join(' L ');
    var area = 'M ' + points[0].x.toFixed(2) + ',' + baseY.toFixed(2) + ' ' +
      points.map(function(p) {
        return 'L ' + p.x.toFixed(2) + ',' + p.y.toFixed(2);
      }).join(' ') +
      ' L ' + points[n - 1].x.toFixed(2) + ',' + baseY.toFixed(2) + ' Z';
    return { line: line, area: area, points: points, yMax: yMax, yMin: yMin };
  }

  function formatUsd(n) {
    var sign = n < 0 ? '-' : '';
    return sign + '$' + Math.abs(n).toLocaleString('en-US', {
      minimumFractionDigits: 0, maximumFractionDigits: 0,
    });
  }

  function monthLabel(monthIdx) {
    if (monthIdx === 0) return 'Today';
    var d = new Date();
    d.setDate(1);
    d.setMonth(d.getMonth() + monthIdx);
    return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
  }

  // HTML tooltip + dot avoid the SVG's preserveAspectRatio="none" squash.
  var tooltipEl = document.createElement('div');
  tooltipEl.className = 'bar-tooltip';
  document.body.appendChild(tooltipEl);
  var dot = document.createElement('div');
  dot.className = 'networth-dot';
  dot.style.display = 'none';
  document.body.appendChild(dot);

  var renderedPoints = [];
  var renderedTrend = 'up';

  function readNumberInput(el) {
    if (!el) return 0;
    var v = parseFloat(el.value);
    return isNaN(v) ? 0 : v;
  }

  function netMonthlyFlow() {
    return readNumberInput(incomeInput) - readNumberInput(spendInput);
  }

  function updateNetDisplay() {
    if (!netFlowEl) return;
    var net = netMonthlyFlow();
    netFlowEl.textContent = formatUsd(net);
    netFlowEl.style.color = net > 0
      ? 'var(--positive)'
      : (net < 0 ? 'var(--negative)' : '');
  }

  function render() {
    var years = activeYears();
    var months = years * 12;
    var accounts = snapshotAccounts();
    var series = buildSeriesFor(activeTarget(), accounts, months, netMonthlyFlow());
    updateNetDisplay();
    if (!series.length) {
      svg.innerHTML = '';
      summaryEl.textContent = '';
      renderedPoints = [];
      return;
    }
    var paths = pathFromSeries(series);
    var trend = series[series.length - 1] >= series[0] ? 'up' : 'down';

    svg.innerHTML = '';
    var areaEl = document.createElementNS(SVG_NS, 'path');
    areaEl.setAttribute('d', paths.area);
    areaEl.setAttribute('class', 'planning-area planning-trend-' + trend);
    svg.appendChild(areaEl);
    var lineEl = document.createElementNS(SVG_NS, 'path');
    lineEl.setAttribute('d', paths.line);
    lineEl.setAttribute('class', 'planning-line planning-trend-' + trend);
    lineEl.setAttribute('fill', 'none');
    lineEl.setAttribute('stroke-width', '2');
    lineEl.setAttribute('stroke-linecap', 'round');
    lineEl.setAttribute('stroke-linejoin', 'round');
    lineEl.setAttribute('vector-effect', 'non-scaling-stroke');
    svg.appendChild(lineEl);

    renderedPoints = paths.points;
    renderedTrend = trend;

    var label = triggerLabel.textContent;
    summaryEl.textContent = label + ' · today ' + formatUsd(series[0])
      + ' → ' + years + 'y ' + formatUsd(series[series.length - 1]);
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

  function handleHoverAt(clientX) {
    if (!renderedPoints.length) return;
    var rect = svg.getBoundingClientRect();
    if (rect.width === 0) return;
    var svgX = (clientX - rect.left) / rect.width * WIDTH;
    var p = nearest(svgX);
    if (!p) return;

    var screenX = rect.left + (p.x / WIDTH) * rect.width;
    var screenY = rect.top + (p.y / HEIGHT) * rect.height;
    dot.style.left = screenX + 'px';
    dot.style.top = screenY + 'px';
    dot.style.display = '';
    dot.style.borderColor = renderedTrend === 'up'
      ? 'var(--positive)' : 'var(--negative)';

    tooltipEl.textContent = monthLabel(p.monthIdx) + ': ' + formatUsd(p.value);
    tooltipEl.style.top = screenY + 'px';
    tooltipEl.classList.add('visible');
    var tipW = tooltipEl.offsetWidth;
    var minX = tipW / 2 + 8;
    var maxX = window.innerWidth - tipW / 2 - 8;
    tooltipEl.style.left = Math.max(minX, Math.min(screenX, maxX)) + 'px';
  }
  function hideHover() {
    dot.style.display = 'none';
    tooltipEl.classList.remove('visible');
  }

  svg.addEventListener('mousemove', function(e) { handleHoverAt(e.clientX); });
  svg.addEventListener('mouseleave', hideHover);
  svg.addEventListener('touchstart', function(e) {
    if (e.touches.length > 0) handleHoverAt(e.touches[0].clientX);
  }, { passive: true });
  svg.addEventListener('touchmove', function(e) {
    if (e.touches.length > 0) {
      handleHoverAt(e.touches[0].clientX);
      e.preventDefault();
    }
  }, { passive: false });
  svg.addEventListener('touchend', hideHover);
  svg.addEventListener('touchcancel', hideHover);
  window.addEventListener('scroll', hideHover, { passive: true });

  inputs.forEach(function(input) {
    var lastValue = input.value;
    function save() {
      if (input.value === lastValue) return;
      lastValue = input.value;
      var payload = input.value === ''
        ? { rate: null }
        : { rate: parseFloat(input.value) };
      csrfFetch('/planning/rate/' + encodeURIComponent(input.dataset.account), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).catch(function() {});
    }
    input.addEventListener('input', render);
    input.addEventListener('blur', save);
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    });
  });

  function wireCashflowInput(input, field) {
    if (!input) return;
    var lastValue = input.value;
    function save() {
      if (input.value === lastValue) return;
      lastValue = input.value;
      var payload = {
        field: field,
        value: input.value === '' ? null : parseFloat(input.value),
      };
      csrfFetch('/planning/cashflow', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).catch(function() {});
    }
    input.addEventListener('input', render);
    input.addEventListener('blur', save);
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    });
  }
  wireCashflowInput(incomeInput, 'income');
  wireCashflowInput(spendInput, 'spend');

  horizonFilter.addEventListener('click', function(e) {
    var btn = e.target.closest('.chart-range-btn');
    if (!btn) return;
    horizonFilter.querySelectorAll('.chart-range-btn').forEach(function(b) {
      b.classList.remove('active');
    });
    btn.classList.add('active');
    render();
  });

  render();
})();
