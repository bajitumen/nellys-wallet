(function() {
  function formatUsd(n) {
    return '$' + n.toLocaleString('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
  }

  // 350ms matches the segment animation so ticker + bar arrive together.
  var TICKER_MS = 350;
  function easeOut(t) {
    return 1 - Math.pow(1 - t, 3);
  }
  function animateTicker(el, from, to) {
    if (el._tickerRaf) cancelAnimationFrame(el._tickerRaf);
    var start = performance.now();
    function step(now) {
      var t = Math.min(1, (now - start) / TICKER_MS);
      var v = from + (to - from) * easeOut(t);
      el.textContent = formatUsd(v);
      if (t < 1) {
        el._tickerRaf = requestAnimationFrame(step);
      } else {
        el._tickerRaf = null;
      }
    }
    el._tickerRaf = requestAnimationFrame(step);
  }

  function readNumeric(el) {
    if (!el) return 0;
    var v = parseFloat(el.textContent.replace(/[$,]/g, ''));
    return isNaN(v) ? 0 : v;
  }

  function refreshSummary(primary, newPrimarySum) {
    var totalEl = document.getElementById('card-total-budget');
    var diffEl = document.getElementById('card-difference');
    var summaryCard = document.querySelector('.budget-summary-card');
    if (!totalEl) return;
    var primaryTotals = {};
    document.querySelectorAll('.budget-group-total').forEach(function(el) {
      primaryTotals[el.dataset.primary] = readNumeric(el);
    });
    primaryTotals[primary] = newPrimarySum;
    var grand = Object.values(primaryTotals).reduce(function(s, v) { return s + v; }, 0);
    var prevTotal = readNumeric(totalEl);
    animateTicker(totalEl, prevTotal, grand);

    if (diffEl && summaryCard) {
      var spent = parseFloat(summaryCard.dataset.monthSpent) || 0;
      tickerDifference(diffEl, grand - spent);
    }

    document.querySelectorAll('.budget-stacked-bar .stacked-bar-segment').forEach(function(seg) {
      var p = seg.dataset.primary;
      var v = primaryTotals[p] || 0;
      seg.style.flex = v + ' 0 0';
      // data-tooltip is "<label>: <usd>"; preserve the leading label.
      var label = (seg.dataset.tooltip || '').split(':')[0].trim();
      seg.dataset.tooltip = label + ': ' + formatUsd(v);
    });
  }

  function tickerDifference(diffEl, newDiff) {
    if (!diffEl) return;
    var prevDiff = readNumeric(diffEl);
    var diffCard = diffEl.closest('.card');
    if (diffCard) {
      diffCard.classList.remove('credit', 'net');
      if (newDiff < 0) diffCard.classList.add('credit');
      else if (newDiff > 0) diffCard.classList.add('net');
    }
    animateTicker(diffEl, prevDiff, newDiff);
  }

  document.addEventListener('click', function(e) {
    var seg = e.target.closest('.budget-stacked-bar a.stacked-bar-segment');
    if (!seg) return;
    var hash = seg.getAttribute('href') || '';
    if (!hash.startsWith('#')) return;
    var target = document.getElementById(hash.slice(1));
    if (!target) return;
    e.preventDefault();
    history.pushState({}, '', hash);
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  (function() {
    var menu = document.getElementById('month-menu');
    if (!menu) return;
    menu.addEventListener('click', function(e) {
      var link = e.target.closest('a');
      if (!link) return;
      var url = new URL(link.href, window.location.href);
      var newMonth = url.searchParams.get('month');
      if (!newMonth) return;
      e.preventDefault();
      e.stopPropagation();

      history.pushState({}, '', url.pathname + url.search);
      menu.querySelectorAll('a').forEach(function(a) { a.classList.remove('active'); });
      link.classList.add('active');
      var trigger = document.getElementById('month-trigger');
      var labelEl = trigger && trigger.querySelector('.month-label');
      if (labelEl) labelEl.textContent = link.textContent.trim();
      menu.hidden = true;
      if (trigger) trigger.setAttribute('aria-expanded', 'false');

      fetch('/budget/summary?month=' + encodeURIComponent(newMonth))
        .then(function(r) { return r.json(); })
        .then(function(data) {
          var spentEl = document.getElementById('card-total-spent');
          var summaryCard = document.querySelector('.budget-summary-card');
          var totalBudgetEl = document.getElementById('card-total-budget');
          var diffEl = document.getElementById('card-difference');
          if (spentEl) {
            animateTicker(spentEl, readNumeric(spentEl), data.total_spent);
          }
          if (summaryCard) summaryCard.dataset.monthSpent = data.total_spent;
          if (totalBudgetEl && diffEl) {
            tickerDifference(diffEl, readNumeric(totalBudgetEl) - data.total_spent);
          }
        });
    });
  })();

  document.querySelectorAll('.budget-input').forEach(function(input) {
    var lastValue = input.value;

    function save() {
      if (input.value === lastValue) return;
      lastValue = input.value;
      var payload = input.value === ''
        ? { amount: null }
        : { amount: parseFloat(input.value) };
      csrfFetch('/budget/' + encodeURIComponent(input.dataset.code), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
        .then(function(res) {
          if (!res.ok) {
            alert('Failed to save: ' + (res.data.error || 'unknown error'));
            return;
          }
          var totalEl = document.querySelector(
            '.budget-group-total[data-primary="' + input.dataset.primary + '"]'
          );
          if (totalEl) totalEl.textContent = formatUsd(res.data.primary_sum);
          refreshSummary(input.dataset.primary, res.data.primary_sum);
        });
    }

    input.addEventListener('blur', save);
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        input.blur();
      }
    });
  });
})();
