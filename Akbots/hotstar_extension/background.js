// Ott-Bot Background Service Worker
// Captures MPD URLs and License URLs from Hotstar/OTT platforms

let capturedData = {
    mpdUrl: null,
    licenseUrl: null,
    title: null,
    timestamp: null,
    videos: []  // Track multiple videos on page
};

// Listen for completed requests with MPD
chrome.webRequest.onCompleted.addListener(
    (details) => {
        console.log("🎯 MPD Captured:", details.url);
        capturedData.mpdUrl = details.url;
        capturedData.timestamp = Date.now();

        // Save to storage
        chrome.storage.local.set({
            mpdUrl: details.url,
            captured: true,
            timestamp: Date.now()
        });

        // Send to Python backend
        notifyPython({ type: 'mpd', url: details.url });

        // Update popup
        updatePopup();
    },
    {
        urls: [
            "*://*.hotstar.com/*.mpd*",
            "*://*.disneystar.com/*.mpd*",
            "*://*.hotstar.com/*master*",
            "*://*.disneystar.com/*master*"
        ]
    }
);

// Listen for license requests
chrome.webRequest.onCompleted.addListener(
    (details) => {
        console.log("🔑 License URL Captured:", details.url);
        capturedData.licenseUrl = details.url;

        chrome.storage.local.set({ licenseUrl: details.url });
        notifyPython({ type: 'license', url: details.url });
        updatePopup();
    },
    {
        urls: [
            "*://*.hotstar.com/*license*",
            "*://*.hotstar.com/*drm*",
            "*://apix.hotstar.com/v2/fetch/license*"
        ]
    }
);

// Listen for messages from content script
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'mpd' && message.url) {
        console.log("📨 MPD from content:", message.url);
        capturedData.mpdUrl = message.url;
        capturedData.timestamp = Date.now();

        chrome.storage.local.set({
            mpdUrl: message.url,
            captured: true,
            timestamp: Date.now()
        });

        notifyPython({ type: 'mpd', url: message.url });
        updatePopup();
    }

    if (message.type === 'license' && message.url) {
        console.log("📨 License from content:", message.url);
        capturedData.licenseUrl = message.url;
        chrome.storage.local.set({ licenseUrl: message.url });
        updatePopup();
    }

    if (message.type === 'video_info') {
        capturedData.title = message.title;
        chrome.storage.local.set({ title: message.title });
        updatePopup();
    }

    sendResponse({ status: 'ok' });
    return true;
});

// Send data to Python backend
function notifyPython(data) {
    fetch('http://localhost:8765/mpd', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).catch(err => {
        console.log("⚠️ Python backend not listening:", err);
    });
}

// Update popup UI
function updatePopup() {
    chrome.action.setBadgeText({
        text: capturedData.mpdUrl ? '✓' : '...'
    });
    chrome.action.setBadgeBackgroundColor({
        color: capturedData.mpdUrl ? '#00ff00' : '#ffaa00'
    });
}

// Provide data to popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "getData") {
        chrome.storage.local.get(null, (data) => {
            sendResponse({
                ...capturedData,
                ...data
            });
        });
        return true;
    }
});

console.log("🎬 Ott-Bot Stream Capturer loaded!");
