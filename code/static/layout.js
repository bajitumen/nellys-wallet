// Shared chrome: theme toggle, refresh button, add-account flow, plus the
// csrfFetch wrapper every state-changing fetch on the page uses. Also
// hosts a few page-agnostic widgets — number-input formatter, month
// picker, stacked-bar tooltip — that used to be duplicated across the
// per-page JS files.

window.csrfFetch = function(url, options) {
  options = options || {};
  options.headers = Object.assign({}, options.headers, {
    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
  });
  return fetch(url, options);
};

// ---------------------------------------------------------------------------
// Numeric input formatter
//
// Any `<input class="numeric-input">` is normalized to fixed-2 on blur:
//   "25"     → "25.00"
//   "25.5"   → "25.50"
//   "12.345" → "12.35"
//   ""       stays empty (callers treat empty as "cleared")
// Runs in the capture phase so page-specific blur handlers read the
// already-formatted value.
// ---------------------------------------------------------------------------
document.addEventListener('blur', function(e) {
  var el = e.target;
  if (!el.classList || !el.classList.contains('numeric-input')) return;
  if (el.value === '') return;
  var v = parseFloat(el.value);
  if (!isNaN(v)) el.value = v.toFixed(2);
}, true);

// ---------------------------------------------------------------------------
// Month-picker dropdown (Spending / Income / Budget header card)
// ---------------------------------------------------------------------------
(function() {
  var trigger = document.getElementById('month-trigger');
  var menu = document.getElementById('month-menu');
  if (!trigger || !menu) return;
  trigger.addEventListener('click', function(e) {
    e.stopPropagation();
    var open = !menu.hidden;
    menu.hidden = open;
    trigger.setAttribute('aria-expanded', String(!open));
  });
  document.addEventListener('click', function(e) {
    if (!menu.hidden && !e.target.closest('.month-menu')) {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
    }
  });
})();

// ---------------------------------------------------------------------------
// Stacked-bar hover tooltip — auto-attached to every `.stacked-bar` whose
// segments carry `data-tooltip="..."`.
// ---------------------------------------------------------------------------
(function() {
  var bars = document.querySelectorAll('.stacked-bar');
  if (!bars.length) return;
  var tooltip = document.createElement('div');
  tooltip.className = 'bar-tooltip';
  document.body.appendChild(tooltip);
  function show(seg) {
    if (!seg.dataset.tooltip) return;
    tooltip.textContent = seg.dataset.tooltip;
    var rect = seg.getBoundingClientRect();
    tooltip.style.left = (rect.left + rect.width / 2) + 'px';
    tooltip.style.top = rect.top + 'px';
    tooltip.classList.add('visible');
  }
  function hide() { tooltip.classList.remove('visible'); }
  bars.forEach(function(bar) {
    bar.addEventListener('mouseover', function(e) {
      var seg = e.target.closest('.stacked-bar-segment');
      if (seg && seg.offsetWidth > 0) show(seg);
    });
    bar.addEventListener('mouseleave', hide);
  });
  window.addEventListener('scroll', hide, { passive: true });
})();

document.querySelector('.theme-toggle').addEventListener('click', function() {
  var html = document.documentElement;
  var isDark = html.getAttribute('data-theme') === 'dark';
  if (isDark) {
    html.removeAttribute('data-theme');
    localStorage.setItem('theme', 'light');
  } else {
    html.setAttribute('data-theme', 'dark');
    localStorage.setItem('theme', 'dark');
  }
});

// Refresh button → POST /sync, reload.
(function() {
  var btn = document.getElementById('refresh-btn');
  if (!btn) return;
  btn.addEventListener('click', function() {
    if (btn.classList.contains('busy')) return;
    btn.classList.add('busy');
    csrfFetch('/sync', { method: 'POST' })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(res) {
        if (res.ok && res.data.ok) {
          window.location.reload();
        } else {
          alert('Sync failed: ' + (res.data.error || 'unknown error'));
          btn.classList.remove('busy');
        }
      })
      .catch(function(e) {
        alert('Sync error: ' + e.message);
        btn.classList.remove('busy');
      });
  });
})();

// Add-account button → fetch link token, open Plaid Link, exchange, reload.
(function() {
  var btn = document.getElementById('add-account-btn');
  if (!btn) return;
  btn.addEventListener('click', function() {
    btn.disabled = true;
    csrfFetch('/link/token', { method: 'POST' })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(res) {
        if (!res.ok || !res.data.link_token) {
          alert('Could not start Plaid Link: ' + (res.data.error || 'unknown error'));
          btn.disabled = false;
          return;
        }
        var handler = Plaid.create({
          token: res.data.link_token,
          onSuccess: function(public_token) {
            csrfFetch('/link/exchange', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ public_token: public_token }),
            })
              .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
              .then(function(res) {
                if (!res.ok) {
                  alert('Failed to save account: ' + (res.data.error || 'unknown error'));
                  btn.disabled = false;
                  return;
                }
                window.location.reload();
              });
          },
          onExit: function() { btn.disabled = false; },
        });
        handler.open();
      })
      .catch(function(e) {
        alert('Error: ' + e.message);
        btn.disabled = false;
      });
  });
})();
