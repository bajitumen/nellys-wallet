(function() {
  var TICKER_MS = 350;
  function easeOut(t) { return 1 - Math.pow(1 - t, 3); }

  function parseNumeric(text) {
    if (text == null) return null;
    var v = parseFloat(String(text).replace(/[$,%+\s]/g, ''));
    return isNaN(v) ? null : v;
  }

  function inferFormat(s) {
    s = String(s).trim();
    var prefix = '';
    var suffix = '';
    if (s.charAt(0) === '$') prefix = '$';
    if (s.charAt(s.length - 1) === '%') suffix = '%';
    var m = s.match(/\.(\d+)/);
    return { prefix: prefix, suffix: suffix, decimals: m ? m[1].length : 0 };
  }

  function format(fmt, v) {
    return fmt.prefix + v.toLocaleString('en-US', {
      minimumFractionDigits: fmt.decimals,
      maximumFractionDigits: fmt.decimals,
    }) + fmt.suffix;
  }

  function tick(el, from, to) {
    if (el._tickerRaf) cancelAnimationFrame(el._tickerRaf);
    var fmt = inferFormat(el.textContent || ('' + to));
    var start = performance.now();
    function step(now) {
      var t = Math.min(1, (now - start) / TICKER_MS);
      var v = from + (to - from) * easeOut(t);
      el.textContent = format(fmt, v);
      if (t < 1) {
        el._tickerRaf = requestAnimationFrame(step);
      } else {
        el._tickerRaf = null;
      }
    }
    el._tickerRaf = requestAnimationFrame(step);
  }

  function snapshotValues(root) {
    var snap = {};
    (root || document).querySelectorAll('.anim-value[data-anim-key]').forEach(function(el) {
      snap[el.dataset.animKey] = parseNumeric(el.textContent);
    });
    return snap;
  }

  function replayValues(snap, root) {
    (root || document).querySelectorAll('.anim-value[data-anim-key]').forEach(function(el) {
      var key = el.dataset.animKey;
      var from = snap[key];
      var to = parseNumeric(el.textContent);
      if (from == null || to == null || from === to) return;
      tick(el, from, to);
    });
  }

  function snapshotSegments(root) {
    var snap = {};
    (root || document).querySelectorAll('.stacked-bar-segment[data-filter-value]').forEach(function(seg) {
      snap[seg.dataset.filterValue] = parseFloat(seg.style.flexGrow) || 0;
    });
    return snap;
  }

  function replaySegments(snap, root) {
    (root || document).querySelectorAll('.stacked-bar-segment[data-filter-value]').forEach(function(seg) {
      var key = seg.dataset.filterValue;
      var target = parseFloat(seg.style.flexGrow) || 0;
      var from = snap[key];
      if (from == null || from === target) return;
      seg.style.transition = 'none';
      seg.style.flexGrow = from;
      // Two RAFs so the browser commits the "from" value before transitioning.
      requestAnimationFrame(function() {
        requestAnimationFrame(function() {
          seg.style.transition = 'flex-grow ' + TICKER_MS + 'ms ease-out';
          seg.style.flexGrow = target;
        });
      });
    });
  }

  function refreshMain(href) {
    return fetch(href || window.location.href, { credentials: 'same-origin' })
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function(html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var fresh = doc.querySelector('main');
        var current = document.querySelector('main');
        if (!fresh || !current) return;
        var valueSnap = snapshotValues(current);
        var segmentSnap = snapshotSegments(current);
        current.innerHTML = fresh.innerHTML;
        replayValues(valueSnap, current);
        replaySegments(segmentSnap, current);
        if (window._txFilters && window._txFilters.refresh) {
          window._txFilters.refresh();
        }
      });
  }

  function wireMonthMenu() {
    document.addEventListener('click', function(e) {
      var menu = document.getElementById('month-menu');
      if (!menu) return;
      var link = e.target.closest('#month-menu a');
      if (!link) return;
      e.preventDefault();
      e.stopPropagation();
      var href = link.getAttribute('href');
      history.pushState({}, '', href);
      menu.querySelectorAll('a').forEach(function(a) { a.classList.remove('active'); });
      link.classList.add('active');
      var trigger = document.getElementById('month-trigger');
      var labelEl = trigger && trigger.querySelector('.month-label');
      if (labelEl) labelEl.textContent = link.textContent.trim();
      menu.hidden = true;
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
      refreshMain(href);
    });
  }

  window.NWAnimate = {
    tick: tick,
    parseNumeric: parseNumeric,
    snapshotValues: snapshotValues,
    replayValues: replayValues,
    snapshotSegments: snapshotSegments,
    replaySegments: replaySegments,
    refreshMain: refreshMain,
    wireMonthMenu: wireMonthMenu,
  };
})();
