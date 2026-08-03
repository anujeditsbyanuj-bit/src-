"""
Voice Chat web page — serverless P2P WebRTC voice/video chat room, served
straight out of the same aiohttp server that already powers File-to-Link
(see Akbots/filetolink/web_server.py). Ported in from the standalone
telegram_voice_bot.py script: same PeerJS-based mesh (voice + screen-share
+ chat/file transfer), just without its own ngrok tunnel / Pyrogram Client —
those are unnecessary here since this bot already has a running Client and
a public URL (STREAM_URL) once STREAM_BIN_CHANNEL is configured.

Room state (roomId + expiry) lives entirely in the URL's `?vc=` base64
param — nothing is stored server-side, exactly like the original script.
"""

from aiohttp import web

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Voice Chat - P2P Communication</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/peerjs@1.5.2/dist/peerjs.min.js"></script>
    <style>
        :root { --bg-primary: #0a0e1a; --bg-secondary: #141b2d; }
        body {
            background: linear-gradient(135deg, #0a0e1a 0%, #1e293b 100%);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            overflow: hidden;
        }

        @keyframes pulse-aura {
            0%, 100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.8), 0 0 25px 8px rgba(16, 185, 129, 0.6), inset 0 0 25px rgba(16, 185, 129, 0.3); transform: scale(1); }
            50% { box-shadow: 0 0 0 15px rgba(16, 185, 129, 0), 0 0 35px 12px rgba(16, 185, 129, 0.5), inset 0 0 35px rgba(16, 185, 129, 0.4); transform: scale(1.05); }
        }

        .active-speaker {
            animation: pulse-aura 0.8s cubic-bezier(0.4, 0, 0.6, 1) infinite;
            border-color: #10b981 !important;
            border-width: 5px !important;
        }

        .mic-badge {
            position: absolute; bottom: 5px; right: 5px; width: 28px; height: 28px;
            border-radius: 50%; display: flex; align-items: center; justify-content: center;
            transition: all 0.3s; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
        }
        .mic-badge.unmuted { background: linear-gradient(135deg, #10b981 0%, #059669 100%); }
        .mic-badge.muted { background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); }
        .mic-slash { opacity: 0; transition: opacity 0.3s ease, transform 0.3s ease; transform: scale(0.8) rotate(-10deg); }
        .mic-muted .mic-slash { opacity: 1; transform: scale(1) rotate(0deg); }

        .glass { background: rgba(20, 27, 45, 0.85); backdrop-filter: blur(30px); border: 1px solid rgba(255, 255, 255, 0.1); }
        @keyframes slideUp { from { transform: translateY(100%); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
        @keyframes fadeIn { from { opacity: 0; transform: scale(0.9); } to { opacity: 1; transform: scale(1); } }
        .slide-up { animation: slideUp 0.4s cubic-bezier(0.4, 0, 0.2, 1); }
        .fade-in { animation: fadeIn 0.5s cubic-bezier(0.4, 0, 0.2, 1); }

        .mic-active { box-shadow: 0 0 50px rgba(16, 185, 129, 0.7); background: linear-gradient(135deg, #10b981 0%, #3b82f6 100%); transition: all 0.4s; }
        .mic-active:hover { transform: scale(1.1); }
        .mic-muted { background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); box-shadow: 0 0 30px rgba(239, 68, 68, 0.5); transition: all 0.4s; }
        .mic-muted:hover { transform: scale(1.1); }

        .chat-bubble { max-width: 75%; word-wrap: break-word; animation: fadeIn 0.3s ease-out; }
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-thumb { background: rgba(59, 130, 246, 0.5); border-radius: 10px; }
        
        .toast-show { animation: slideDown 0.5s cubic-bezier(0.4, 0, 0.2, 1); }
        @keyframes slideDown { from { transform: translate(-50%, -150%); opacity: 0; } to { transform: translate(-50%, 0); opacity: 1; } }
        
        .btn-ripple { position: relative; overflow: hidden; }
    </style>
</head>
<body class="h-screen">

    <div id="joinModal" class="fixed inset-0 bg-black bg-opacity-70 flex items-end md:items-center justify-center z-50">
        <div class="glass rounded-t-3xl md:rounded-3xl w-full md:w-96 p-8 slide-up">
            <h2 class="text-3xl font-bold text-white mb-2 text-center">Join Voice Chat</h2>
            <input id="usernameInput" type="text" placeholder="Your name..." class="w-full px-5 py-4 rounded-xl bg-gray-700 bg-opacity-50 text-white mb-5"/>
            <button id="joinBtn" class="w-full py-4 rounded-xl font-semibold text-white bg-gradient-to-r from-blue-500 to-green-500 hover:scale-105 transition-all">🎙️ Join Voice Chat</button>
        </div>
    </div>

    <div id="mainInterface" class="hidden h-full flex flex-col fade-in">
        <header class="glass px-5 py-4 flex items-center justify-between shadow-2xl">
            <div>
                <h1 class="text-white font-bold text-xl flex items-center gap-2"><span class="w-3 h-3 bg-green-500 rounded-full animate-pulse"></span>Voice Chat</h1>
                <p class="text-gray-400 text-sm" id="participantCount">1 participant</p>
            </div>
            <button id="leaveBtn" class="px-6 py-2 rounded-lg bg-red-500 text-white font-medium hover:scale-105 transition-all">Leave</button>
        </header>

        <main class="flex-1 overflow-y-auto p-6">
            <div id="screenShareContainer" class="hidden mb-6 fade-in">
                <div class="relative bg-black rounded-2xl overflow-hidden"><video id="screenVideo" autoplay playsinline class="w-full"></video></div>
            </div>
            <div id="participantsGrid" class="grid gap-6"></div>
        </main>

        <div class="glass px-6 py-6 flex items-center justify-around">
            <button id="chatToggleBtn" class="p-4 rounded-full hover:bg-gray-700 hover:scale-110 transition-all"><svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg></button>
            <button id="micBtn" class="w-24 h-24 rounded-full flex items-center justify-center mic-muted transition-all relative">
                <svg class="w-11 h-11 text-white relative z-10" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"/></svg>
                <svg class="mic-slash absolute inset-0 w-full h-full text-white z-20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3.5" d="M5 3l14 18"/></svg>
            </button>
            <button id="screenShareBtn" class="p-4 rounded-full hover:bg-gray-700 hover:scale-110 transition-all"><svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg></button>
        </div>
    </div>

    <div id="chatDrawer" class="fixed inset-0 bg-black bg-opacity-70 hidden z-40">
        <div class="absolute right-0 top-0 h-full w-full md:w-96 glass flex flex-col slide-up">
            <div class="px-6 py-5 border-b border-gray-700 flex justify-between">
                <h3 class="text-white font-bold text-xl">Messages</h3>
                <button id="closeChatBtn" class="text-gray-400 hover:text-white"><svg class="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button>
            </div>
            <div id="chatMessages" class="flex-1 overflow-y-auto p-5 space-y-4"></div>
            <div class="p-5 border-t border-gray-700">
                <div class="flex gap-3 mb-3">
                    <label class="px-4 py-3 rounded-xl bg-gray-700 bg-opacity-50 hover:bg-opacity-70 cursor-pointer">
                        <input id="fileInput" type="file" accept="*/*" class="hidden"/>
                        <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"/></svg>
                    </label>
                </div>
                <div class="flex gap-3">
                    <input id="messageInput" type="text" placeholder="Type a message..." class="flex-1 px-5 py-4 rounded-xl bg-gray-700 text-white focus:outline-none"/>
                    <button id="sendMessageBtn" class="px-6 py-4 rounded-xl bg-blue-500 hover:bg-blue-600 text-white"><svg class="w-6 h-6" fill="currentColor" viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg></button>
                </div>
            </div>
        </div>
    </div>

    <div id="toast" class="fixed top-8 left-1/2 transform -translate-x-1/2 px-8 py-4 rounded-2xl glass hidden z-50 shadow-2xl toast-show"><p id="toastMessage" class="text-white font-medium text-center"></p></div>

    <script>
        const state = {
            peer: null, myPeerId: null, roomId: null, myUsername: '',
            isMuted: true, isScreenSharing: false, localStream: null,
            screenStream: null, peers: new Map(), audioContext: null, myAnalyser: null,
        };

        function showToast(message, duration = 3500) {
            const toast = document.getElementById('toast');
            document.getElementById('toastMessage').textContent = message;
            toast.classList.remove('hidden');
            setTimeout(() => toast.classList.add('hidden'), duration);
        }

        function getInitials(name) { return name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2); }
        function getAvatarColor(name) {
            const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4', '#14b8a6'];
            return colors[name.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0) % colors.length];
        }

        function parseRoomFromURL() {
            const params = new URLSearchParams(window.location.search);
            const encoded = params.get('vc');
            if (!encoded) return null;
            try {
                const decoded = JSON.parse(atob(encoded));
                const now = Math.floor(Date.now() / 1000);
                if (now > decoded.expiry) { showToast('Link expired'); return null; }
                return decoded.roomId;
            } catch { return null; }
        }

        async function setupMicrophone() {
            try {
                state.localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                state.localStream.getAudioTracks().forEach(t => t.enabled = false);
                setupSelfSpeakingDetection();
                return true;
            } catch { showToast('Microphone required'); return false; }
        }

        // --- FIX 4: Lower Volume Threshold ---
        function setupSelfSpeakingDetection() {
            if (!state.audioContext) state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            state.myAnalyser = state.audioContext.createAnalyser();
            const microphone = state.audioContext.createMediaStreamSource(state.localStream);
            const dataArray = new Uint8Array(state.myAnalyser.frequencyBinCount);
            microphone.connect(state.myAnalyser);

            function checkMySpeaking() {
                state.myAnalyser.getByteFrequencyData(dataArray);
                const avg = dataArray.reduce((a, b) => a + b) / dataArray.length;
                const myAvatar = document.querySelector('[data-peer-id="self"] .avatar-circle');
                if (myAvatar) myAvatar.classList.toggle('active-speaker', avg > 8 && !state.isMuted);
                requestAnimationFrame(checkMySpeaking);
            }
            checkMySpeaking();
        }

        // --- FIX 1 & 5: Mesh Network & Auto-Healing Probe ---
        function initializePeer() {
            state.peer = new Peer(state.roomId, { config: { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] } });

            state.peer.on('open', id => {
                console.log('✅ HOST Mode');
                state.myPeerId = id;
            });

            state.peer.on('error', err => {
                if (err.type === 'unavailable-id') {
                    console.log('ℹ️ GUEST Mode');
                    state.peer = new Peer({ config: { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] } });
                    state.peer.on('open', id => {
                        state.myPeerId = id;
                        connectToPeer(state.roomId);
                        startGuestProbe(); // Start Pinging for Host reconnect
                    });
                    bindPeerEvents(); 
                }
            });

            bindPeerEvents();
        }

        function startGuestProbe() {
            // FIX 5: Guests will quietly ping the Room ID every 5 seconds.
            // If the original Host left and rejoined, this reconnects them.
            setInterval(() => {
                if (state.myPeerId !== state.roomId && state.peer && !state.peer.destroyed) {
                    const probe = state.peer.connect(state.roomId, { reliable: false });
                    probe.on('open', () => {
                        probe.send({ type: 'probe_sync', peerId: state.myPeerId });
                        setTimeout(() => probe.close(), 1000);
                    });
                }
            }, 5000);
        }

        function bindPeerEvents() {
            state.peer.on('connection', setupDataConnection);
            state.peer.on('call', call => {
                call.answer(state.localStream);
                call.on('stream', stream => handleIncomingStream(call.peer, stream));
                call.on('close', () => removePeer(call.peer));
            });
        }

        function setupDataConnection(conn) {
            conn.on('open', () => {
                conn.send({ type: 'username', username: state.myUsername, peerId: state.myPeerId, isMuted: state.isMuted });
                
                // HOST sends peer list with a slight delay to allow connections (FIX 1)
                if (state.myPeerId === state.roomId) {
                    const otherPeers = Array.from(state.peers.keys()).filter(id => id !== conn.peer);
                    if (otherPeers.length > 0) {
                        setTimeout(() => conn.send({ type: 'peer_list', peers: otherPeers }), 500);
                    }
                }

                if (!state.peers.has(conn.peer)) {
                    state.peers.set(conn.peer, { connection: conn, username: 'Unknown', isSpeaking: false, isMuted: true });
                    updateParticipantsUI();
                } else state.peers.get(conn.peer).connection = conn;
            });
            conn.on('data', data => handleIncomingData(conn.peer, data));
            conn.on('close', () => removePeer(conn.peer));
        }

        const fileBuffer = new Map(); // For chunking

        function handleIncomingData(peerId, data) {
            if (data.type === 'username') {
                if (!state.peers.has(peerId)) state.peers.set(peerId, { username: data.username, isSpeaking: false, isMuted: data.isMuted });
                else { state.peers.get(peerId).username = data.username; state.peers.get(peerId).isMuted = data.isMuted; }
                updateParticipantsUI();
            } else if (data.type === 'peer_list') {
                // FIX 1: Staggered connections to prevent browser overload
                data.peers.forEach((otherPeerId, index) => {
                    setTimeout(() => {
                        if (!state.peers.has(otherPeerId) && otherPeerId !== state.myPeerId) connectToPeer(otherPeerId);
                    }, index * 800);
                });
            } else if (data.type === 'probe_sync') {
                // FIX 5: Host receives ping from guest, connects back if needed
                if (!state.peers.has(data.peerId)) connectToPeer(data.peerId);
            } else if (data.type === 'mic-status') {
                if (state.peers.has(peerId)) {
                    state.peers.get(peerId).isMuted = data.isMuted;
                    updateParticipantsUI(); // Refresh UI
                }
            } else if (data.type === 'message') {
                addChatMessage(data.username, data.message, false);
            } else if (data.type === 'file_chunk') {
                // FIX 2: Reassembling File Chunks
                if (!fileBuffer.has(data.fileId)) fileBuffer.set(data.fileId, []);
                const buffer = fileBuffer.get(data.fileId);
                buffer[data.chunkIndex] = data.chunk;
                
                if (buffer.filter(Boolean).length === data.totalChunks) {
                    const completeData = buffer.join('');
                    addFileMessage(data.username, { data: completeData, name: data.metadata.name, type: data.metadata.type, size: data.metadata.size }, false);
                    fileBuffer.delete(data.fileId);
                }
            } else if (data.type === 'file') {
                addFileMessage(data.username, data.file, false);
            }
        }

        function connectToPeer(peerId) {
            if (peerId === state.myPeerId || state.peers.has(peerId)) return;
            const conn = state.peer.connect(peerId, { reliable: true });
            setupDataConnection(conn);
            const call = state.peer.call(peerId, state.localStream);
            call.on('stream', stream => handleIncomingStream(peerId, stream));
            call.on('close', () => removePeer(peerId));
            state.peers.set(peerId, { connection: conn, call: call, username: 'Connecting...', isSpeaking: false, isMuted: true });
            updateParticipantsUI();
        }

        function handleIncomingStream(peerId, stream) {
            const audio = new Audio(); audio.srcObject = stream; audio.autoplay = true;
            if (state.peers.has(peerId)) {
                state.peers.get(peerId).audioElement = audio;
                detectSpeaking(peerId, stream);
            }
        }

        // --- FIX 4: Lower Threshold for Guests ---
        function detectSpeaking(peerId, stream) {
            if (!state.audioContext) state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const analyser = state.audioContext.createAnalyser();
            const microphone = state.audioContext.createMediaStreamSource(stream);
            const dataArray = new Uint8Array(analyser.frequencyBinCount);
            microphone.connect(analyser);

            function check() {
                if (!state.peers.has(peerId)) return;
                analyser.getByteFrequencyData(dataArray);
                const avg = dataArray.reduce((a, b) => a + b) / dataArray.length;
                const isSpeaking = avg > 8; // Lowered to 8
                if (state.peers.get(peerId).isSpeaking !== isSpeaking) {
                    state.peers.get(peerId).isSpeaking = isSpeaking;
                    const avatar = document.querySelector(`[data-peer-id="${peerId}"] .avatar-circle`);
                    if (avatar) avatar.classList.toggle('active-speaker', isSpeaking);
                }
                requestAnimationFrame(check);
            }
            check();
        }

        function removePeer(peerId) {
            if (state.peers.has(peerId)) {
                const p = state.peers.get(peerId);
                p.audioElement?.pause(); p.connection?.close(); p.call?.close();
                state.peers.delete(peerId);
                updateParticipantsUI();
            }
        }

        function updateParticipantsUI() {
            const grid = document.getElementById('participantsGrid');
            const total = state.peers.size + 1;
            grid.className = `grid gap-6 ${total === 1 ? 'grid-cols-1' : total <= 4 ? 'grid-cols-2' : 'md:grid-cols-3 grid-cols-2'}`;
            grid.innerHTML = '';
            grid.appendChild(createAvatar(state.myUsername, true, false, 'self', state.isMuted));
            state.peers.forEach((peer, peerId) => grid.appendChild(createAvatar(peer.username, false, peer.isSpeaking, peerId, peer.isMuted)));
            document.getElementById('participantCount').textContent = `${total} participant${total > 1 ? 's' : ''}`;
        }

        function createAvatar(username, isSelf, isSpeaking, peerId, isMuted) {
            const div = document.createElement('div'); div.className = 'flex flex-col items-center gap-3 fade-in';
            div.dataset.peerId = peerId;
            const container = document.createElement('div'); container.className = 'relative';
            const avatar = document.createElement('div');
            avatar.className = `avatar-circle w-28 h-28 md:w-36 md:h-36 rounded-full flex items-center justify-center text-3xl font-bold text-white border-4 ${isSpeaking ? 'active-speaker' : ''}`;
            avatar.style.backgroundColor = getAvatarColor(username); avatar.style.borderColor = 'rgba(255,255,255,0.2)';
            avatar.textContent = getInitials(username);
            const mic = document.createElement('div');
            mic.className = `mic-badge ${isMuted ? 'muted' : 'unmuted'}`;
            mic.innerHTML = isMuted ? '<svg class="w-4 h-4 text-white" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M13.477 14.89A6 6 0 015.11 6.524l8.367 8.368zm1.414-1.414L6.524 5.11a6 6 0 018.367 8.367zM18 10a8 8 0 11-16 0 8 8 0 0116 0z" clip-rule="evenodd"/></svg>'
                                    : '<svg class="w-4 h-4 text-white" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M7 4a3 3 0 016 0v4a3 3 0 11-6 0V4zm4 10.93A7.001 7.001 0 0017 8a1 1 0 10-2 0A5 5 0 015 8a1 1 0 00-2 0 7.001 7.001 0 006 6.93V17H6a1 1 0 100 2h8a1 1 0 100-2h-3v-2.07z" clip-rule="evenodd"/></svg>';
            container.appendChild(avatar); container.appendChild(mic);
            const label = document.createElement('p'); label.className = 'text-white font-medium'; label.textContent = isSelf ? `${username} (You)` : username;
            div.appendChild(container); div.appendChild(label);
            return div;
        }

        // --- FIX 3: Video Playback in Chat ---
        function addFileMessage(username, file, isSelf) {
            const container = document.getElementById('chatMessages');
            const wrapper = document.createElement('div'); wrapper.className = `flex ${isSelf ? 'justify-end' : 'justify-start'}`;
            const bubble = document.createElement('div'); bubble.className = `chat-bubble px-5 py-3 rounded-2xl ${isSelf ? 'bg-blue-600' : 'bg-gray-700'}`;
            const name = document.createElement('p'); name.className = 'text-xs text-white mb-2 font-semibold'; name.textContent = username;
            bubble.appendChild(name);
            
            if (file.type.startsWith('image/')) {
                const img = document.createElement('img'); img.src = file.data; img.className = 'rounded-xl mb-2 cursor-pointer'; img.onclick = () => window.open(file.data);
                bubble.appendChild(img);
            } else if (file.type.startsWith('video/')) {
                const video = document.createElement('video'); video.src = file.data; video.controls = true; video.className = 'rounded-xl mb-2 w-full';
                bubble.appendChild(video);
            } else {
                const doc = document.createElement('div'); doc.className = 'text-sm text-gray-200 bg-gray-800 p-2 rounded mb-2 break-all';
                doc.textContent = `📎 ${file.name} (${(file.size/1024).toFixed(1)} KB)`;
                bubble.appendChild(doc);
            }
            
            const dl = document.createElement('a'); dl.href = file.data; dl.download = file.name; dl.className = 'text-sm text-white bg-gray-600 px-3 py-1 rounded hover:bg-gray-500 inline-block'; dl.textContent = '⬇️ Download';
            bubble.appendChild(dl); wrapper.appendChild(bubble); container.appendChild(wrapper); container.scrollTop = container.scrollHeight;
        }

        function addChatMessage(username, message, isSelf) {
            const container = document.getElementById('chatMessages');
            const wrapper = document.createElement('div'); wrapper.className = `flex ${isSelf ? 'justify-end' : 'justify-start'}`;
            const bubble = document.createElement('div'); bubble.className = `chat-bubble px-5 py-3 rounded-2xl ${isSelf ? 'bg-blue-600' : 'bg-gray-700'}`;
            bubble.innerHTML = `<p class="text-xs text-white mb-1 font-semibold">${username}</p><p class="text-white break-words">${message}</p>`;
            wrapper.appendChild(bubble); container.appendChild(wrapper); container.scrollTop = container.scrollHeight;
        }

        function sendMessage(message) {
            if (!message.trim()) return;
            addChatMessage(state.myUsername, message, true);
            state.peers.forEach(peer => { if (peer.connection?.open) peer.connection.send({ type: 'message', username: state.myUsername, message }); });
        }

        // --- FIX 2: File Chunking Logic ---
        async function sendFile(file) {
            if (!file) return;
            showToast(`Sending ${file.name}...`);
            const reader = new FileReader();
            reader.onload = async (e) => {
                const dataURL = e.target.result;
                const fileData = { data: dataURL, name: file.name, type: file.type, size: file.size };
                addFileMessage(state.myUsername, fileData, true);
                
                const chunkSize = 64 * 1024; // 64KB
                if (dataURL.length > chunkSize) {
                    const totalChunks = Math.ceil(dataURL.length / chunkSize);
                    const fileId = Math.random().toString(36).substring(2);
                    for (let i = 0; i < totalChunks; i++) {
                        const chunk = dataURL.slice(i * chunkSize, (i + 1) * chunkSize);
                        state.peers.forEach(peer => {
                            if (peer.connection?.open) {
                                peer.connection.send({ type: 'file_chunk', fileId, chunkIndex: i, totalChunks, chunk, metadata: { name: file.name, type: file.type, size: file.size }, username: state.myUsername });
                            }
                        });
                        await new Promise(r => setTimeout(r, 15)); // Delay to prevent buffer overflow
                    }
                } else {
                    state.peers.forEach(peer => { if (peer.connection?.open) peer.connection.send({ type: 'file', file: fileData, username: state.myUsername }); });
                }
                showToast('File sent!');
            };
            reader.readAsDataURL(file);
        }

        async function toggleScreenShare() {
            if (state.isScreenSharing) { state.screenStream?.getTracks().forEach(t => t.stop()); document.getElementById('screenShareContainer').classList.add('hidden'); state.isScreenSharing = false; showToast('Screen sharing stopped'); return; }
            try {
                state.screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true });
                document.getElementById('screenVideo').srcObject = state.screenStream; document.getElementById('screenShareContainer').classList.remove('hidden');
                state.screenStream.getVideoTracks()[0].onended = toggleScreenShare; state.isScreenSharing = true;
            } catch { showToast('Screen sharing failed or unsupported'); }
        }

        document.getElementById('joinBtn').addEventListener('click', async () => {
            const username = document.getElementById('usernameInput').value.trim();
            if (!username) { showToast('Enter name'); return; }
            state.myUsername = username; localStorage.setItem('voiceChatUsername', username);
            if (!await setupMicrophone()) return;
            initializePeer();
            document.getElementById('joinModal').classList.add('hidden'); document.getElementById('mainInterface').classList.remove('hidden');
            updateParticipantsUI();
        });

        document.getElementById('micBtn').addEventListener('click', () => {
            state.isMuted = !state.isMuted; state.localStream.getAudioTracks().forEach(t => t.enabled = !state.isMuted);
            document.getElementById('micBtn').classList.toggle('mic-muted', state.isMuted); document.getElementById('micBtn').classList.toggle('mic-active', !state.isMuted);
            updateParticipantsUI(); state.peers.forEach(p => { if (p.connection?.open) p.connection.send({ type: 'mic-status', isMuted: state.isMuted }); });
        });

        document.getElementById('leaveBtn').addEventListener('click', () => {
            state.peers.forEach(p => { p.connection?.close(); p.call?.close(); });
            state.peer?.destroy(); window.location.href = 'https://google.com';
        });

        document.getElementById('chatToggleBtn').addEventListener('click', () => document.getElementById('chatDrawer').classList.remove('hidden'));
        document.getElementById('closeChatBtn').addEventListener('click', () => document.getElementById('chatDrawer').classList.add('hidden'));
        document.getElementById('sendMessageBtn').addEventListener('click', () => { sendMessage(document.getElementById('messageInput').value); document.getElementById('messageInput').value = ''; });
        document.getElementById('messageInput').addEventListener('keypress', e => { if (e.key === 'Enter') { sendMessage(e.target.value); e.target.value = ''; } });
        document.getElementById('screenShareBtn').addEventListener('click', toggleScreenShare);

        document.getElementById('fileInput').addEventListener('change', e => {
            if (e.target.files[0]) { sendFile(e.target.files[0]); }
            e.target.value = '';
        });

        window.addEventListener('DOMContentLoaded', () => {
            state.roomId = parseRoomFromURL();
            if (!state.roomId) document.getElementById('joinModal').classList.add('hidden');
            const saved = localStorage.getItem('voiceChatUsername'); if (saved) document.getElementById('usernameInput').value = saved;
        });
    </script>
</body>
</html>
"""

routes = web.RouteTableDef()


@routes.get("/vc", allow_head=True)
async def vc_page(request: web.Request) -> web.Response:
    return web.Response(text=HTML_CONTENT, content_type="text/html")
