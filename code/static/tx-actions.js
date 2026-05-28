// Shared transaction-row actions: kebab menu (split/dismiss/set-rule/reset/restore),
// applyOverride, and the Set Rule modal. Loaded on Spending and Income pages.

window.applyOverride = function(txId, payload, failMsg) {
  return csrfFetch('/transactions/' + encodeURIComponent(txId) + '/override', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
    .then(function(r) {
      if (r.ok) return window.NWAnimate.refreshMain();
      throw new Error(failMsg || 'Save failed');
    })
    .catch(function(err) {
      alert((failMsg || 'Save failed') + ': ' + err.message);
    });
};

(function() {
  function closeAllMenus(except) {
    document.querySelectorAll('.tx-menu').forEach(function(m) {
      if (m !== except) m.remove();
    });
  }

  function buildMenu(isDismissed) {
    var menu = document.createElement('div');
    menu.className = 'tx-menu';
    if (isDismissed) {
      menu.innerHTML =
        '<button class="tx-menu-item" data-action="restore">Restore</button>';
    } else {
      menu.innerHTML =
        '<button class="tx-menu-item" data-action="split">Split</button>' +
        '<button class="tx-menu-item" data-action="dismiss">Dismiss</button>' +
        '<button class="tx-menu-item" data-action="set-rule">Set rule</button>' +
        '<button class="tx-menu-item" data-action="reset">Reset to original</button>';
    }
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
        var menu = buildMenu(kebab.dataset.dismissed === '1');
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
      window.applyOverride(txId, { dismiss: true }, 'Failed to dismiss');
    } else if (action === 'set-rule') {
      menu.remove();
      window.openRuleModal({ kebab: kebab });
    } else if (action === 'reset') {
      window.applyOverride(txId, { clear: true }, 'Failed to reset');
    } else if (action === 'restore') {
      window.applyOverride(txId, { dismiss: false }, 'Failed to restore');
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

    window.applyOverride(txId, payload, 'Failed to save');
  });
})();

