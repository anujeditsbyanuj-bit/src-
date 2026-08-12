// Ott-Bot Content Script
// Injected into Hotstar/OTT pages to capture MPD and License URLs

console.log("🎬 Ott-Bot Content Script loaded!");

// Override fetch API
const originalFetch = window.fetch;
window.fetch = function(...args) {
    const url = args[0];

    if (typeof url === 'string') {
        // Capture MPD URLs
        if (url.includes('.mpd') || url.includes('master')) {
            console.log("🎯 MPD via Fetch:", url);
            sendToExtension({ type: 'mpd', url: url });
        }

        // Capture license URLs
        if (url.includes('license') || url.includes('drm') || url.includes('token')) {
            console.log("🔑 License via Fetch:", url);
            sendToExtension({ type: 'license', url: url });
        }
    }

    return originalFetch.apply(this, args);
};

// Override XHR
const originalXHR = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    if (typeof url === 'string') {
        this.addEventListener('load', function() {
            if (url.includes('.mpd') || url.includes('master')) {
                console.log("🎯 MPD via XHR:", url);
                sendToExtension({ type: 'mpd', url: url });
            }

            if (url.includes('license') || url.includes('drm')) {
                console.log("🔑 License via XHR:", url);
                sendToExtension({ type: 'license', url: url });
            }
        });
    }
    return originalXHR.apply(this, [method, url, ...rest]);
};

// Send message to extension background
function sendToExtension(data) {
    chrome.runtime.sendMessage(data, () => {});
}

// Detect video title and send to extension
function detectVideoInfo() {
    // Try to get page title
    const title = document.title || '';
    if (title) {
        sendToExtension({ type: 'video_info', title: title });
    }

    // Try to find content ID from URL
    const urlMatch = window.location.href.match(/\/(\d{10,})/);
    if (urlMatch) {
        sendToExtension({ type: 'content_id', id: urlMatch[1] });
    }
}

// Run on load
setTimeout(detectVideoInfo, 2000);

// Watch for video elements
const observer = new MutationObserver((mutations) => {
    const videos = document.querySelectorAll('video');
    if (videos.length > 0) {
        console.log("📹 Video element found!");
        detectVideoInfo();
    }
});

observer.observe(document.body, {
    childList: true,
    subtree: true
});

console.log("🔍 Watching for video streams...");
