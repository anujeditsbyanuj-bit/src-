// Ott-Bot Popup Script
// Auto-refreshes and displays captured data

let autoRefreshInterval = null;

// Start auto-refresh on popup open
startAutoRefresh();

function startAutoRefresh() {
    loadData();
    autoRefreshInterval = setInterval(loadData, 1000);
}

function stopAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
    }
}

function loadData() {
    chrome.runtime.sendMessage({ action: "getData" }, (data) => {
        if (!data) return;

        // Video info
        if (data.title) {
            document.getElementById('videoSection').style.display = 'block';
            document.getElementById('videoTitle').textContent =
                data.title.length > 50 ? data.title.substring(0, 50) + '...' : data.title;
        }

        // MPD URL
        if (data.mpdUrl) {
            document.getElementById('mpdSection').style.display = 'block';
            document.getElementById('mpdUrl').textContent = data.mpdUrl;

            if (data.captured) {
                const status = document.getElementById('status');
                status.className = 'status ready';
                status.textContent = '✅ MPD & License Captured!';
            }
        }

        // License URL
        if (data.licenseUrl) {
            document.getElementById('licenseSection').style.display = 'block';
            document.getElementById('licenseUrl').textContent = data.licenseUrl;
        }
    });
}

function copyToClipboard(elementId) {
    const text = document.getElementById(elementId).textContent;

    navigator.clipboard.writeText(text).then(() => {
        // Show copied feedback
        const btn = event.target;
        const originalText = btn.textContent;
        btn.textContent = '✅ Copied!';
        setTimeout(() => {
            btn.textContent = originalText;
        }, 2000);
    }).catch(() => {
        // Fallback
        const textarea = document.createElement('textarea');
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);

        const btn = event.target;
        const originalText = btn.textContent;
        btn.textContent = '✅ Copied!';
        setTimeout(() => {
            btn.textContent = originalText;
        }, 2000);
    });
}

function startDownload() {
    // Check if Python backend is running
    fetch('http://localhost:8765/mpd', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            type: 'download_request',
            mpdUrl: document.getElementById('mpdUrl')?.textContent,
            licenseUrl: document.getElementById('licenseUrl')?.textContent,
            title: document.getElementById('videoTitle')?.textContent
        })
    })
    .then(response => response.json())
    .then(data => {
        alert("✅ Download started!\n\nCheck Python backend console for progress.");
    })
    .catch(err => {
        alert("⚠️ Python backend not running!\n\nPlease run: python bot/main.py\n\nOr copy the MPD URL and send it to your Telegram bot.");
    });
}

// Cleanup on popup close
window.addEventListener('unload', () => {
    // Don't stop - keep capturing
});