window.openRuleModal = function(opts) {
  opts = opts || {};
  var kebab = opts.kebab || null;
  var editingRule = opts.rule || null;

  var row = kebab ? kebab.closest('tr.tx-row') : null;
  var merchant = row ? (row.dataset.description || '') : '';
  var categoryRaw = row ? (row.dataset.categoryRaw || '') : '';
  var detailedRaw = row ? (row.dataset.item || '') : '';
  var options = window.RULE_MATCH_OPTIONS || { merchant: [], category: [], item: [] };

  function defaultForScope(scope) {
    var list = options[scope] || [];
    var picks = {
      merchant: function(o) { return o.value === merchant; },
      category: function(o) { return o.value === categoryRaw; },
      item: function(o) { return o.value === detailedRaw; },
    };
    var found = list.find(picks[scope]) || list[0];
    if (!found) return null;
    return { value: found.value, label: found.label, _field: found.field };
  }

  function scopeForField(field) {
    if (field === 'pfc_primary') return 'category';
    if (field === 'pfc_detailed') return 'item';
    return 'merchant';
  }

  function stateFromRule(r) {
    var scope = scopeForField(r.match_field);
    var list = options[scope] || [];
    var found = list.find(function(o) { return o.value === r.match_value; });
    var label = found ? found.label : r.match_value;
    var action = r.action;
    var splitMode = 'pct';
    var splitPct = '50';
    var splitAmt = '';
    var setCat = (window.PFC_PRIMARIES && window.PFC_PRIMARIES[0])
      ? window.PFC_PRIMARIES[0].code : '';
    if (action === 'split') {
      splitPct = String(r.action_value || '50');
    } else if (action === 'split_dollar') {
      splitMode = 'dollar';
      splitAmt = String(r.action_value || '');
      action = 'split';
    } else if (action === 'set_category') {
      setCat = r.action_value || setCat;
    }
    return {
      scope: scope,
      op: r.match_op || 'equals',
      value: { value: r.match_value, label: label, _field: r.match_field },
      action: action,
      splitPct: splitPct,
      splitAmt: splitAmt,
      splitMode: splitMode,
      setCategory: setCat,
      rule_id: r.id,
    };
  }

  var state = editingRule ? stateFromRule(editingRule) : {
    scope: 'merchant',
    op: 'equals',
    value: defaultForScope('merchant'),
    action: 'dismiss',
    splitPct: '50',
    splitAmt: '',
    splitMode: 'pct',
    setCategory: (window.PFC_PRIMARIES && window.PFC_PRIMARIES[0])
      ? window.PFC_PRIMARIES[0].code : '',
  };

  var SCOPE_OPTS = [
    { value: 'merchant', label: 'Merchant' },
    { value: 'category', label: 'Category' },
    { value: 'item', label: 'Item' },
  ];
  var OP_OPTS = [
    { value: 'equals', label: 'equals' },
    { value: 'not_equals', label: 'does not equal' },
  ];
  var ACTION_OPTS = [
    { value: 'dismiss', label: 'Dismiss' },
    { value: 'split', label: 'Split' },
    { value: 'set_category', label: 'Recategorize' },
  ];

  function valueOpts() {
    return (options[state.scope] || []).map(function(o) {
      return { value: o.value, label: o.label, _field: o.field };
    });
  }

  var modal = document.createElement('div');
  modal.className = 'rule-modal-backdrop';
  modal.innerHTML =
    '<div class="rule-modal" role="dialog" aria-modal="true" aria-labelledby="rule-modal-title">' +
      '<h2 id="rule-modal-title">' + (editingRule ? 'Edit rule' : 'Set rule') + '</h2>' +
      '<div class="rule-modal-section">' +
        '<div class="rule-modal-section-label">If</div>' +
        '<div class="rule-modal-row">' +
          '<div class="inline-dropdown rule-modal-dd" data-key="scope"><button type="button" class="inline-dropdown-trigger" data-key="scope" aria-haspopup="listbox" aria-expanded="false"><span class="inline-dropdown-label"></span><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button></div>' +
          '<div class="inline-dropdown rule-modal-dd" data-key="op"><button type="button" class="inline-dropdown-trigger" data-key="op" aria-haspopup="listbox" aria-expanded="false"><span class="inline-dropdown-label"></span><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button></div>' +
          '<div class="inline-dropdown rule-modal-dd rule-modal-dd-value"><button type="button" class="inline-dropdown-trigger" data-key="value" aria-haspopup="listbox" aria-expanded="false"><span class="inline-dropdown-label"></span><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button></div>' +
        '</div>' +
      '</div>' +
      '<div class="rule-modal-section">' +
        '<div class="rule-modal-section-label">Then</div>' +
        '<div class="rule-modal-row">' +
          '<div class="inline-dropdown rule-modal-dd" data-key="action"><button type="button" class="inline-dropdown-trigger" data-key="action" aria-haspopup="listbox" aria-expanded="false"><span class="inline-dropdown-label"></span><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button></div>' +
          '<span class="rule-modal-extra" data-extra="split" hidden>' +
            '<span>so my share is</span>' +
            '<input type="number" class="rule-modal-pct" min="1" max="100" step="1" value="50">' +
            '<span>%</span>' +
            '<span>or</span>' +
            '<span class="rule-modal-amt-prefix">$</span>' +
            '<input type="number" class="rule-modal-amt" min="0.01" step="0.01" placeholder="0">' +
          '</span>' +
          '<span class="rule-modal-extra" data-extra="recat" hidden>' +
            '<span>to</span>' +
            '<span class="inline-dropdown rule-modal-dd rule-modal-dd-cat" data-key="setcat"><button type="button" class="inline-dropdown-trigger" data-key="setcat" aria-haspopup="listbox" aria-expanded="false"><span class="inline-dropdown-label"></span><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button></span>' +
          '</span>' +
        '</div>' +
      '</div>' +
      '<div class="rule-modal-actions">' +
        '<button type="button" class="rule-modal-cancel">Cancel</button>' +
        '<button type="button" class="rule-modal-save primary">Save rule</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(modal);

  var pctInput = modal.querySelector('.rule-modal-pct');
  var amtInput = modal.querySelector('.rule-modal-amt');
  // Seed inputs from state (matters when editing an existing rule).
  if (state.splitMode === 'dollar') {
    pctInput.value = '';
    amtInput.value = state.splitAmt || '';
  } else {
    pctInput.value = state.splitPct || '';
    amtInput.value = '';
  }
  pctInput.addEventListener('input', function() {
    if (pctInput.value !== '') {
      amtInput.value = '';
      state.splitAmt = '';
      state.splitMode = 'pct';
    }
    state.splitPct = pctInput.value;
  });
  amtInput.addEventListener('input', function() {
    if (amtInput.value !== '') {
      pctInput.value = '';
      state.splitPct = '';
      state.splitMode = 'dollar';
    }
    state.splitAmt = amtInput.value;
  });

  function findLabel(list, value) {
    var f = list.find(function(o) { return o.value === value; });
    return f ? f.label : '';
  }

  function setLabel(key, text) {
    var btn = modal.querySelector('button[data-key="' + key + '"] .inline-dropdown-label');
    if (btn) btn.textContent = text || '—';
  }

  function refreshLabels() {
    setLabel('scope', findLabel(SCOPE_OPTS, state.scope));
    setLabel('op', findLabel(OP_OPTS, state.op));
    setLabel('value', state.value ? state.value.label : '—');
    setLabel('action', findLabel(ACTION_OPTS, state.action));
    var pName = (window.PFC_PRIMARIES || []).find(function(p) {
      return p.code === state.setCategory;
    });
    setLabel('setcat', pName ? pName.label : '—');
    modal.querySelector('[data-extra="split"]').hidden = state.action !== 'split';
    modal.querySelector('[data-extra="recat"]').hidden = state.action !== 'set_category';
  }

  refreshLabels();

  function bindTrigger(key, opts, onPick) {
    var trigger = modal.querySelector('button.inline-dropdown-trigger[data-key="' + key + '"]');
    trigger.addEventListener('click', function(e) {
      e.stopPropagation();
      if (trigger.getAttribute('aria-expanded') === 'true') {
        window.closeInlineDropdowns();
        return;
      }
      var current = (function() {
        if (key === 'scope') return state.scope;
        if (key === 'op') return state.op;
        if (key === 'value') return state.value ? state.value.value : '';
        if (key === 'action') return state.action;
        if (key === 'setcat') return state.setCategory;
        return null;
      })();
      window.openInlineDropdown(trigger, opts(), current, function(opt) {
        onPick(opt);
        refreshLabels();
      });
    });
  }

  bindTrigger('scope', function() { return SCOPE_OPTS; }, function(opt) {
    state.scope = opt.value;
    state.value = defaultForScope(opt.value);
  });
  bindTrigger('op', function() { return OP_OPTS; }, function(opt) { state.op = opt.value; });
  bindTrigger('value', valueOpts, function(opt) {
    state.value = { value: opt.value, label: opt.label, _field: opt._field };
  });
  bindTrigger('action', function() { return ACTION_OPTS; }, function(opt) {
    state.action = opt.value;
  });
  bindTrigger('setcat', function() {
    return (window.PFC_PRIMARIES || []).map(function(p) {
      return { value: p.code, label: p.label };
    });
  }, function(opt) { state.setCategory = opt.value; });

  function escListener(e) {
    if (e.key === 'Escape') close();
  }
  function close() {
    document.removeEventListener('keydown', escListener);
    modal.remove();
    window.closeInlineDropdowns();
  }
  modal.addEventListener('click', function(e) {
    if (e.target === modal) close();
  });
  modal.querySelector('.rule-modal-cancel').addEventListener('click', close);
  document.addEventListener('keydown', escListener);

  function buildPayload() {
    var payload = {
      match_field: state.value._field,
      match_op: state.op,
      match_value: state.value.value,
      action: state.action,
    };
    if (state.rule_id) payload.rule_id = state.rule_id;
    if (state.action === 'split') {
      if (state.splitMode === 'dollar' && state.splitAmt !== '') {
        payload.action = 'split_dollar';
        payload.action_value = state.splitAmt;
      } else if (state.splitPct !== '') {
        payload.action_value = state.splitPct;
      } else {
        alert('Enter a percentage or a dollar amount.');
        return null;
      }
    }
    if (state.action === 'set_category') payload.action_value = state.setCategory;
    return payload;
  }

  function submit(payload) {
    var saveBtn = modal.querySelector('.rule-modal-save');
    saveBtn.disabled = true;
    csrfFetch('/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(res) {
        if (!res.ok) {
          alert('Failed to save rule: ' + (res.data.error || 'unknown error'));
          saveBtn.disabled = false;
          return;
        }
        close();
        window.NWAnimate.refreshMain();
      });
  }

  modal.querySelector('.rule-modal-save').addEventListener('click', function() {
    if (!state.value) {
      alert('Pick a value to match against.');
      return;
    }
    var payload = buildPayload();
    if (!payload) return;
    // not_equals + dismiss is a footgun (dismisses everything but the chosen
    // match). Preview affected count and confirm before applying.
    var risky = payload.match_op === 'not_equals' && payload.action === 'dismiss';
    if (risky) {
      csrfFetch('/rules/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
        .then(function(res) {
          if (!res.ok) {
            alert('Failed to preview: ' + (res.data.error || 'unknown error'));
            return;
          }
          var n = res.data.matches || 0;
          var msg = 'This rule will dismiss ' + n + ' transaction'
            + (n === 1 ? '' : 's') + '. Continue?';
          window.confirmDialog(msg, function() { submit(payload); },
            { title: 'Dismiss many transactions?', confirmLabel: 'Dismiss', danger: true });
        });
      return;
    }
    submit(payload);
  });
};
