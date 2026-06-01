(async () => {
  const store = chrome.storage.local;
  let stored = await store.get(['tabbiUrl', 'passphrases']);
  let tabbiUrl = (stored.tabbiUrl || 'https://tab.bi').replace(/\/$/, '');
  let passphrases = stored.passphrases || {};

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const pageUrl = tab.url || '';
  const pageTitle = tab.title || '';

  const ADD_TRIP_VALUE = '__add_trip__';

  // ── Cog / settings panel ──────────────────────────────────────────────────
  const settingsPanel = document.getElementById('settings-panel');
  document.getElementById('server-url').value = tabbiUrl;

  document.getElementById('cog-btn').onclick = () => {
    settingsPanel.hidden = !settingsPanel.hidden;
  };

  document.getElementById('server-save').onclick = async () => {
    const url = document.getElementById('server-url').value.trim().replace(/\/$/, '');
    await store.set({ tabbiUrl: url });
    tabbiUrl = url;
    settingsPanel.hidden = true;
    if (url) await tryLoadPlans();
  };
  document.getElementById('server-url').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('server-save').click();
  });

  // ── Plan select ───────────────────────────────────────────────────────────
  const planSelect = document.getElementById('plan-select');

  planSelect.addEventListener('change', () => {
    const isAdd = planSelect.value === ADD_TRIP_VALUE;
    document.getElementById('unlock-section').hidden = !isAdd;
    document.getElementById('unlock-btn').hidden = !isAdd;
    document.getElementById('add-btn').hidden = isAdd;
    if (isAdd) document.getElementById('phrase-input').focus();
  });

  // ── Unlock ────────────────────────────────────────────────────────────────
  document.getElementById('unlock-btn').onclick = handleUnlock;
  document.getElementById('phrase-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') handleUnlock();
  });

  // ── Add to trip ───────────────────────────────────────────────────────────
  document.getElementById('add-btn').onclick = handleAdd;

  // ── Init: load trips from stored passphrases ──────────────────────────────
  if (tabbiUrl && Object.keys(passphrases).length) {
    await tryLoadPlans();
  }

  // ─────────────────────────────────────────────────────────────────────────

  async function tryLoadPlans() {
    try {
      const { plans } = await callApi('/api/plans', { passphrases });
      if (plans && plans.length) renderPlans(plans);
    } catch (e) {
      showMsg(`Cannot reach ${tabbiUrl || 'server'} — is Tabbi running?`, 'msg-err');
    }
  }

  function renderPlans(plans) {
    // Rebuild select: real trips + sentinel at end
    const currentSlug = planSelect.value;
    planSelect.innerHTML = '';
    plans.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.slug;
      opt.textContent = p.title;
      planSelect.appendChild(opt);
    });
    const addOpt = document.createElement('option');
    addOpt.value = ADD_TRIP_VALUE;
    addOpt.textContent = '＋ Add another trip';
    planSelect.appendChild(addOpt);

    // Restore previous selection if still present
    if (currentSlug && currentSlug !== ADD_TRIP_VALUE && [...planSelect.options].some(o => o.value === currentSlug)) {
      planSelect.value = currentSlug;
    }

    document.getElementById('plan-section').hidden = false;
    document.getElementById('unlock-section').hidden = true;
    document.getElementById('unlock-btn').hidden = true;
    document.getElementById('add-btn').hidden = false;
  }

  async function handleUnlock() {
    if (!tabbiUrl) {
      settingsPanel.hidden = false;
      document.getElementById('server-url').focus();
      return;
    }
    const phrase = document.getElementById('phrase-input').value.trim();
    if (!phrase) return;

    const btn = document.getElementById('unlock-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
    clearMsg();

    try {
      const { plans } = await callApi('/api/plans', { passphrases, passphrase: phrase });
      if (!plans || !plans.length) {
        showMsg('No trip found with that passphrase.', 'msg-err');
      } else {
        plans.forEach(p => { passphrases[p.slug] = phrase; });
        await store.set({ passphrases });
        document.getElementById('phrase-input').value = '';
        renderPlans(plans);
        clearMsg();
      }
    } catch (e) {
      showMsg(`Error: ${e.message}`, 'msg-err');
    }

    btn.disabled = false;
    btn.textContent = 'Unlock trip';
  }

  async function handleAdd() {
    const slug = planSelect.value;
    if (!slug || slug === ADD_TRIP_VALUE) return;
    const passphrase = passphrases[slug];
    if (!passphrase) return;

    const btn = document.getElementById('add-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Reading page…';
    clearMsg();

    let pageContent = '';
    try {
      const [{ result }] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const clone = document.body.cloneNode(true);
          clone.querySelectorAll('script,style,noscript,nav,header,footer,aside,[aria-hidden="true"]').forEach(el => el.remove());
          return (clone.innerText || clone.textContent || '').replace(/\s{3,}/g, '\n\n').trim().slice(0, 10000);
        },
      });
      pageContent = result || '';
    } catch { /* backend fetches directly */ }

    btn.innerHTML = '<span class="spinner"></span>Asking AI…';

    try {
      const data = await callApi('/api/add-from-url', {
        url: pageUrl, plan_slug: slug, passphrase,
        page_content: pageContent, page_title: pageTitle,
      });
      if (data.error === 'unauthorized') {
        delete passphrases[slug];
        await store.set({ passphrases });
        showMsg('Passphrase rejected — re-unlock via the dropdown.', 'msg-err');
      } else if (data.error) {
        showMsg(data.error, 'msg-err');
      } else {
        showAdded(data.added, data.message);
      }
    } catch (e) {
      showMsg(`Error: ${e.message}`, 'msg-err');
    }

    btn.disabled = false;
    btn.textContent = 'Add to trip';
  }

  async function callApi(path, body) {
    const resp = await fetch(tabbiUrl + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return resp.json();
  }

  function showAdded(added, message) {
    if (!added || !added.length) {
      showMsg(message || 'No matching places found for your trip stops.', 'msg-info');
      return;
    }
    const el = document.getElementById('msg');
    el.hidden = false;
    el.className = 'msg msg-ok';
    el.innerHTML = '<strong>Added to trip:</strong><ul>' +
      added.map(a => `<li>${esc(a.title)} <span class="city">${esc(a.city)}</span></li>`).join('') +
      '</ul>';
  }

  function showMsg(text, cls) {
    const el = document.getElementById('msg');
    el.hidden = false;
    el.className = `msg ${cls}`;
    el.textContent = text;
  }

  function clearMsg() {
    document.getElementById('msg').hidden = true;
  }

  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
})();
