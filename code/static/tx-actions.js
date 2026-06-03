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
        '<button class="tx-menu-item" data-action="restore">Restore</button>' +
        '<button class="tx-menu-item" data-action="set-rule">Set rule</button>';
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
  var pageScope = (
    (editingRule && editingRule.scope)
    || opts.pageScope || window.RULE_PAGE_SCOPE || 'all'
  );

  var row = kebab ? kebab.closest('tr.tx-row') : null;
  var merchant = row ? (row.dataset.description || '') : '';
  var categoryRaw = row ? (row.dataset.categoryRaw || '') : '';
  var detailedRaw = row ? (row.dataset.item || '') : '';
  var sourceRaw = row ? (row.dataset.source || '') : '';
  var options = window.RULE_MATCH_OPTIONS
    || { merchant: [], category: [], item: [], source: [] };

  var FIELD_OPTS = [
    { value: 'merchant', label: 'Merchant' },
    { value: 'category', label: 'Category' },
    { value: 'item', label: 'Item' },
    { value: 'source', label: 'Source' },
  ];
  var OP_OPTS = [
    { value: 'equals', label: 'equals' },
    { value: 'not_equals', label: 'does not equal' },
  ];
  var LOGIC_OPTS = [
    { value: 'all', label: 'All' },
    { value: 'any', label: 'Any' },
  ];
  var ACTION_OPTS = [
    { value: 'dismiss', label: 'Dismiss' },
    { value: 'split', label: 'Split' },
    { value: 'set_category', label: 'Recategorize' },
  ];

  function fieldKeyFromMatchField(mf) {
    if (mf === 'pfc_primary') return 'category';
    if (mf === 'pfc_detailed') return 'item';
    if (mf === 'source') return 'source';
    return 'merchant';
  }

  function defaultCondition(fieldKey) {
    var list = options[fieldKey] || [];
    var picks = {
      merchant: function(o) { return o.value === merchant; },
      category: function(o) { return o.value === categoryRaw; },
      item: function(o) { return o.value === detailedRaw; },
      source: function(o) { return o.value === sourceRaw; },
    };
    var found = (picks[fieldKey] && list.find(picks[fieldKey])) || list[0] || null;
    return {
      field: fieldKey,
      op: 'equals',
      value: found ? { value: found.value, label: found.label, _field: found.field } : null,
    };
  }

  function conditionsFromRule(r) {
    return (r.conditions || []).map(function(c) {
      var key = fieldKeyFromMatchField(c.match_field);
      var list = options[key] || [];
      var found = list.find(function(o) { return o.value === c.match_value; });
      var label = found ? found.label : c.match_value;
      return {
        field: key,
        op: c.match_op || 'equals',
        value: { value: c.match_value, label: label, _field: c.match_field },
      };
    });
  }

  var state;
  if (editingRule) {
    var existing = conditionsFromRule(editingRule);
    if (!existing.length) existing = [defaultCondition('merchant')];
    var action = editingRule.action;
    var splitMode = 'pct', splitPct = '50', splitAmt = '';
    var setCategory = (window.PFC_PRIMARIES && window.PFC_PRIMARIES[0])
      ? window.PFC_PRIMARIES[0].code : '';
    if (action === 'split') {
      splitPct = String(editingRule.action_value || '50');
    } else if (action === 'split_dollar') {
      splitMode = 'dollar';
      splitAmt = String(editingRule.action_value || '');
      action = 'split';
    } else if (action === 'set_category') {
      setCategory = editingRule.action_value || setCategory;
    }
    state = {
      conditions: existing,
      conditionsLogic: editingRule.conditions_logic || 'all',
      action: action, splitPct: splitPct, splitAmt: splitAmt, splitMode: splitMode,
      setCategory: setCategory, pageScope: pageScope, rule_id: editingRule.id,
    };
  } else {
    state = {
      conditions: [defaultCondition('merchant')],
      conditionsLogic: 'all',
      action: 'dismiss', splitPct: '50', splitAmt: '', splitMode: 'pct',
      setCategory: (window.PFC_PRIMARIES && window.PFC_PRIMARIES[0])
        ? window.PFC_PRIMARIES[0].code : '',
      pageScope: pageScope,
    };
  }

  function valueOptsFor(fieldKey) {
    return (options[fieldKey] || []).map(function(o) {
      return { value: o.value, label: o.label, _field: o.field };
    });
  }

  var CARET_SVG =
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"' +
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<polyline points="6 9 12 15 18 9"/></svg>';

  function ddHtml(key, extraClass) {
    return '<div class="inline-dropdown rule-modal-dd ' + (extraClass || '') +
      '" data-key="' + key + '">' +
      '<button type="button" class="inline-dropdown-trigger" data-key="' + key + '"' +
      ' aria-haspopup="listbox" aria-expanded="false">' +
      '<span class="inline-dropdown-label"></span>' + CARET_SVG +
      '</button></div>';
  }

  var scopeTitle = { spending: ' · Spending', income: ' · Income' }[state.pageScope] || '';
  var modal = document.createElement('div');
  modal.className = 'rule-modal-backdrop';
  modal.innerHTML =
    '<div class="rule-modal" role="dialog" aria-modal="true" aria-labelledby="rule-modal-title">' +
      '<h2 id="rule-modal-title">' + (editingRule ? 'Edit rule' : 'Set rule') +
        '<span class="rule-modal-scope-tag">' + scopeTitle + '</span></h2>' +
      '<fieldset class="rule-modal-conditions">' +
        '<legend class="rule-modal-conditions-legend">' +
          '<span>If</span>' +
          ddHtml('logic', 'rule-modal-dd-logic') +
          '<span>of the following conditions are met</span>' +
        '</legend>' +
        '<div class="rule-modal-conditions-list"></div>' +
      '</fieldset>' +
      '<div class="rule-modal-section">' +
        '<div class="rule-modal-section-label">Then</div>' +
        '<div class="rule-modal-row">' +
          ddHtml('action') +
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
            ddHtml('setcat', 'rule-modal-dd-cat') +
          '</span>' +
        '</div>' +
      '</div>' +
      '<div class="rule-modal-actions">' +
        '<button type="button" class="rule-modal-cancel">Cancel</button>' +
        '<button type="button" class="rule-modal-save primary">Save rule</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(modal);

  var listEl = modal.querySelector('.rule-modal-conditions-list');
  var pctInput = modal.querySelector('.rule-modal-pct');
  var amtInput = modal.querySelector('.rule-modal-amt');

  if (state.splitMode === 'dollar') {
    pctInput.value = '';
    amtInput.value = state.splitAmt || '';
  } else {
    pctInput.value = state.splitPct || '';
    amtInput.value = '';
  }
  pctInput.addEventListener('input', function() {
    if (pctInput.value !== '') {
      amtInput.value = ''; state.splitAmt = ''; state.splitMode = 'pct';
    }
    state.splitPct = pctInput.value;
  });
  amtInput.addEventListener('input', function() {
    if (amtInput.value !== '') {
      pctInput.value = ''; state.splitPct = ''; state.splitMode = 'dollar';
    }
    state.splitAmt = amtInput.value;
  });

  function findLabel(list, value) {
    var f = list.find(function(o) { return o.value === value; });
    return f ? f.label : '';
  }

  function setLabel(trigger, text) {
    var lbl = trigger.querySelector('.inline-dropdown-label');
    if (lbl) lbl.textContent = text || '—';
  }

  function bindTrigger(trigger, optsFn, currentFn, onPick) {
    trigger.addEventListener('click', function(e) {
      e.stopPropagation();
      if (trigger.getAttribute('aria-expanded') === 'true') {
        window.closeInlineDropdowns();
        return;
      }
      window.openInlineDropdown(trigger, optsFn(), currentFn(), function(opt) {
        onPick(opt);
        refresh();
      });
    });
  }

  function renderConditionRow(cond, idx) {
    var row = document.createElement('div');
    row.className = 'rule-modal-condition-row';
    row.dataset.idx = idx;
    row.innerHTML =
      ddHtml('field', 'rule-modal-dd-field') +
      ddHtml('op', 'rule-modal-dd-op') +
      ddHtml('value', 'rule-modal-dd-value') +
      '<button type="button" class="rule-modal-cond-add" aria-label="Add condition">+</button>' +
      '<button type="button" class="rule-modal-cond-remove" aria-label="Remove condition"' +
        (state.conditions.length === 1 ? ' disabled' : '') + '>−</button>';

    var fieldTrigger = row.querySelector('.rule-modal-dd-field .inline-dropdown-trigger');
    var opTrigger = row.querySelector('.rule-modal-dd-op .inline-dropdown-trigger');
    var valueTrigger = row.querySelector('.rule-modal-dd-value .inline-dropdown-trigger');
    setLabel(fieldTrigger, findLabel(FIELD_OPTS, cond.field));
    setLabel(opTrigger, findLabel(OP_OPTS, cond.op));
    setLabel(valueTrigger, cond.value ? cond.value.label : '—');

    bindTrigger(fieldTrigger,
      function() { return FIELD_OPTS; },
      function() { return cond.field; },
      function(opt) {
        cond.field = opt.value;
        var d = defaultCondition(opt.value);
        cond.value = d.value;
      }
    );
    bindTrigger(opTrigger,
      function() { return OP_OPTS; },
      function() { return cond.op; },
      function(opt) { cond.op = opt.value; }
    );
    bindTrigger(valueTrigger,
      function() { return valueOptsFor(cond.field); },
      function() { return cond.value ? cond.value.value : ''; },
      function(opt) {
        cond.value = { value: opt.value, label: opt.label, _field: opt._field };
      }
    );

    row.querySelector('.rule-modal-cond-add').addEventListener('click', function(e) {
      e.stopPropagation();
      state.conditions.splice(idx + 1, 0, defaultCondition('merchant'));
      refresh();
    });
    row.querySelector('.rule-modal-cond-remove').addEventListener('click', function(e) {
      e.stopPropagation();
      if (state.conditions.length === 1) return;
      state.conditions.splice(idx, 1);
      refresh();
    });
    return row;
  }

  function renderConditions() {
    listEl.innerHTML = '';
    state.conditions.forEach(function(c, i) {
      listEl.appendChild(renderConditionRow(c, i));
    });
  }

  var logicTrigger = modal.querySelector('.rule-modal-dd-logic .inline-dropdown-trigger');
  var actionTrigger = modal.querySelector('.rule-modal-dd[data-key="action"] .inline-dropdown-trigger');
  var setcatTrigger = modal.querySelector('.rule-modal-dd-cat .inline-dropdown-trigger');

  bindTrigger(logicTrigger,
    function() { return LOGIC_OPTS; },
    function() { return state.conditionsLogic; },
    function(opt) { state.conditionsLogic = opt.value; }
  );
  bindTrigger(actionTrigger,
    function() { return ACTION_OPTS; },
    function() { return state.action; },
    function(opt) { state.action = opt.value; }
  );
  bindTrigger(setcatTrigger,
    function() {
      return (window.PFC_PRIMARIES || []).map(function(p) {
        return { value: p.code, label: p.label };
      });
    },
    function() { return state.setCategory; },
    function(opt) { state.setCategory = opt.value; }
  );

  function refresh() {
    setLabel(logicTrigger, findLabel(LOGIC_OPTS, state.conditionsLogic));
    setLabel(actionTrigger, findLabel(ACTION_OPTS, state.action));
    var pName = (window.PFC_PRIMARIES || []).find(function(p) {
      return p.code === state.setCategory;
    });
    setLabel(setcatTrigger, pName ? pName.label : '—');
    modal.querySelector('[data-extra="split"]').hidden = state.action !== 'split';
    modal.querySelector('[data-extra="recat"]').hidden = state.action !== 'set_category';
    renderConditions();
  }

  refresh();

  function escListener(e) { if (e.key === 'Escape') close(); }
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
    var unset = state.conditions.find(function(c) { return !c.value; });
    if (unset) { alert('Pick a value for every condition.'); return null; }
    var payload = {
      conditions: state.conditions.map(function(c) {
        return { match_field: c.value._field, match_op: c.op, match_value: c.value.value };
      }),
      conditions_logic: state.conditionsLogic,
      action: state.action,
      scope: state.pageScope || 'all',
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
    var origLabel = saveBtn.textContent;
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';

    var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timeoutMs = 30000;
    var timer = setTimeout(function() {
      if (controller) controller.abort();
    }, timeoutMs);

    function done() {
      clearTimeout(timer);
      saveBtn.disabled = false;
      saveBtn.textContent = origLabel;
    }

    csrfFetch('/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller ? controller.signal : undefined,
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(res) {
        if (!res.ok) {
          done();
          alert('Failed to save rule: ' + (res.data.error || 'unknown error'));
          return;
        }
        clearTimeout(timer);
        if (res.data && res.data.warning) {
          alert('Rule saved, but applying to past transactions failed: ' + res.data.warning);
        }
        // Hard reload: a rule save can affect many rows; refreshMain races with
        // a follow-up save if the user opens another modal mid-refresh.
        close();
        window.location.reload();
      })
      .catch(function(err) {
        done();
        if (err && err.name === 'AbortError') {
          alert('Saving the rule timed out. A background sync may still be running — try again in a few seconds.');
        } else {
          alert('Failed to save rule: ' + (err && err.message ? err.message : 'network error'));
        }
      });
  }

  modal.querySelector('.rule-modal-save').addEventListener('click', function() {
    var payload = buildPayload();
    if (!payload) return;
    // not_equals + dismiss can be a footgun (dismisses everything but the chosen
    // match). Preview affected count and confirm before applying.
    var anyNotEqualsDismiss = (
      payload.action === 'dismiss'
      && payload.conditions.some(function(c) { return c.match_op === 'not_equals'; })
    );
    if (anyNotEqualsDismiss) {
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
