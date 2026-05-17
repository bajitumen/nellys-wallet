// Reads server-injected window.PFC_TAXONOMY and PFC_PRIMARIES; see spending.html.

function spendingPostOverride(txId, payload) {
  return csrfFetch('/transactions/' + encodeURIComponent(txId) + '/override', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

(function() {
  var TAXONOMY = window.PFC_TAXONOMY || {};
  var PRIMARIES = window.PFC_PRIMARIES || [];

  document.addEventListener('click', function(e) {
    var catTrigger = e.target.closest('.cat-trigger');
    if (catTrigger) {
      e.stopPropagation();
      if (catTrigger.getAttribute('aria-expanded') === 'true') {
        window.closeInlineDropdowns();
        return;
      }
      window.openInlineDropdown(catTrigger, PRIMARIES, catTrigger.dataset.value, function(opt) {
        spendingPostOverride(catTrigger.dataset.txId, {
          category: opt.value, detailed: null,
        }).then(function(r) {
          if (r.ok) window.location.reload();
          else alert('Failed to save category');
        });
      });
      return;
    }

    var itemTrigger = e.target.closest('.item-trigger');
    if (itemTrigger) {
      e.stopPropagation();
      if (itemTrigger.getAttribute('aria-expanded') === 'true') {
        window.closeInlineDropdowns();
        return;
      }
      var primary = itemTrigger.dataset.primary;
      var itemOptions = [{value: '', label: '—'}].concat(TAXONOMY[primary] || []);
      window.openInlineDropdown(itemTrigger, itemOptions, itemTrigger.dataset.value, function(opt) {
        itemTrigger.querySelector('.inline-dropdown-label').textContent = opt.label;
        itemTrigger.dataset.value = opt.value;
        itemTrigger.closest('td').dataset.value = opt.value ? opt.label : '';
        spendingPostOverride(itemTrigger.dataset.txId, {
          detailed: opt.value || null,
        }).then(function(r) {
          if (!r.ok) alert('Failed to save item');
        });
      });
    }
  });
})();

(function() {
  function closeAllMenus(except) {
    document.querySelectorAll('.tx-menu').forEach(function(m) {
      if (m !== except) m.remove();
    });
  }

  function buildMenu() {
    var menu = document.createElement('div');
    menu.className = 'tx-menu';
    menu.innerHTML =
      '<button class="tx-menu-item" data-action="split">Split</button>' +
      '<button class="tx-menu-item" data-action="dismiss">Dismiss</button>' +
      '<button class="tx-menu-item" data-action="reset">Reset to original</button>';
    return menu;
  }

  function buildSplitForm(amount) {
    return (
      '<form class="tx-form" data-form-type="split">' +
        '<label>Your share of $' + amount.toFixed(2) + '</label>' +
        '<div class="tx-form-row">' +
          '<label style="margin:0">$ you owe</label>' +
          '<input type="number" name="amount" min="0" step="0.01" placeholder="25.00" autofocus>' +
        '</div>' +
        '<div class="tx-form-actions">' +
          '<button type="button" class="cancel">Cancel</button>' +
          '<button type="submit" class="primary">Save</button>' +
        '</div>' +
      '</form>'
    );
  }

  function positionMenu(menu) {
    menu.classList.remove('tx-menu-up');
    var rect = menu.getBoundingClientRect();
    if (rect.bottom > window.innerHeight - 8) menu.classList.add('tx-menu-up');
  }

  document.addEventListener('click', function(e) {
    var kebab = e.target.closest('.kebab');
    if (kebab) {
      e.stopPropagation();
      var existing = kebab.parentElement.querySelector('.tx-menu');
      closeAllMenus(existing);
      if (existing) {
        existing.remove();
      } else {
        var menu = buildMenu();
        kebab.parentElement.appendChild(menu);
        positionMenu(menu);
      }
      return;
    }
    if (!e.target.closest('.tx-menu')) closeAllMenus();
  });

  document.addEventListener('click', function(e) {
    var item = e.target.closest('.tx-menu-item');
    if (!item) return;
    e.stopPropagation();
    var menu = item.closest('.tx-menu');
    var kebab = menu.parentElement.querySelector('.kebab');
    var txId = kebab.dataset.txId;
    var amount = parseFloat(kebab.dataset.amount);
    var action = item.dataset.action;

    if (action === 'split') {
      menu.innerHTML = buildSplitForm(amount);
      positionMenu(menu);
    } else if (action === 'dismiss') {
      spendingPostOverride(txId, { dismiss: true }).then(function(r) {
        if (r.ok) window.location.reload();
        else alert('Failed to dismiss');
      });
    } else if (action === 'reset') {
      spendingPostOverride(txId, { clear: true }).then(function(r) {
        if (r.ok) window.location.reload();
        else alert('Failed to reset');
      });
    }
  });

  document.addEventListener('click', function(e) {
    if (e.target.matches('.tx-form .cancel')) {
      e.stopPropagation();
      var menu = e.target.closest('.tx-menu');
      if (menu) menu.remove();
    }
  });

  document.addEventListener('submit', function(e) {
    var form = e.target.closest('.tx-form');
    if (!form) return;
    e.preventDefault();
    var menu = form.closest('.tx-menu');
    var kebab = menu.parentElement.querySelector('.kebab');
    var txId = kebab.dataset.txId;
    var origAmount = parseFloat(kebab.dataset.amount);
    var payload = {};

    if (form.dataset.formType === 'split') {
      var amt = parseFloat(form.querySelector('input[name="amount"]').value);
      if (isNaN(amt) || amt <= 0) {
        alert('Enter a dollar amount.');
        return;
      }
      if (amt > origAmount) {
        alert('Your share cannot exceed the original amount of $' + origAmount.toFixed(2) + '.');
        return;
      }
      payload.amount = amt;
      payload.split_percentage = null;
    }

    spendingPostOverride(txId, payload).then(function(r) {
      if (r.ok) window.location.reload();
      else alert('Failed to save');
    });
  });
})();

(function() {
  var filtersContainer = document.querySelector('.tx-filters');
  if (!filtersContainer) return;

  var activeFilters = new Set();
  filtersContainer.querySelectorAll('.filter-chip[data-code]').forEach(function(chip) {
    activeFilters.add(chip.dataset.code);
  });

  function applyRowVisibility() {
    document.querySelectorAll('tr.tx-row').forEach(function(row) {
      var show = activeFilters.size === 0 || activeFilters.has(row.dataset.categoryRaw);
      row.style.display = show ? '' : 'none';
    });
  }

  function updateUrl() {
    var url = new URL(window.location.href);
    url.searchParams.delete('category');
    activeFilters.forEach(function(c) { url.searchParams.append('category', c); });
    history.replaceState({}, '', url.pathname + (url.search || '') + '#transactions');
  }

  function renderChip(code) {
    var menuLink = filtersContainer.querySelector('#filter-menu a[data-code="' + code + '"]');
    var label = menuLink ? menuLink.textContent.trim() : code;
    var chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'filter-chip';
    chip.dataset.code = code;
    chip.innerHTML = label + ' <span class="filter-chip-x">×</span>';
    return chip;
  }

  function syncFilterUi() {
    filtersContainer.querySelectorAll('.filter-chip').forEach(function(el) { el.remove(); });
    var plusBtn = document.getElementById('filter-add');
    activeFilters.forEach(function(code) {
      filtersContainer.insertBefore(renderChip(code), plusBtn);
    });
    var menu = document.getElementById('filter-menu');
    var anyAvailable = false;
    menu.querySelectorAll('a[data-code]').forEach(function(link) {
      var inUse = activeFilters.has(link.dataset.code);
      link.hidden = inUse;
      if (!inUse) anyAvailable = true;
    });
    plusBtn.style.display = anyAvailable ? '' : 'none';
    menu.hidden = true;
    plusBtn.setAttribute('aria-expanded', 'false');

    document.querySelectorAll('tr.category-row').forEach(function(row) {
      row.classList.toggle('active', activeFilters.has(row.dataset.categoryCode));
    });
    document.querySelectorAll('a.stacked-bar-segment').forEach(function(seg) {
      seg.classList.toggle('active', activeFilters.has(seg.dataset.categoryCode));
    });
  }

  function commit() {
    applyRowVisibility();
    updateUrl();
    syncFilterUi();
  }

  function scrollToTransactions() {
    var target = document.getElementById('transactions');
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function addFilter(code) { activeFilters.add(code); commit(); }
  function removeFilter(code) { activeFilters.delete(code); commit(); }
  function replaceFilter(code) {
    activeFilters.clear();
    activeFilters.add(code);
    commit();
    scrollToTransactions();
  }

  filtersContainer.addEventListener('click', function(e) {
    var chip = e.target.closest('.filter-chip[data-code]');
    if (chip) { e.preventDefault(); removeFilter(chip.dataset.code); return; }
    var menuLink = e.target.closest('#filter-menu a[data-code]');
    if (menuLink) { e.preventDefault(); addFilter(menuLink.dataset.code); return; }
    var plusBtn = e.target.closest('#filter-add');
    if (plusBtn) {
      e.stopPropagation();
      var menu = document.getElementById('filter-menu');
      var open = !menu.hidden;
      menu.hidden = open;
      plusBtn.setAttribute('aria-expanded', String(!open));
    }
  });

  document.addEventListener('click', function(e) {
    var row = e.target.closest('tr.category-row');
    if (row && row.dataset.categoryCode) {
      if (e.target.closest('a, button, .inline-dropdown, .kebab, input')) return;
      e.preventDefault();
      replaceFilter(row.dataset.categoryCode);
      return;
    }
    var seg = e.target.closest('a.stacked-bar-segment');
    if (seg && seg.dataset.categoryCode) {
      e.preventDefault();
      replaceFilter(seg.dataset.categoryCode);
      return;
    }
    var menu = document.getElementById('filter-menu');
    var plus = document.getElementById('filter-add');
    if (menu && !menu.hidden && !e.target.closest('#filter-menu') && !e.target.closest('#filter-add')) {
      menu.hidden = true;
      if (plus) plus.setAttribute('aria-expanded', 'false');
    }
  });
})();
