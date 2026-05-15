// Shared chrome: theme toggle, refresh button, add-account flow, plus the
// csrfFetch wrapper every state-changing fetch on the page uses.

window.csrfFetch = function(url, options) {
  options = options || {};
  options.headers = Object.assign({}, options.headers, {
    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
  });
  return fetch(url, options);
};

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
