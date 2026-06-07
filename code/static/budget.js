(function() {
  function formatUsd(n) { return fmtUsd(n, 2); }
  function animateTicker(el, from, to) { NWAnimate.tick(el, from, to, formatUsd); }

  function readNumeric(el) {
    if (!el) return 0;
    var v = parseFloat(el.textContent.replace(/[$,]/g, ''));
    return isNaN(v) ? 0 : v;
  }

  function updateActualCells(spentByDetailed) {
    var primarySpent = {};
    document.querySelectorAll('.budget-sub-actual[data-code]').forEach(function(cell) {
      var code = cell.dataset.code;
      var amount = spentByDetailed[code] || 0;
      cell.textContent = amount > 0 ? formatUsd(amount) : '';
      cell.classList.remove('spend-good', 'spend-bad');
      var input = document.querySelector('.budget-input[data-code="' + code + '"]');
      if (input) primarySpent[input.dataset.primary] = (primarySpent[input.dataset.primary] || 0) + amount;
      if (amount > 0) {
        var budget = input ? (parseFloat(input.value) || 0) : 0;
        cell.classList.add(budget > 0 && amount <= budget ? 'spend-good' : 'spend-bad');
      }
    });
    Object.keys(primarySpent).forEach(function(primary) {
      var el = document.querySelector('.budget-group-spent[data-primary="' + primary + '"]');
      if (el) el.textContent = formatUsd(primarySpent[primary]);
    });
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
          updateActualCells(data.spent_by_detailed || {});
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
      csrfFetchJson('/budget/' + encodeURIComponent(input.dataset.code), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
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
          var actualCell = document.querySelector('.budget-sub-actual[data-code="' + input.dataset.code + '"]');
          if (actualCell) {
            var amount = readNumeric(actualCell);
            var newBudget = parseFloat(input.value) || 0;
            actualCell.classList.remove('spend-good', 'spend-bad');
            if (amount > 0) {
              actualCell.classList.add(newBudget > 0 && amount <= newBudget ? 'spend-good' : 'spend-bad');
            }
          }
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
