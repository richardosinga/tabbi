(async () => {
  const { tabbiUrl = 'https://tab.bi' } = await chrome.storage.local.get('tabbiUrl');
  document.getElementById('tabbi-url').value = tabbiUrl;

  document.getElementById('save-btn').onclick = async () => {
    const url = document.getElementById('tabbi-url').value.trim().replace(/\/$/, '');
    await chrome.storage.local.set({ tabbiUrl: url });
    const msg = document.getElementById('saved-msg');
    msg.style.display = 'inline';
    setTimeout(() => { msg.style.display = 'none'; }, 2000);
  };
})();
