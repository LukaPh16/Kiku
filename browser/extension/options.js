// Options page: port + token for the Kiku WebSocket. Saving makes the background reconnect.
const $ = id => document.getElementById(id);

async function load() {
    const {port, token} = await browser.storage.local.get(['port', 'token']);
    if (port) $('port').value = port;
    if (token) $('token').value = token;
    const st = await browser.runtime.sendMessage({kiku: 'status'}).catch(() => null);
    $('status').textContent = st ? `Status: ${st}` : '';
}

$('save').addEventListener('click', async () => {
    await browser.storage.local.set({port: Number($('port').value) || 8766, token: $('token').value.trim()});
    $('status').textContent = 'Saved. Connecting...';
    setTimeout(load, 1500);
});

load();
