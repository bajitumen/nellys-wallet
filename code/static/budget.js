// Auto-save each sub-category budget on blur (or Enter). The server returns
// the new primary-group total so the header refreshes without a page reload.

(function() {
  function formatUsd(n) {
    return '$' + n.toLocaleString('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
  }

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
