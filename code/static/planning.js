// Planning page: forward-project either total net worth or a single
// account's balance using the per-account annual rates set in the table
// below. Math runs client-side so the dropdown + horizon buttons change
// the chart without a server roundtrip; rate edits debounce-save to the
// backend on blur.

(function() {
  var svg = document.getElementById('planning-svg');
  var targetEl = document.getElementById('planning-target');
  var summaryEl = document.getElementById('planning-summary');
  var inputs = document.querySelectorAll('.planning-rate-input');
  var horizonFilter = document.querySelector('.planning-horizon-filter');
  if (!svg || !targetEl || !inputs.length) return;

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var WIDTH = 1000, HEIGHT = 220;
  var PAD_X = 8, PAD_Y = 14;

  function activeYears() {
    var btn = horizonFilter.querySelector('.chart-range-btn.active');
    return btn ? parseInt(btn.dataset.years, 10) : 10;
  }

  // Snapshot the current state of every rate input into a uniform shape
  // the projection math can consume without poking the DOM in a loop.
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

  function projectSeries(startValue, annualRatePct, months) {
    // Compound monthly so the chart looks smooth even at 30 years and a
    // big rate; (1 + r)^(t/12) gives same end value as (1 + r/12)^t at
    // small r and is more honest about "annual rate" semantics.
    var out = [startValue];
    var monthly = Math.pow(1 + annualRatePct / 100, 1 / 12) - 1;
    var v = startValue;
    for (var m = 1; m <= months; m++) {
      v = v * (1 + monthly);
      out.push(v);
    }
    return out;
  }

  function buildSeriesFor(targetId, accounts, months) {
    if (targetId === '__net__') {
      // Per month: sum of each asset's projected balance minus each debt's
      // projected balance. Keep them independent so each grows at its own
      // rate.
      var series = new Array(months + 1).fill(0);
      accounts.forEach(function(a) {
        var p = projectSeries(a.balance, a.rateAnnual, months);
        for (var i = 0; i <= months; i++) {
          series[i] += a.sign * p[i];
        }
      });
      return series;
    }
    var acct = accounts.find(function(a) { return a.id === targetId; });
    if (!acct) return [];
    return projectSeries(acct.balance, acct.rateAnnual, months);
  }

  function pathFromSeries(series) {
    if (series.length < 2) return { line: '', area: '' };
    var n = series.length;
    var plotW = WIDTH - 2 * PAD_X;
    var plotH = HEIGHT - 2 * PAD_Y;
    var yMin = Math.min.apply(null, series);
    var yMax = Math.max.apply(null, series);
    // Pad the Y span so the line isn't flush against the chart edges.
    var pad = Math.max(1, (yMax - yMin) * 0.05);
    yMin -= pad;
    yMax += pad;
    var ySpan = Math.max(1, yMax - yMin);

    var points = series.map(function(v, i) {
      var x = PAD_X + (i / (n - 1)) * plotW;
      var y = PAD_Y + (yMax - v) / ySpan * plotH;
      return { x: x, y: y };
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

  function render() {
    var years = activeYears();
    var months = years * 12;
    var accounts = snapshotAccounts();
    var series = buildSeriesFor(targetEl.value, accounts, months);
    if (!series.length) {
      svg.innerHTML = '';
      summaryEl.textContent = '';
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

    var label = targetEl.options[targetEl.selectedIndex].text;
    summaryEl.textContent = label + ' · today ' + formatUsd(series[0])
      + ' → ' + years + 'y ' + formatUsd(series[series.length - 1]);
  }

  // Auto-save a rate to the server on blur or Enter.
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
      }).catch(function() { /* swallow; UI already shows new value */ });
    }
    input.addEventListener('input', render);  // live chart updates as you type
    input.addEventListener('blur', save);
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    });
  });

  targetEl.addEventListener('change', render);
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
