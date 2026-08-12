
// Embeds resueltos desde PHP (sin fetch del browser, sin CORS)
const EMBEDS = {"latino":{"remux":"https://remux.unlimplay.com/remux?id=1022789","streamwish":"https://hglink.to/e/xwcvr3g2pd0o","vidhide":"https://minochinos.com/embed/rpfesmmuikkb","voe":"https://voe.sx/e/o8lstwriy6rm","direct":"https://s12.vimeos.net/hls2/03/00000/yh723kv19oal_h/master.m3u8?t=8z0Rd9ppm8o2YenqoRFQQowpL-OYeAcawnxugZdGv6Q&s=1785959184&e=43200&v=312006658&i=0.0&sp=0&r=e","goodstream":"https://goodstream.one/embed-ov28ojao8dsy.html","streamhg":"https://hlswish.com/e/ync535yd4tfl","voe 2":"https://voe.sx/e/07gjsjf8u5vn","streamwish 2":"https://streamwish.to/e/z0xmal10v19h","filemoon":"https://bysejikuar.com/e/8rtwx6j2equb","vidhide 2":"https://vidhidepro.com/v/2ajluf6gbjfc","netu":"https://waaw.to/f/6fWud4m6wlE8"},"español":{"streamwish":"https://hglink.to/e/hx6v2wjkguzl","vidhide":"https://minochinos.com/embed/bqq1a2kamgqn","voe":"https://voe.sx/e/mzoqrnaotvnj"},"subtitulado":{"streamwish":"https://hglink.to/e/sucr1pyl2023","vidhide":"https://minochinos.com/embed/j0nqwemvd06e","voe":"https://voe.sx/e/y4cli4adz1mw","streamwish 2":"https://streamwish.to/e/jbpn24b29hqv","filemoon":"https://bysejikuar.com/e/hebpvnq2gvm7","vidhide 2":"https://vidhidepro.com/v/h7wl56pf762v","netu":"https://waaw.to/f/PMf6eR8macWo"},"espanol":{"streamwish":"https://streamwish.to/e/4f4blji9d7iv","vidhide":"https://vidhidepro.com/v/diwuwo4w2n4q","netu":"https://waaw.to/f/8Aed3HwRl2Uy"}};

const LANG_ORDER = ['latino', 'español', 'subtitulado'];

// ── Iconos de servidor ─────────────────────────────────────────────────────
const SERVER_ICONS_MAP = {
    'vidhide':    { bg:'#16a34a', icon:'▶' },
    'streamwish': { bg:'#ea580c', icon:'✦' },
    'filemoon':   { bg:'#6b7280', icon:'∞' },
    'voe':        { bg:'#2563eb', icon:'◉' },
    'rapidvideo': { bg:'#7c3aed', icon:'▶' },
    'streamtape': { bg:'#ca8a04', icon:'▶' },
    'doodstream': { bg:'#dc2626', icon:'●' },
    'mixdrop':    { bg:'#0891b2', icon:'▶' },
    'streamhg':   { bg:'#be185d', icon:'▶' },
    'earnvids':   { bg:'#15803d', icon:'▶' },
    'direct':     { bg:'#0d9488', icon:'⚡' },
    'proxy':      { bg:'#0891b2', icon:'⚡' },
    'remux':      { bg:'#059669', icon:'⚡' },
    'vimeus':     { bg:'#7c3aed', icon:'▶' },
    'gnula':      { bg:'#b45309', icon:'▶' },
    'cuevana':    { bg:'#0369a1', icon:'▶' },
    'fembed':     { bg:'#7c3aed', icon:'▶' },
    'uqload':     { bg:'#4f46e5', icon:'▶' },
    'mp4upload':  { bg:'#0f766e', icon:'▶' },
    'okru':       { bg:'#f97316', icon:'▶' },
};
function getServerIcon(name) {
    const key = (name || '').toLowerCase().replace(/\s+\d+$/, '');
    for (const [k, v] of Object.entries(SERVER_ICONS_MAP)) {
        if (key.includes(k)) return v;
    }
    return { bg:'#374151', icon:null };
}
// Extrae el hostname de la URL del embed para pedir su favicon real
function getHostname(url) {
    try { return new URL(url).hostname; } catch(e) { return null; }
}
// Ícono de servidor "profesional": intenta mostrar el favicon real del sitio
// (a partir de la URL del embed) y si falla (o no hay URL) cae al badge de
// color con la inicial/símbolo de siempre. baseName ya viene sin el sufijo
// " 2", " 3" (ese se maneja aparte con un badge numérico, ver buildServerBar).
function serverIconHtml(baseName, url, badgeNum) {
    const ico     = getServerIcon(baseName);
    const label   = ico.icon || (baseName||'??').substring(0,2).toUpperCase();
    const host    = getHostname(url);
    const badge   = badgeNum ? `<span class="di-badge">${badgeNum}</span>` : '';
    if (host) {
        // Con favicon real: sin fondo de color, que se vea solo la imagen.
        // Si falla la carga, recién ahí se agrega el fondo de color + letra
        // como respaldo (si no, quedaría invisible sobre el fondo oscuro).
        const fav = `https://www.google.com/s2/favicons?sz=64&domain=${encodeURIComponent(host)}`;
        return `<span class="di-ico">`
             + `<img src="${fav}" alt="" loading="lazy" `
             + `onerror="this.parentElement.classList.add('di-ico-fallback');`
             + `this.parentElement.style.background='${ico.bg}';`
             + `this.replaceWith(document.createTextNode('${label}'))">`
             + `${badge}</span>`;
    }
    // Sin URL/host disponible: no hay favicon que pedir, va directo el badge de color
    return `<span class="di-ico di-ico-fallback" style="background:${ico.bg}">${label}${badge}</span>`;
}
// Recorta TODOS los sufijos numéricos finales de un nombre (por si llegara
// más de uno pegado, ej. "Voe 2 2" desde una caché vieja del backend).
function trueBaseName(name) {
    let b = name || '', prev;
    do { prev = b; b = b.replace(/\s+\d+$/, ''); } while (b !== prev);
    return b;
}
// Asigna la numeración de los badges EN EL FRONT, agrupando por nombre base
// real y contando ocurrencias en el orden en que aparecen. Así el numerito
// siempre queda 1(sin badge), 2, 3... correcto, sin depender de cómo haya
// llegado numerado el nombre crudo desde el backend.
function assignServerBadges(names) {
    const seen = {}, map = {};
    names.forEach(name => {
        const base = trueBaseName(name);
        seen[base] = (seen[base] || 0) + 1;
        map[name] = { base, num: seen[base] > 1 ? String(seen[base]) : null };
    });
    return map;
}
let curBadgeMap = {};
// HTML compacto para el botón de la topbar: ícono/favicon + nombre base
// limpio (el numerito, si corresponde, va como badge sobre el ícono — igual
// que en la lista — en vez de texto tipo "Goodstream 2"). SIEMPRE calculado
// desde curBadgeMap, nunca desde el nombre crudo.
function serverButtonHtml(name, url) {
    const info = curBadgeMap[name] || { base: trueBaseName(name), num: null };
    return serverIconHtml(info.base, url, info.num)
         + `<span class="sbar-label">${esc(capitalize(info.base))}</span>`;
}

// ── Helpers de dropdown ────────────────────────────────────────────────────
function toggleDrop(id, closeOtherId) {
    const list = document.getElementById(id);
    if (!list) return;
    const isOpen = list.classList.contains('open');
    // Cerrar todos primero
    document.querySelectorAll('.drop-list.open').forEach(l => l.classList.remove('open'));
    document.querySelectorAll('.drop-btn.open').forEach(b => b.classList.remove('open'));
    if (!isOpen) {
        list.classList.add('open');
        const btn = list.parentElement && list.parentElement.querySelector('.drop-btn');
        if (btn) btn.classList.add('open');
        // Calcular max-height para que no salga del player (viewport)
        const rect = btn ? btn.getBoundingClientRect() : null;
        if (rect) {
            const avail = window.innerHeight - rect.bottom - 14;
            list.style.maxHeight = Math.max(90, Math.min(avail, 260)) + 'px';
        }
    }
}
function closeDrop(id) {
    const list = document.getElementById(id);
    if (!list) return;
    list.classList.remove('open');
    const btn = list.parentElement && list.parentElement.querySelector('.drop-btn');
    if (btn) btn.classList.remove('open');
}
// Cerrar dropdowns al click afuera
document.addEventListener('click', function(e) {
    if (!e.target.closest('#lbar') && !e.target.closest('#sbar')) {
        document.querySelectorAll('.drop-list.open').forEach(l => l.classList.remove('open'));
        document.querySelectorAll('.drop-btn.open').forEach(b => b.classList.remove('open'));
    }
}, true);

const LANG_META = {
    'latino':      { label:'LATINO', flag:'mx', flagName:'México' },
    'español':     { label:'ESPAÑOL', flag:'es', flagName:'España' },
    'subtitulado': { label:'SUBTITULADO', flag:'us', flagName:'English' },
};

// Regla simple, tal como la pidió el usuario: Direct (Lamovie) siempre
// primero; Remux nunca primero ni último — va en el medio de lo que sea
// que haya; el orden del resto de los servidores no importa. Proxy se
// descarta del todo (no se muestra). Función pura sobre una lista de
// nombres, reusada tanto en la carga inicial (filterEmbeds) como cada vez
// que llegan servidores nuevos después (ver resortServerBar).
function orderNames(names) {
    const usable = names.filter(n => !/proxy/i.test(n));
    const remux  = usable.filter(n => /remux/i.test(n));
    const direct = usable.filter(n => /direct/i.test(n) && !/remux/i.test(n));
    const rest   = usable.filter(n => !/direct|remux/i.test(n));

    const withoutRemux = [...direct, ...rest]; // Direct primero; el resto, en el orden que haya llegado
    if (!remux.length || !withoutRemux.length) return [...withoutRemux, ...remux];
    // Insertamos Remux en el medio — Math.ceil nunca da 0, así que Remux
    // jamás puede terminar en la posición 0 (primero) por más chica que
    // sea la lista de "lo demás".
    const mid = Math.ceil(withoutRemux.length / 2);
    return [...withoutRemux.slice(0, mid), ...remux, ...withoutRemux.slice(mid)];
}

// Filtra y ordena embeds: solo LAT → ESP → SUB, descarta el resto.
function filterEmbeds(raw) {
    const out = {};
    LANG_ORDER.forEach(lang => {
        if (!raw[lang] || !Object.keys(raw[lang]).length) return;
        const srvs = raw[lang];
        const ordered = orderNames(Object.keys(srvs));

        const sorted = {};
        ordered.forEach(n => { sorted[n] = srvs[n]; });
        out[lang] = sorted;
    });
    return out;
}

// Genera un <img> de bandera real usando Flagcdn
function flagImg(code, size=24) {
    return `<img src="https://flagcdn.com/w40/${code}.png" width="${size}" height="${Math.round(size*0.75)}" alt="${code}" style="display:block;">`;
}

let embeds       = filterEmbeds(EMBEDS);
let curLang      = null;
let curSrv       = null;
let noticeClosed = false;
// Cuántas veces se reintentó cargar cada servidor de <video> nativo (Remux,
// Direct). Los cold starts (sobre todo Remux, que arranca ffmpeg al vuelo)
// a veces fallan el primer intento mientras el backend todavía está
// consultando metadata/Mediafire — un reintento silencioso resuelve la
// mayoría de esos casos sin mostrar error al usuario.
const _videoRetryCount = {};

// ── Tutorial de una sola vez ──────────────────────────────────────────────────
const TUT_KEY = 'ulp_tut_done_v1';
let _tutStep = 0;
const _tutSteps = [
    {
        icon: '🌐', title: 'Cambiar Idioma',
        body: 'Elegí el idioma del video: <strong>LATINO</strong>, <strong>ESPAÑOL</strong> o <strong>SUBTITULADO</strong>.',
        anchor: 'lbar', nextLabel: 'Siguiente'
    },
    {
        icon: '🖥️', title: 'Cambiar Servidor',
        body: 'Si un servidor no carga, seleccioná otro. Hay múltiples opciones disponibles.',
        anchor: 'sbar', nextLabel: '¡Entendido!'
    }
];

function tutPositionBubble(anchorId) {
    const ov = document.getElementById('tutorial-overlay');
    const bubble = document.getElementById('tut-bubble');
    const anchor = document.getElementById(anchorId);
    if (!ov || !bubble || !anchor) return;
    const r = anchor.getBoundingClientRect();
    bubble.style.top  = (r.bottom + 8) + 'px';
    bubble.style.left = Math.max(8, r.left) + 'px';
}

function tutShow(step) {
    if (step >= _tutSteps.length) { tutClose(); return; }
    _tutStep = step;
    const s = _tutSteps[step];
    document.getElementById('tut-icon').textContent       = s.icon;
    document.getElementById('tut-title-txt').textContent  = s.title;
    document.getElementById('tut-body').innerHTML         = s.body;
    document.getElementById('tut-next-btn').textContent   = s.nextLabel;
    tutPositionBubble(s.anchor);
    document.getElementById('tutorial-overlay').classList.add('on');
}

function tutNext() { tutShow(_tutStep + 1); }

function tutClose() {
    document.getElementById('tutorial-overlay').classList.remove('on');
    try { localStorage.setItem(TUT_KEY, '1'); } catch(e) {}
}

function maybeShowTutorial() {
    try { if (localStorage.getItem(TUT_KEY)) return; } catch(e) {}
    // Solo mostrar si hay idiomas y servidores disponibles
    setTimeout(() => tutShow(0), 400);
}

// ── Notice dinámico: detecta si el servidor tiene anuncios ───────────────────
function updateNoticeForServer(name, url) {
    const noticeEl  = document.getElementById('notice');
    const msgEl     = document.getElementById('notice-msg');
    const iconEl    = document.getElementById('notice-icon');
    if (!noticeEl || !msgEl) return;

    const isDirectVideo = /\.(mp4|m3u8|webm|ogg|mov|mkv|ts)(\?|$)/i.test(url || '') || /remux\.unlimplay\.com/i.test(url || '');
    const isDirectName  = /direct|proxy|remux/i.test(name || '');
    const isClean       = isDirectVideo || isDirectName;

    const displayName = (name || 'Este servidor').replace(/\s+\d+$/, '');
    const capitalized = displayName.charAt(0).toUpperCase() + displayName.slice(1);

    noticeEl.classList.remove('clean','ads');

    if (isClean) {
        noticeEl.classList.add('clean');
        if(iconEl) iconEl.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#6ee7b7" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`;
        msgEl.innerHTML = `<span style="font-weight:700;color:#6ee7b7">${capitalized}</span> — Sin anuncios ni popups.`;
    } else {
        noticeEl.classList.add('ads');
        if(iconEl) iconEl.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;
        msgEl.innerHTML = `<span style="font-weight:700;color:#fbbf24">${capitalized}</span> — Este servidor tiene ventanas emergentes y anuncios.`;
    }

    if (!noticeClosed && _vastDone) showNoticeWithBar(6000);
}

// ── VAST Config ────────────────────────────────────────────────────
const PREROLL_CONFIG = {
    vastTags: [
        "https://latgw.fun/assets/vendor/573ca43191063e6d27320c17d20d1ac7.xml?v=3.0&external_subid=(host)",
        "https://latgw.fun/assets/vendor/686718f61b8cd1aefb027604ad13d378.xml?v=3.0&external_subid=(host)",
        "https://servetraff.com/ztcuDBsE3-shVl2BKaXkcw7isCdYYurLZaCVL85p22YXdroBqND3lm3V5raE5MyEYsYYYFmQeiN1bfbOZTTPHxsCTc8V5_RY",
    ],
    skipAfter:   3,
    muteOnStart: false,
    corsProxy:   "",
    autoAdvance: true,
};

// ── VAST Engine ──────────────────────────────────────────────────────────────
// Dispara N impresiones con un delay entre cada una (no de golpe)
let _vastStartPreroll    = null;
let _vastPreflightDone   = false;
let _vastDone            = false; // true = todos los anuncios terminaron
let _userPlayed          = false; // true = usuario presionó Play
let _vastDismissCallback = null;  // si está seteado, dismiss() lo llama en lugar de buildPlayer()
let _firstPrerollDone    = false; // true = ya se mostró el preroll inicial de bienvenida

(function initPrerollEngine() {
    const prerollOverlay = document.getElementById('vast-preroll-overlay');
    const player    = document.getElementById('outstream-player');
    const video     = document.getElementById('vast-video');
    const overlay   = document.getElementById('player-overlay');
    const overlayMsg= document.getElementById('overlay-msg');
    const counter   = document.getElementById('ad-counter');
    const progFill  = document.getElementById('progress-fill');
    const timeDisp  = document.getElementById('time-display');
    const btnPlay   = document.getElementById('btn-play');
    const btnMute   = document.getElementById('btn-mute');
    const btnSkip   = document.getElementById('btn-skip');
    const progTrack = document.getElementById('progress-track');
    const iconPlay  = document.getElementById('icon-play');
    const iconPause = document.getElementById('icon-pause');
    const iconMuteOff = document.getElementById('icon-mute-off');
    const iconMuteOn  = document.getElementById('icon-mute-on');
    const vpaidContainer = document.getElementById('vpaid-container');

    let currentIndex  = 0;
    let vastData      = null;
    let isVpaid       = false;
    let skipTimer     = null;
    let impInterval   = null;
    let lastLoadTs    = 0; // marca de tiempo del último video.load(); sirve para ignorar eventos 'ended'/'error' obsoletos de un anuncio ya reemplazado
    let skipRemaining = PREROLL_CONFIG.skipAfter;
    let isMuted       = PREROLL_CONFIG.muteOnStart;
    let hasStarted    = false;
    let _adReady      = false;
    let validatedTags = [];
    let trackFired    = {};
    let imaLoader     = null;
    let imaManager    = null;
    let imaReady      = false;

    function fmtTime(s){ if(!isFinite(s))return'0:00'; return Math.floor(s/60)+':'+(Math.floor(s%60)+'').padStart(2,'0'); }
    function showOverlay(msg){ if(overlayMsg)overlayMsg.textContent=msg; if(overlay)overlay.classList.remove('hidden'); if(counter)counter.classList.remove('show'); }
    function hideOverlay(){ if(overlay)overlay.classList.add('hidden'); if(counter)counter.classList.add('show'); }
    function updatePlayIcon(p){ if(iconPlay)iconPlay.style.display=p?'none':'block'; if(iconPause)iconPause.style.display=p?'block':'none'; }
    function updateMuteIcon(){ if(iconMuteOff)iconMuteOff.style.display=isMuted?'none':'block'; if(iconMuteOn)iconMuteOn.style.display=isMuted?'block':'none'; }
    function fireUrls(urls, useBeacon){
        if(!urls||!urls.length)return;
        urls.forEach(u=>{
            if(!u)return;
            const resolved=u.replace('[TIMESTAMP]',Date.now()).replace('[CACHEBUSTING]',Date.now());
            // sendBeacon solo si se pide explícitamente (para tracking events internos).
            // Para impresiones VAST siempre usamos Image pixel: es el método que los
            // proveedores validan (ServeTraff, Adtelligent, etc.) — sendBeacon puede
            // omitir el Referer o llegar con content-type incorrecto y la impresión
            // no se registra, aunque el click sí (porque viene de navegación real).
            if(useBeacon && navigator.sendBeacon){
                try{navigator.sendBeacon(resolved);return;}catch(e){}
            }
            const img=new Image();
            const t=setTimeout(()=>{img.src='';},8000);
            img.onload=img.onerror=()=>clearTimeout(t);
            img.src=resolved;
        });
    }
    function fireEvent(name){ if(!vastData||!vastData.events||trackFired[name])return; trackFired[name]=true; fireUrls(vastData.events[name]); }
    function fireImpression(){ if(!vastData||trackFired.impression)return; trackFired.impression=true; fireUrls(vastData.impressionUrls); }
    // Abre el clickThrough del VAST en una pestaña nueva (navegación top-level real,
    // el click cuenta igual ante la red de anuncios) pero le devuelve el foco a esta
    // página inmediatamente, así el usuario no "salta" a la pestaña del anunciante.
    // OJO: esto NO es un popunder clásico (no reordena ventanas del SO) — es lo máximo
    // que los navegadores modernos permiten hoy sin tratarlo como popup abusivo, y solo
    // funciona si se llama de forma sincrónica dentro del mismo gesto de click.
    function openClickThroughBackground(url){
        window._vastClickThrough = true;
        const win = window.open(url, '_blank');
        if (win) { try { win.blur(); } catch(e) {} }
        window.focus();
    }

    async function fetchVAST(url){
        const u=PREROLL_CONFIG.corsProxy?PREROLL_CONFIG.corsProxy+encodeURIComponent(url):url;
        const r=await fetch(u,{cache:'no-store'}); if(!r.ok)throw new Error('HTTP '+r.status);
        return new DOMParser().parseFromString(await r.text(),'text/xml');
    }
    function parseVAST(doc){
        const wrapper=doc.querySelector('Wrapper,wrapper');
        if(wrapper){const uri=wrapper.querySelector('VASTAdTagURI,vastAdTagURI');if(uri)return{wrapperUrl:uri.textContent.trim()};}
        const mediaFiles=[...doc.querySelectorAll('MediaFile')];
        const vpaidFile=mediaFiles.find(m=>(m.getAttribute('apiFramework')||'').toUpperCase()==='VPAID');
        const impressionUrls=[...doc.querySelectorAll('Impression')].map(i=>i.textContent.trim()).filter(Boolean);
        const clickThrough=doc.querySelector('ClickThrough, VideoClicks ClickThrough');
        const clickTracking=[...doc.querySelectorAll('ClickTracking')].map(c=>c.textContent.trim());
        const events={};
        doc.querySelectorAll('Tracking').forEach(t=>{const ev=t.getAttribute('event');if(ev){if(!events[ev])events[ev]=[];events[ev].push(t.textContent.trim());}});
        const durNode=doc.querySelector('Duration');let duration=0;
        if(durNode){const p=durNode.textContent.trim().split(':');duration=parseInt(p[0])*3600+parseInt(p[1])*60+parseFloat(p[2]);}
        if(vpaidFile)return{isVpaid:true,vpaidUrl:vpaidFile.textContent.replace(/\s/g,''),
            adParameters:(doc.querySelector('AdParameters')||{textContent:''}).textContent.trim(),
            impressionUrls,clickThrough:clickThrough?clickThrough.textContent.trim():null,clickTracking,events,duration};
        const mp4s=mediaFiles.filter(m=>(m.getAttribute('type')||'').toLowerCase().includes('mp4'));
        const chosen=mp4s.sort((a,b)=>parseInt(b.getAttribute('bitrate')||0)-parseInt(a.getAttribute('bitrate')||0))[0]||mediaFiles[0];
        if(!chosen)throw new Error('No MediaFile');
        return{mediaUrl:chosen.textContent.trim(),mediaType:chosen.getAttribute('type')||'video/mp4',
            events,impressionUrls,clickThrough:clickThrough?clickThrough.textContent.trim():null,clickTracking,duration};
    }
    async function resolveVAST(url,depth=0){
        if(depth>3)throw new Error('Wrapper depth exceeded');
        const parsed=parseVAST(await fetchVAST(url));
        return parsed.wrapperUrl?resolveVAST(parsed.wrapperUrl,depth+1):parsed;
    }

    function startSkipTimer(){
        if(PREROLL_CONFIG.skipAfter<=0)return;
        if(isVpaid)return; // VPAID: IMA maneja su propio skip internamente, nuestro botón no aplica
        clearInterval(skipTimer);
        skipTimer=null;
        if(btnSkip){
            btnSkip.innerHTML='Saltar anuncio en <span id="skip-count">'+PREROLL_CONFIG.skipAfter+'</span>s';
            btnSkip.style.display='block';
            btnSkip.classList.remove('ready');
            delete btnSkip._readyAt;
        }
        let remaining=PREROLL_CONFIG.skipAfter;
        skipRemaining=remaining;
        const myTimer=setInterval(()=>{
            if(skipTimer!==myTimer){clearInterval(myTimer);return;}
            remaining--;
            skipRemaining=remaining;
            const countEl=btnSkip?btnSkip.querySelector('#skip-count'):null;
            if(countEl)countEl.textContent=remaining;
            if(remaining<=0){
                clearInterval(myTimer);
                skipTimer=null;
                if(btnSkip){btnSkip.innerHTML='Saltar anuncio \u203A';btnSkip.classList.add('ready');btnSkip._readyAt=Date.now();}
            }
        },1000);
        skipTimer=myTimer;
    }
    function updateUI(){
        const dur=video.duration||vastData?.duration||0,cur=video.currentTime||0;
        if(progFill)progFill.style.width=(dur>0?(cur/dur)*100:0)+'%';
        if(timeDisp)timeDisp.textContent=fmtTime(cur)+' / '+fmtTime(dur);
        // Cuartiles: solo si _adReady (metadata cargada) y no disparados aún
        if(_adReady&&dur>0){
            if(!trackFired.firstQuartile&&cur>=dur*.25)fireEvent('firstQuartile');
            if(!trackFired.midpoint&&cur>=dur*.5)fireEvent('midpoint');
            if(!trackFired.thirdQuartile&&cur>=dur*.75)fireEvent('thirdQuartile');
        }
    }
    function dismiss(){
        if(isVpaid)vpaidCleanup();
        video.pause(); clearInterval(skipTimer); clearTimeout(impInterval); impInterval=null;
        if(prerollOverlay)prerollOverlay.style.display='none';
        _vastDone=true;

        // ── Primer preroll (antes del intro) ────────────────────────────────
        // Al terminar el preroll inicial mostramos el intro con "VERIFICANDO..."
        // y no construimos el player todavía — eso ocurre cuando el usuario
        // da click en Play (que lanza otro preroll antes de buildPlayer).
        if(!_firstPrerollDone){
            _firstPrerollDone = true;
            const intro = document.getElementById('intro');
            if(intro) intro.classList.add('ready');
            // Si finalizePlayer() ya corrió mientras el anuncio estaba en pantalla,
            // aplicar ahora la UI del botón (ONLINE/OFFLINE/VERIFICANDO).
            if(_pendingFinalizeUI) { _pendingFinalizeUI = false; _applyFinalizeUI(); }
            return;
        }
        // ────────────────────────────────────────────────────────────────────

        _showPlayerAfterAd();
        if(_vastDismissCallback) {
            const cb = _vastDismissCallback;
            _vastDismissCallback = null;
            cb();
        } else if(embeds && Object.keys(embeds).length){
            buildPlayer();
            if(!noticeClosed) setTimeout(()=>updateNoticeForServer(curSrv, embeds[curLang]?.[curSrv]),700);
        } else {
            // embeds todavía no cargaron (PHP sigue procesando) — esperar a finalizePlayer
            (function waitEmbeds(){
                if(embeds && Object.keys(embeds).length){ buildPlayer(); if(!noticeClosed) setTimeout(()=>updateNoticeForServer(curSrv, embeds[curLang]?.[curSrv]),700); }
                else setTimeout(waitEmbeds, 100);
            })();
        }
        const frame=document.getElementById('iarea')?.querySelector('.frm.fv');
        if(frame){try{const v=(frame.contentDocument||frame.contentWindow.document).querySelector('video');if(v&&v.paused)v.play().catch(()=>{});}catch(e){}}
    }
    let _vpaidDismissed=false; // guard contra doble dismiss CONTENT_RESUME + ALL_ADS_COMPLETED
    function vpaidCleanup(){
        try{if(imaManager)imaManager.destroy();}catch(e){}
        imaManager=null;isVpaid=false;
        if(vpaidContainer){vpaidContainer.style.display='none';vpaidContainer.innerHTML='';}
        // No restaurar el display del <video> aquí: para VPAID no tiene src,
        // y hacerlo causa un flash de pantalla negra innecesario.
        // Se restaura solo si hay un anuncio VAST normal en la cola siguiente.
    }
    function loadImaSDK(){
        return new Promise((res,rej)=>{
            if(imaReady){res();return;}
            const ex=document.getElementById('_ima_sdk');if(ex){ex.addEventListener('load',()=>{imaReady=true;res();});ex.addEventListener('error',rej);return;}
            const s=document.createElement('script');s.id='_ima_sdk';s.src='https://imasdk.googleapis.com/js/sdkloader/ima3.js';
            s.onload=()=>{imaReady=true;res();};s.onerror=()=>rej(new Error('IMA SDK no cargó'));document.head.appendChild(s);
        });
    }
    async function loadVpaidAd(vd){
        // Detener COMPLETAMENTE el video del anuncio anterior antes de arrancar el VPAID.
        // solo pause()+src='' no alcanza: el browser puede seguir decodificando en bg
        // y disparar 'ended' tardíamente, lo que cerraría el overlay del VPAID.
        // El trío pause + removeAttribute('src') + load() cancela cualquier descarga pendiente.
        try { video.pause(); } catch(e) {}
        video.removeAttribute('src');
        try { video.load(); } catch(e) {}
        // Avanzar lastLoadTs para que cualquier evento 'ended'/'error' obsoleto del
        // anuncio anterior quede bloqueado por el guard e.timeStamp < lastLoadTs.
        lastLoadTs = performance.now();
        isVpaid=true;video.style.display='none';
        const w=window.innerWidth||screen.width||640;
        const h=window.innerHeight||screen.height||360;
        if(vpaidContainer){
            vpaidContainer.style.display='block';
            vpaidContainer.innerHTML='';
        }        await loadImaSDK();
        const google=window.google;if(!google||!google.ima)throw new Error('google.ima no disponible');
        google.ima.settings.setDisableCustomPlaybackForIOS10Plus(true);
        google.ima.settings.setVpaidMode(google.ima.ImaSdkSettings.VpaidMode.ENABLED);
        if(imaLoader){try{imaLoader.destroy();}catch(e){}}
        // AdDisplayContainer debe crearse con el elemento <video> como segundo arg
        // para que IMA pueda controlar el contenido de fondo durante el VPAID.
        const adc=new google.ima.AdDisplayContainer(vpaidContainer,video);
        adc.initialize();
        imaLoader=new google.ima.AdsLoader(adc);
        imaLoader.addEventListener(google.ima.AdsManagerLoadedEvent.Type.ADS_MANAGER_LOADED,(e)=>onImaLoaded(e,vd,adc),false);
        imaLoader.addEventListener(google.ima.AdErrorEvent.Type.AD_ERROR,(err)=>{
            console.warn('[VPAID] Loader AdError:', err.getError&&err.getError().toString());
            vpaidCleanup();PREROLL_CONFIG.autoAdvance?loadAdFromQueue(currentIndex+1):dismiss();
        },false);
        const req=new google.ima.AdsRequest();
        // Para VPAID hay que pasar la URL del tag VAST original (no la del MediaFile):
        // IMA resuelve el wrapper internamente y extrae el VPAID JS por su cuenta.
        req.adTagUrl=validatedTags[currentIndex].url;
        req.linearAdSlotWidth=w;req.linearAdSlotHeight=h;
        req.setAdWillAutoPlay(true);req.setAdWillPlayMuted(isMuted);
        imaLoader.requestAds(req);
    }
    function onImaLoaded(e,vd,adc){
        const google=window.google;
        // Para VPAID, IMA necesita el elemento <video> como contenido de referencia,
        // no el AdDisplayContainer. Pasarlo como segundo arg de getAdsManager es
        // lo que permite que el SDK inyecte y controle correctamente el ad unit VPAID.
        imaManager=e.getAdsManager(video);
        imaManager.addEventListener(google.ima.AdErrorEvent.Type.AD_ERROR,(err)=>{
            console.warn('[VPAID] AdError:', err.getError&&err.getError().toString());
            vpaidCleanup();PREROLL_CONFIG.autoAdvance?loadAdFromQueue(currentIndex+1):dismiss();
        });
        imaManager.addEventListener(google.ima.AdEvent.Type.LOADED,()=>hideOverlay());
        // VPAID: el IMA SDK dispara sus propias impresiones internamente.
        // Solo disparamos los tracking events del XML que IMA no cubre (complete, skip).
        // NO llamamos fireImpression() aquí para evitar doble conteo.
        imaManager.addEventListener(google.ima.AdEvent.Type.STARTED,()=>{updatePlayIcon(true);startSkipTimer();});
        // CONTENT_RESUME_REQUESTED: el VPAID terminó su ejecución (el ad unit llamó
        // AdStopped / AdSkipped internamente). Es la señal de fin más confiable para VPAID.
        // ALL_ADS_COMPLETED: IMA confirma que no quedan más ads en la queue.
        // Ambos pueden dispararse casi simultáneamente — el guard _vpaidDismissed evita
        // que el avance a la siguiente cola se ejecute dos veces.
        _vpaidDismissed=false;
        const onVpaidEnd=()=>{
            if(_vpaidDismissed)return; _vpaidDismissed=true;
            fireEvent('complete');vpaidCleanup();clearInterval(skipTimer);updatePlayIcon(false);
            if(PREROLL_CONFIG.autoAdvance)setTimeout(()=>loadAdFromQueue(currentIndex+1),600);
            else dismiss();
        };
        imaManager.addEventListener(google.ima.AdEvent.Type.CONTENT_RESUME_REQUESTED,onVpaidEnd);
        imaManager.addEventListener(google.ima.AdEvent.Type.ALL_ADS_COMPLETED,onVpaidEnd);
        imaManager.addEventListener(google.ima.AdEvent.Type.SKIPPED,()=>{fireEvent('skip');vpaidCleanup();clearInterval(skipTimer);PREROLL_CONFIG.autoAdvance?loadAdFromQueue(currentIndex+1):dismiss();});
        try{const w=window.innerWidth||640,h=window.innerHeight||360;imaManager.init(w,h,google.ima.ViewMode.NORMAL);imaManager.setVolume(isMuted?0:1);imaManager.start();}
        catch(initErr){console.warn('[VPAID] init error:',initErr);vpaidCleanup();PREROLL_CONFIG.autoAdvance?loadAdFromQueue(currentIndex+1):dismiss();}

        // Redimensionar IMA cuando el usuario entra/sale de pantalla completa
        const onFsChange=()=>{
            if(!imaManager||!isVpaid)return;
            const fs=!!(document.fullscreenElement||document.webkitFullscreenElement);
            const nw=fs?window.innerWidth:(player?player.offsetWidth||640:640);
            const nh=fs?window.innerHeight:Math.round(nw*9/16);
            if(vpaidContainer){vpaidContainer.style.width=nw+'px';vpaidContainer.style.height=nh+'px';}
            try{imaManager.resize(nw,nh,fs?google.ima.ViewMode.FULLSCREEN:google.ima.ViewMode.NORMAL);}catch(e){}
        };
        document.addEventListener('fullscreenchange',onFsChange);
        document.addEventListener('webkitfullscreenchange',onFsChange);
        // Limpiar listeners al terminar el VPAID
        const _origCleanup=vpaidCleanup;
        vpaidCleanup=function(){
            document.removeEventListener('fullscreenchange',onFsChange);
            document.removeEventListener('webkitfullscreenchange',onFsChange);
            vpaidCleanup=_origCleanup;
            _origCleanup();
        };
    }

    async function preflightVAST(url){try{const d=await resolveVAST(url);if(d.mediaUrl||d.isVpaid)return{url,data:d};}catch(e){}return null;}
    async function runPreflight(){const results=await Promise.all(PREROLL_CONFIG.vastTags.map(u=>preflightVAST(u)));validatedTags=results.filter(Boolean);}

    function startPlayback(){
        // Impresión ya se disparó en loadedmetadata (ver listener abajo)
        video.play()
            .then(()=>{hideOverlay();updatePlayIcon(true);startSkipTimer();})
            .catch(()=>{
                // Autoplay bloqueado con sonido → reintentar muteado (Chrome móvil)
                video.muted=true;
                isMuted=true;
                updateMuteIcon();
                video.play()
                    .then(()=>{hideOverlay();updatePlayIcon(true);startSkipTimer();})
                    .catch(()=>{
                        // Autoplay bloqueado incluso muteado → mostrar botón play
                        hideOverlay();
                        updatePlayIcon(false);
                        showOverlay('Toca para reproducir');
                        if(overlay) overlay.style.cursor='pointer';
                        const playOnClick=()=>{
                            overlay.style.cursor='';
                            hideOverlay();
                            video.play().then(()=>{updatePlayIcon(true);startSkipTimer();}).catch(()=>{});
                            overlay.removeEventListener('click',playOnClick);
                        };
                        if(overlay) overlay.addEventListener('click',playOnClick);
                    });
            });
    }
    async function loadAdFromQueue(index){
        if(index>=validatedTags.length){dismiss();return;}
        currentIndex=index;
        if(counter)counter.textContent='Anuncio '+(index+1)+' de '+validatedTags.length;
        vastData=null;trackFired={};hasStarted=false;_adReady=false;
        if(progFill)progFill.style.width='0%';
        if(btnSkip){btnSkip.style.display='none';btnSkip.classList.remove('ready');}
        clearInterval(skipTimer);
        vastData=validatedTags[index].data;
        if(vastData.isVpaid){
            _adReady=true; // VPAID maneja su propia duración vía IMA
            showOverlay('Espere, verificando anuncio...');
            loadVpaidAd(vastData).catch(e=>{vpaidCleanup();PREROLL_CONFIG.autoAdvance?setTimeout(()=>loadAdFromQueue(index+1),500):dismiss();});
        } else {
            vpaidCleanup();
            video.style.display='block'; // restaurar tras posible VPAID previo
            video.src=vastData.mediaUrl;
            video.muted=isMuted;
            lastLoadTs=performance.now();
            video.load();
            // _adReady se activa en loadedmetadata, cuando duration ya es del nuevo anuncio
        }
    }

    // Eventos del video
    video.addEventListener('loadedmetadata',()=>{
        _adReady=true;                // duration ya es válida para este anuncio
        fireImpression();             // IAB: impresión al primer frame visible (metadata lista)
    });
    video.addEventListener('canplay',()=>{
        if(!hasStarted){hasStarted=true;startPlayback();}
    });
    video.addEventListener('timeupdate',()=>{
        if(!_adReady)return;          // ignorar hasta que metadata esté lista
        updateUI();
        if(!trackFired.start&&video.currentTime>0.1)fireEvent('start');
    });
    video.addEventListener('ended',(e)=>{
        if(!_adReady)return;
        if(e.timeStamp<lastLoadTs)return; // evento obsoleto de un anuncio ya reemplazado, ignorar
        _adReady=false;
        clearTimeout(impInterval); impInterval=null;
        fireEvent('complete');
        clearInterval(skipTimer);
        if(btnSkip){btnSkip.style.display='none';btnSkip.classList.remove('ready');} // ocultar YA, no esperar el setTimeout de abajo
        updatePlayIcon(false);
        if(PREROLL_CONFIG.autoAdvance)setTimeout(()=>loadAdFromQueue(currentIndex+1),600);
    });
    video.addEventListener('play',()=>updatePlayIcon(true));
    video.addEventListener('pause',()=>updatePlayIcon(false));
    video.addEventListener('error',(e)=>{
        if(!_adReady)return;
        if(e.timeStamp<lastLoadTs)return; // evento obsoleto de un anuncio ya reemplazado, ignorar
        _adReady=false;
        clearTimeout(impInterval); impInterval=null;
        clearInterval(skipTimer);
        if(btnSkip){btnSkip.style.display='none';btnSkip.classList.remove('ready');}
        showOverlay('Error al cargar el video');
        setTimeout(()=>{PREROLL_CONFIG.autoAdvance?loadAdFromQueue(currentIndex+1):dismiss();},2000);
    });
    video.addEventListener('click',e=>{e.stopPropagation();if(vastData?.clickThrough){fireUrls(vastData.clickTracking);openClickThroughBackground(vastData.clickThrough);}});
    if(btnPlay)btnPlay.addEventListener('click',()=>{if(isVpaid){try{video.paused?imaManager.resume():imaManager.pause();}catch(e){}return;}if(video.paused){video.play();fireEvent('resume');}else{video.pause();fireEvent('pause');}});
    if(btnMute)btnMute.addEventListener('click',()=>{isMuted=!isMuted;if(isVpaid){try{imaManager.setVolume(isMuted?0:1);}catch(e){}}else video.muted=isMuted;updateMuteIcon();fireEvent(isMuted?'mute':'unmute');});
    if(btnSkip)btnSkip.addEventListener('click',()=>{
        if(!btnSkip.classList.contains('ready'))return;
        if(!_adReady)return; // el anuncio ya terminó/está cambiando, evita disparar un salto duplicado
        clearTimeout(impInterval); impInterval=null;
        fireEvent('skip');
        clearInterval(skipTimer);
        if(isVpaid){try{imaManager.skip();}catch(e){}return;}
        PREROLL_CONFIG.autoAdvance?loadAdFromQueue(currentIndex+1):dismiss();
    });
    if(progTrack)progTrack.addEventListener('click',e=>{const r=e.currentTarget.getBoundingClientRect();if(video.duration)video.currentTime=((e.clientX-r.left)/r.width)*video.duration;});

    // Exponer _vastStartPreroll — se llama DIRECTO dentro del click (sin setTimeout)
    _vastStartPreroll = async function(){
        _vastDone=false;
        if(prerollOverlay)prerollOverlay.style.display='block';
        showOverlay('Espere, verificando anuncio...');
        updateMuteIcon();
        hasStarted=false;currentIndex=0;vastData=null;trackFired={};_adReady=false;
        if(!_vastPreflightDone) await runPreflight();
        if(validatedTags.length===0){ dismiss(); return; }
        loadAdFromQueue(0);
    };

    // Warmup: solo pre-validar los tags VAST anticipadamente (sin lanzar el anuncio).
    // El anuncio arranca únicamente cuando el usuario presiona Play.
    runPreflight().then(()=>{
        _vastPreflightDone=true;
    }).catch(()=>{
        _vastPreflightDone=true;
        _vastDone=true;
    });

    // bfcache: cuando el usuario navega atrás/adelante el browser restaura la
    // página desde memoria (persisted=true). Las URLs de mediafile de los VAST
    // normales ya expiraron. Forzar re-preflight completo.
    window.addEventListener('pageshow', (e) => {
        if (!e.persisted) return;
        validatedTags = [];
        _vastPreflightDone = false;
        runPreflight().then(() => { _vastPreflightDone = true; }).catch(() => { _vastPreflightDone = true; });
    });

})(); // fin initPrerollEngine

// ── Autoplay preroll siempre ─────────────────────────────────────────────────
// Sin preroll automático: mostramos el intro directamente y marcamos
// _firstPrerollDone=true para que dismiss() nunca intente volver al intro.
_firstPrerollDone = true;
_vastDone         = true;
(function showIntroDirectly() {
    const intro = document.getElementById('intro');
    if (intro) {
        intro.classList.add('ready');
    } else {
        // intro aún no está en el DOM (script se parseó antes del flush), reintentar
        setTimeout(showIntroDirectly, 30);
    }
})();

// ── Fake verificador animado ──────────────────────────────────────────────────
// Muestra mensajes progresivos y una barra de progreso falsa mientras PHP
// termina de procesar los servidores en segundo plano. Se oculta automáticamente
// cuando finalizePlayer() llega con los datos reales.
(function initFakeVerifier() {
    const TOTAL_SERVERS = 9;
    const MESSAGES = [
        'Espere, verificando servidores…',
        'Conectando con fuentes de video…',
        'Comprobando disponibilidad de servidores…',
        'Analizando calidad de transmisión…',
        'Verificando idiomas disponibles…',
        'Casi listo, finalizando verificación…',
    ];
    // Tiempos aproximados de progreso (ms) — van de 0% a 90% antes de que llegue la respuesta real
    const STEPS = [
        { at: 0,    pct: 3,  msgIdx: 0 },
        { at: 600,  pct: 18, msgIdx: 1 },
        { at: 1400, pct: 35, msgIdx: 2 },
        { at: 2400, pct: 52, msgIdx: 3 },
        { at: 3600, pct: 68, msgIdx: 4 },
        { at: 5000, pct: 82, msgIdx: 5 },
        { at: 7000, pct: 90, msgIdx: 5 },
    ];

    const fv     = document.getElementById('fake-verifier');
    const fill   = document.getElementById('fv-bar-fill');
    const msgEl  = document.getElementById('fv-msg');
    const cntEl  = document.getElementById('fv-counter');
    if (!fv || !fill || !msgEl) return;

    let _done      = false;
    let _timers    = [];
    let _fakeCount = 0;

    function setMsg(txt) {
        msgEl.classList.add('fade');
        setTimeout(() => {
            msgEl.textContent = txt;
            msgEl.classList.remove('fade');
        }, 220);
    }

    function setPct(p) {
        fill.style.width = p + '%';
    }

    function animateFakeCount(target) {
        if (_fakeCount >= target) return;
        const step = () => {
            if (_fakeCount < target) {
                _fakeCount++;
                if (cntEl) cntEl.textContent = _fakeCount + ' / ' + TOTAL_SERVERS;
                if (_fakeCount < target) setTimeout(step, 180 + Math.random() * 220);
            }
        };
        setTimeout(step, 150);
    }

    STEPS.forEach(s => {
        const t = setTimeout(() => {
            if (_done) return;
            setPct(s.pct);
            setMsg(MESSAGES[s.msgIdx]);
            // Simular conteo progresivo de servidores verificados
            const fakeTarget = Math.round((s.pct / 90) * TOTAL_SERVERS * 0.85);
            animateFakeCount(fakeTarget);
        }, s.at);
        _timers.push(t);
    });

    // Exponer función para que finalizePlayer() la llame
    window._hideFakeVerifier = function(found, total) {
        if (_done) return;
        _done = true;
        _timers.forEach(t => clearTimeout(t));

        // Completar barra y mostrar resultado
        if (cntEl) cntEl.textContent = total + ' / ' + TOTAL_SERVERS;
        setPct(100);
        setMsg(found > 0
            ? '✓ Servidores verificados — ' + found + ' disponible' + (found !== 1 ? 's' : '')
            : '✗ Sin servidores disponibles por el momento');
        if (fill) {
            fill.style.background = found > 0
                ? 'linear-gradient(90deg,#22c55e,#4ade80)'
                : 'linear-gradient(90deg,#ef4444,#f87171)';
            fill.style.boxShadow = found > 0
                ? '0 0 8px rgba(34,197,94,.5)'
                : '0 0 8px rgba(239,68,68,.4)';
        }
        const spinner = document.getElementById('fv-spinner');
        if (spinner) spinner.style.display = 'none';
        // Ocultar el verificador con un pequeño delay para que el usuario vea el resultado
        setTimeout(() => {
            if (fv) fv.classList.add('done');
        }, 1400);
    };
})();

window.addEventListener('DOMContentLoaded', ()=>{
    injectFlags();
});

function injectFlags() {
    const slot = document.getElementById('c-flags');
    if (!slot) return;
    slot.style.display = 'inline-flex';
    slot.style.alignItems = 'center';
    slot.style.gap = '4px';
    // Solo LAT→ESP→SUB en orden, sin duplicados
    LANG_ORDER.forEach(lang => {
        if (!embeds[lang]) return;
        if (slot.querySelector(`[data-lang="${lang}"]`)) return;
        const m = LANG_META[lang];
        const s = document.createElement('span');
        s.className = 'iflag';
        s.dataset.lang = lang;
        s.innerHTML = flagImg(m.flag, 22);
        s.title = m.flagName;
        slot.appendChild(s);
    });
}



// ── Launch ────────────────────────────────────────────────────
function launch() {
    if (!embeds || !Object.keys(embeds).length) { q('nocont').classList.add('on'); return; }

    _userPlayed = true;

    if(_vastStartPreroll) {
        // Lanzar preroll; dismiss() ocultará el intro y mostrará el player al terminar
        _vastDone = false;
        _vastStartPreroll();
    } else {
        // initPrerollEngine no corrió → transición directa al player
        _showPlayerAfterAd();
    }
}

function _showPlayerAfterAd() {
    const intro  = document.getElementById('intro');
    const bd     = document.getElementById('bd');
    const player = document.getElementById('player');
    if(intro) intro.classList.add('out');
    if(bd)    bd.classList.add('play-mode');
    player.style.display    = 'flex';
    player.style.opacity    = '0';
    player.style.transition = 'opacity .4s ease';
    setTimeout(() => { player.style.opacity = '1'; }, 20);
}

// ── Fullscreen delegation: relay iframe fullscreen requests al parent ──────────
(function(){
    // Algunos players (GDPlayer, JWPlayer, etc.) llaman requestFullscreen()
    // desde dentro del iframe. Si el browser lo bloquea, lo capturamos via
    // postMessage y hacemos fullscreen en el iframe element desde el parent.
    window.addEventListener('message', function(e) {
        if (!e.data) return;
        const msg = typeof e.data === 'string' ? e.data : JSON.stringify(e.data);
        if (/fullscreen|full_screen|fullScreen/i.test(msg)) {
            const frame = q('iarea') && q('iarea').querySelector('.frm.fv');
            if (frame) {
                const el = frame.requestFullscreen || frame.webkitRequestFullscreen || frame.mozRequestFullScreen || frame.msRequestFullscreen;
                if (el) el.call(frame);
            }
        }
    });

    // También: si el usuario hace doble-tap/click en el iarea y no hay fullscreen,
    // entra fullscreen en el iframe activo
    document.addEventListener('dblclick', function(e) {
        if (!e.target.closest || !e.target.closest('#iarea')) return;
        const frame = q('iarea') && q('iarea').querySelector('.frm.fv');
        if (!frame) return;
        if (!document.fullscreenElement) {
            (frame.requestFullscreen || frame.webkitRequestFullscreen || frame.mozRequestFullScreen || frame.msRequestFullscreen || function(){}).call(frame);
        } else {
            (document.exitFullscreen || document.webkitExitFullscreen || document.mozCancelFullScreen || document.msExitFullscreen || function(){}).call(document);
        }
    });
})();
window.addEventListener('pageshow', function(e) {
    if (!e.persisted) return;
    window.location.reload();
});

// ── Construir player ──────────────────────────────────────────
function buildPlayer() {
    const langs = LANG_ORDER.filter(l => embeds[l]);
    curLang = langs[0] || null;
    if (!curLang) { q('nocont').classList.add('on'); return; }

    // ── Dropdown de idioma en #lbar ──
    const lb = q('lbar'); lb.innerHTML = '';

    const lBtn = document.createElement('button');
    lBtn.className = 'drop-btn';
    lBtn.id = 'lbar-btn';
    const cm = LANG_META[curLang];
    lBtn.innerHTML = `<span class="df">${flagImg(cm.flag, 20)}</span>${esc(cm.label)}<span class="drop-arrow">▼</span>`;
    lBtn.onclick = () => toggleDrop('lbar-list', 'sbar-list');
    lb.appendChild(lBtn);

    const lList = document.createElement('div');
    lList.className = 'drop-list';
    lList.id = 'lbar-list';
    langs.forEach(lang => {
        const m = LANG_META[lang];
        const item = document.createElement('div');
        item.className = 'drop-item' + (lang === curLang ? ' act' : '');
        item.dataset.lang = lang;
        item.innerHTML = `<span class="di-flag">${flagImg(m.flag, 20)}</span>${esc(m.label)}`;
        item.onclick = () => { closeDrop('lbar-list'); switchLang(lang); };
        lList.appendChild(item);
    });
    lb.appendChild(lList);

    buildServerBar(curLang);
    maybeShowTutorial();
}

function stopAllMedia() {
    q('iarea').querySelectorAll('.frm').forEach(f => {
        try { ['pause', JSON.stringify({event:'pause'}), JSON.stringify({type:'pause'})].forEach(m => { try { f.contentWindow.postMessage(m, '*'); } catch(_){} }); } catch(_) {}
        f.src = 'about:blank';
        f.remove();
    });
    q('iarea').querySelectorAll('.frm-video').forEach(f => {
        const vid = f.querySelector('video');
        if (vid) { vid.pause(); vid.src = ''; vid.load(); }
        f.remove();
    });
}

// Crea el <div class="drop-item"> del dropdown de servidores para UN servidor.
// Compartido entre buildServerBar() (carga inicial) y appendServers()
// (servidores que llegan después, en la Fase 2 del backend).
function createServerDropItem(name, url, base, num) {
    const item = document.createElement('div');
    item.className = 'drop-item';
    item.dataset.srv = name;
    item.innerHTML = `${serverIconHtml(base, url, num)}`
        + `<span class="di-label">${esc(capitalize(base))}</span>`
        + `<span class="sdot"></span>`;
    item.onclick = () => { closeDrop('sbar-list'); loadEmbed(name, url); };
    return item;
}
// Crea el <iframe> o <video> (según el tipo de URL) para UN servidor.
// Mismo motivo de compartirlo que createServerDropItem() arriba.
function createServerFrame(name, url) {
    const isDirectVideo = /\.(mp4|m3u8|webm|ogg|mov|mkv|ts)(\?|$)/i.test(url) || /remux\.unlimplay\.com/i.test(url || '');
    if (isDirectVideo) {
        const wrap = document.createElement('div');
        wrap.className = 'frm-video'; wrap.id = 'f_' + name;
        const vid = document.createElement('video');
        vid.controls = true; vid.playsinline = true; vid.preload = 'metadata';
        vid.setAttribute('controlslist', 'nodownload');
        wrap.appendChild(vid);
        return wrap;
    }
    const f = document.createElement('iframe');
    f.className = 'frm'; f.id = 'f_' + name;
    f.allowFullscreen = true;
    f.setAttribute('allowfullscreen', '');
    f.setAttribute('allow', 'autoplay *; fullscreen *; encrypted-media *; picture-in-picture *; web-share *');
    f.setAttribute('referrerpolicy','no-referrer');
    return f;
}

function buildServerBar(lang) {
    const sb = q('sbar'); sb.innerHTML = '';
    stopAllMedia();
    q('epanel').classList.remove('on');
    q('nocont').classList.remove('on');

    const srvs  = embeds[lang] || {};
    const names = Object.keys(srvs);
    if (!names.length) { q('nocont').classList.add('on'); return; }

    curSrv = names[0];
    curBadgeMap = assignServerBadges(names);

    // ── Dropdown de servidor en #sbar ──
    const sBtn = document.createElement('button');
    sBtn.className = 'drop-btn';
    sBtn.id = 'sbar-btn';
    sBtn.innerHTML = `${serverButtonHtml(curSrv, srvs[curSrv])}<span class="sdot"></span><span class="drop-arrow">▼</span>`;
    sBtn.onclick = () => toggleDrop('sbar-list', 'lbar-list');
    sb.appendChild(sBtn);

    const sList = document.createElement('div');
    sList.className = 'drop-list';
    sList.id = 'sbar-list';
    names.forEach((name, i) => {
        const { base, num } = curBadgeMap[name];
        const item = createServerDropItem(name, srvs[name], base, num);
        if (i === 0) item.classList.add('act');
        sList.appendChild(item);
        q('iarea').insertBefore(createServerFrame(name, srvs[name]), q('epanel'));
    });
    sb.appendChild(sList);

    loadEmbed(curSrv, srvs[curSrv]);
}

// Se llama UNA sola vez, cuando el backend termina de intentar con TODOS
// los servidores (Fase 1 + Fase 2 completas), pasándole el resultado FINAL
// completo directamente — recién ahí se revela el botón de play real y el
// contador/banderas finales. Antes de eso, todo queda tapado por el estado
// "VERIFICANDO..." (ver el HTML inicial). El player en sí (dropdown de
// servidores, iframes, reproducción) se sigue armando recién cuando el
// usuario toca play → launch() → buildPlayer(), exactamente como
// funcionaba antes de la carga progresiva.
let _pendingFinalizeUI = false;

function finalizePlayer(finalData) {
    embeds = filterEmbeds(finalData || {});
    const total = LANG_ORDER.reduce((a, l) => a + (embeds[l] ? Object.keys(embeds[l]).length : 0), 0);
    if (typeof window._hideFakeVerifier === 'function') {
        window._hideFakeVerifier(total, total);
    }
    _applyFinalizeUI();
}

function _applyFinalizeUI() {
    const total = LANG_ORDER.reduce((a, l) => a + (embeds[l] ? Object.keys(embeds[l]).length : 0), 0);

    const countEl = document.getElementById('c-count');
    if (countEl) countEl.textContent = `• ${total} Servidor${total !== 1 ? 'es' : ''}`;

    const buff = document.getElementById('c-buff');
    if (buff) buff.style.display = 'none';

    const btn    = document.getElementById('pbtn');
    const dot    = document.querySelector('.odot');
    const status = document.getElementById('c-status');

    if (total > 0) {
        if (btn) {
            btn.classList.remove('offline', 'pending');
            btn.removeAttribute('aria-disabled');
            btn.removeAttribute('title');
            btn.onclick = () => launch();
        }
        if (dot) dot.classList.remove('offline', 'pending');
        if (status) { status.classList.remove('offline', 'pending'); status.textContent = 'ONLINE'; }
        injectFlags();

    } else {
        if (btn) {
            btn.classList.remove('pending'); btn.classList.add('offline');
            btn.setAttribute('aria-disabled', 'true');
            btn.setAttribute('title', 'Sin servidores disponibles');
            btn.onclick = null;
        }
        if (dot) { dot.classList.remove('pending'); dot.classList.add('offline'); }
        if (status) { status.classList.remove('pending'); status.classList.add('offline'); status.textContent = 'OFFLINE'; }
    }
}


function loadEmbed(name, url) {
    if (!url) return;
    const isChange = (name !== curSrv);

    // Lanzar preroll al cambiar de servidor (no en la carga inicial desde buildPlayer
    // donde isChange es false porque curSrv ya fue seteado antes de llamar loadEmbed)
    if (isChange && _vastStartPreroll && _userPlayed) {
        curSrv = name; // marcar el cambio YA: si no, cuando dismiss() vuelva a llamar
                        // a loadEmbed(name,url) por el callback, isChange seguiría dando
                        // true (curSrv aún tendría el servidor viejo) y relanzaría OTRO
                        // preroll completo en vez de cargar el embed, repitiendo el anuncio.
        _vastDone = false;
        _vastDismissCallback = function() { loadEmbed(name, url); };
        _vastStartPreroll();
        return;
    }

    curSrv = name;

    // Actualizar notice según tipo de servidor (antes de mostrarlo)
    updateNoticeForServer(name, url);

    q('epanel').classList.remove('on');
    q('nocont').classList.remove('on');

    // Desmarcar todos los items del dropdown de servidores
    const sList = q('sbar-list');
    if (sList) sList.querySelectorAll('.drop-item').forEach(b => { b.classList.remove('act'); setDot(b, null); });
    q('iarea').querySelectorAll('.frm').forEach(f => {
        if (f.id !== 'f_' + name) {
            try { ['pause', JSON.stringify({event:'pause'}), JSON.stringify({type:'pause'})].forEach(m => { try { f.contentWindow.postMessage(m, '*'); } catch(_){} }); } catch(_) {}
            f.src = 'about:blank';
        }
        f.classList.remove('fa','fv');
    });
    q('iarea').querySelectorAll('.frm-video').forEach(f => {
        if (f.id !== 'f_' + name) {
            const vid = f.querySelector('video');
            if (vid) { vid.pause(); vid.src = ''; vid.load(); }
        }
        f.classList.remove('fa','fv');
    });

    const btn   = sList ? sList.querySelector(`[data-srv="${name}"]`) : null;
    const frame = q('f_' + name);
    if (!frame) return;

    btn?.classList.add('act');
    setDot(btn, 'sl');
    // Actualizar etiqueta del botón del dropdown
    const sBarBtn = q('sbar-btn');
    if (sBarBtn) {
        sBarBtn.innerHTML = `${serverButtonHtml(name, url)}<span class="sdot"></span><span class="drop-arrow">▼</span>`;
        sBarBtn.onclick = () => toggleDrop('sbar-list', 'lbar-list');
        sBarBtn.classList.remove('sl','so','se');
        sBarBtn.classList.add('sl');
    }
    frame.classList.add('fa');
    showVerify('Espere… verificando conexión del servidor');

    // Detectar si es un contenedor de video nativo (Direct/Proxy)
    const isNativeVideo = frame.classList.contains('frm-video');

    if (isNativeVideo) {
        const vid = frame.querySelector('video');
        if (vid) {
            if (_videoRetryCount[name] === undefined) _videoRetryCount[name] = 0;

            let settled    = false;
            let guardTimer = null;

            const startAttempt = () => {
                settled = false;
                if (/\.m3u8(\?|$)/i.test(url) && typeof Hls !== 'undefined' && Hls.isSupported()) {
                    const hls = new Hls();
                    hls.loadSource(url);
                    hls.attachMedia(vid);
                    hls.on(Hls.Events.MANIFEST_PARSED, () => { vid.play().catch(()=>{}); });
                } else {
                    vid.src = url;
                    vid.load();
                }
                clearTimeout(guardTimer);
                // Cold start (sobre todo Remux: metadata + ffmpeg + Mediafire
                // antes del primer byte) puede tardar unos segundos. Si no
                // arranca en 25s, lo tratamos como fallo igual que el iframe.
                guardTimer = setTimeout(handleFail, 25000);
            };

            const handleOk = () => {
                if (settled) return;
                settled = true;
                clearTimeout(guardTimer);
                _videoRetryCount[name] = 0;
                setDot(btn, 'so'); frame.classList.add('fv'); q('bd').classList.add('gone');
                if (curSrv === name) hideVerify();
            };

            const handleFail = () => {
                if (settled) return;
                settled = true;
                clearTimeout(guardTimer);
                if (_videoRetryCount[name] < 1) {
                    // Un solo reintento silencioso: cubre el caso típico de
                    // que la primera conexión falle justo mientras el backend
                    // todavía está arrancando.
                    _videoRetryCount[name]++;
                    if (curSrv === name) showVerify('Espere… reintentando conexión con el servidor');
                    setTimeout(startAttempt, 2000);
                } else {
                    _videoRetryCount[name] = 0;
                    autoAdvance(name, btn);
                }
            };

            vid.oncanplay = handleOk;
            vid.onerror   = handleFail;
            startAttempt();
        }
        // No activar shield para video nativo (no hay popunders en src directo)
    } else {
        frame.src = url;
        // Activar shield: bloquea el primer click sobre el iframe (el que dispara popunders)
        activateShield();

        const guard = setTimeout(() => autoAdvance(name, btn), 20000);
        frame.onload  = () => { clearTimeout(guard); onOk(name, btn, frame); };
        frame.onerror = () => { clearTimeout(guard); autoAdvance(name, btn); };
    }

    // Relanzar VAST al cambiar servidor: notice aparece solo después de que el preroll termina
    if (isChange) {
        noticeClosed = false; // resetear para que el notice aparezca con el nuevo servidor
        _vastDismissCallback = function() {
            if(!noticeClosed) updateNoticeForServer(curSrv, embeds[curLang]?.[curSrv]);
        };
        _vastPreflightDone=true; _vastStartPreroll && _vastStartPreroll();
    }
}

// ── Sistema anti-popunder con detección reactiva ──────────────────────────────
//
// PROBLEMA: cada click en el iframe dispara un popunder → el shield permanente
//           bloquea los popunders PERO también bloquea toda interacción del usuario.
//
// SOLUCIÓN EN DOS CAPAS:
//
//  Capa 1 — Primer click (el más agresivo):
//    El shield arranca ACTIVO. El primer click lo absorbe el shield (el embed
//    no recibe ningún evento de usuario → window.open() no puede ejecutarse).
//    El shield envía postMessage "play" al iframe y luego se retira.
//
//  Capa 2 — Clicks posteriores (detección reactiva):
//    Cuando el usuario hace click directo en el iframe, el popunder puede abrirse.
//    En ese momento el browser pierde foco (window.blur / visibilitychange).
//    Detectamos eso y reactivamos el shield en ≤300ms, protegiéndolo del siguiente.
//    El shield se auto-retira solo tras 3s para que el usuario siga viendo el video.
//
//  Resultado: el usuario puede usar el player normalmente; si algún popunder
//  logra abrirse, el siguiente click ya está protegido automáticamente.

let _shieldTimer      = null;
let _overlayHideTimer = null;
let _lastIareaClick   = 0;   // timestamp del último click dentro del iarea
let _shieldActive     = false;

function activateShield(autoRelease) {
    const shield = q('click-shield');
    const ovPlay = q('overlay-play');
    if (!shield) return;

    clearTimeout(_shieldTimer);
    _shieldActive = true;
    shield.style.pointerEvents = 'auto';
    shield.classList.add('on');

    // Overlay-play: hint visual para que el usuario sepa que puede hacer click
    if (ovPlay) {
        ovPlay.style.opacity = '1';
        ovPlay.classList.add('on');
        clearTimeout(_overlayHideTimer);
    }

    // Handler único que se desregistra solo → absorbe UN click y libera el shield
    function onShieldClick(e) {
        e.stopPropagation();
        shield.removeEventListener('click', onShieldClick);
        clearTimeout(_shieldTimer);
        releaseShield();

        // Enviar "play" al iframe: el usuario hizo click pero el iframe no lo recibió
        const frame = q('iarea').querySelector('.frm.fa, .frm.fv');
        if (frame) {
            ['play',
             JSON.stringify({event:'play'}),
             JSON.stringify({type:'play'}),
             JSON.stringify({action:'play'}),
             JSON.stringify({method:'play'}),
            ].forEach(m => { try { frame.contentWindow.postMessage(m, '*'); } catch(_){} });
        }
    }
    shield.addEventListener('click', onShieldClick);

    // Auto-liberar si no hay click (ej: reactivación por blur sin interacción)
    if (autoRelease) {
        _shieldTimer = setTimeout(() => {
            shield.removeEventListener('click', onShieldClick);
            releaseShield();
        }, autoRelease);
    }
}

function releaseShield() {
    const shield = q('click-shield');
    const ovPlay = q('overlay-play');
    _shieldActive = false;
    if (shield) shield.classList.remove('on');
    if (ovPlay) {
        ovPlay.style.opacity = '0';
        _overlayHideTimer = setTimeout(() => ovPlay.classList.remove('on'), 300);
    }
}

// ── Capa 2: detección reactiva via window.blur ────────────────────────────────
//
// Cuando un popunder se abre, el browser mueve el foco a la nueva pestaña/ventana
// y dispara window.blur en nuestra página. Si ese blur ocurre dentro de los 400ms
// posteriores a un click en el iarea, casi con certeza fue un popunder.
// → Reactivamos el shield para proteger el siguiente click del usuario.
(function(){
    // Registrar cada click dentro del área del iframe
    const iarea = document.getElementById('iarea');
    if (iarea) {
        iarea.addEventListener('click', function() {
            _lastIareaClick = Date.now();
        }, true);  // capture: true para capturar antes del iframe
    }

    function onPopunderDetected() {
        const elapsed = Date.now() - _lastIareaClick;
        // Solo reactivar si hubo un click reciente en el iarea (< 400ms) y el
        // shield no está ya activo (evitar activaciones en cadena)
        if (elapsed < 400 && !_shieldActive) {
            // Reactivar con auto-liberar a los 3s: el usuario podrá interactuar de nuevo
            activateShield(3000);
        }
    }

    // Pérdida de foco de la ventana (desktop: popup/popunder abierto)
    window.addEventListener('blur', onPopunderDetected);

    // visibilitychange: útil en mobile y como respaldo en desktop
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) onPopunderDetected();
    });
})();

// Escuchar mensajes del iframe — si el player emite "playing", ocultar overlay-play
(function(){
    const ovPlay = q('overlay-play');
    if (!ovPlay) return;
    window.addEventListener('message', function(ev) {
        try {
            const d = typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data;
            const isPlaying = d === 'playing' || d?.event === 'play' || d?.type === 'playing'
                           || d?.event === 'playing' || d?.data?.event === 'play';
            if (isPlaying) {
                ovPlay.style.opacity = '0';
                _overlayHideTimer = setTimeout(() => ovPlay.classList.remove('on'), 300);
            }
        } catch(e) {}
    });
})();

function blockPopups(frame) {
    try {
        const w = frame.contentWindow;
        if (!w) return;
        // Bloquear window.open (popunders clásicos)
        w.open = function() { return null; };
        // Bloquear clicks que abren nuevas pestañas (_blank, target)
        w.document.addEventListener('click', function(e) {
            const a = e.target.closest('a');
            if (!a) return;
            const t = (a.getAttribute('target') || '').toLowerCase();
            const href = a.getAttribute('href') || '';
            // Dejar pasar si es ancla interna o vacío
            if (href === '' || href === '#' || href.startsWith('javascript')) return;
            if (t === '_blank' || t === '_top' || t === '_parent') {
                e.preventDefault();
                e.stopImmediatePropagation();
            }
        }, true);
    } catch(e) {
        // Cross-origin: no se puede acceder (embed de otro dominio)
        // En ese caso usamos la técnica de interception por pointer-events
    }
}

// ── Pastilla chica "verificando conexión" centrada sobre el iframe ──────────
// (distinta del overlay grande #srv-loading, que es solo para la carga
// inicial del reproductor antes de elegir un servidor)
let _verifyTimer = null;
function showVerify(text, warn) {
    const v = q('srv-verify');
    if (!v) return;
    clearTimeout(_verifyTimer);
    q('srv-verify-txt').textContent = text || 'Espere… verificando conexión del servidor';
    v.classList.toggle('sv-warn', !!warn);
    v.classList.add('on');
}
function hideVerify() {
    const v = q('srv-verify');
    if (!v) return;
    v.classList.remove('on');
}

function onOk(name, btn, frame) {
    setDot(btn, 'so');
    if (curSrv === name) hideVerify();
    if (frame && !frame.classList.contains('frm-video')) blockPopups(frame);
    if (curSrv === name) {
        frame.classList.add('fv');
        q('bd').classList.add('gone');
    }
}

// Cuando un servidor falla, saltamos automáticamente al siguiente en la lista
// (mostrando un aviso breve en la pastilla de verificación) hasta encontrar
// uno que conecte. El panel de error grande (onErr) solo aparece si se
// probaron TODOS los servidores y ninguno respondió.
function autoAdvance(name, btn) {
    setDot(btn, 'se');
    if (curSrv !== name) return; // el usuario ya cambió de servidor manualmente

    const srvs  = embeds[curLang] || {};
    const names = Object.keys(srvs);
    const idx   = names.indexOf(name);
    const next  = names[idx + 1];

    if (next) {
        const fromLabel = capitalize(trueBaseName(name));
        const toLabel   = capitalize(trueBaseName(next));
        showVerify(`"${fromLabel}" no respondió. Probando "${toLabel}"…`, true);
        setTimeout(() => {
            if (curSrv !== name) return; // el usuario ya tocó otra cosa mientras tanto
            loadEmbed(next, srvs[next]);
        }, 1100);
    } else {
        hideVerify();
        onErr(name, btn);
    }
}

function onErr(name, btn) {
    setDot(btn, 'se');
    if (curSrv !== name) return;
    hideVerify();
    q('ep-sub-txt').textContent = 'Probamos todos los servidores disponibles y ninguno respondió. Podés reintentar o cerrar y elegir otro manualmente.';
    q('epanel').classList.add('on');
}

function switchLang(lang) {
    if (lang === curLang) return;
    curLang = lang;
    // Actualizar botón del dropdown de idioma
    const lBtn = q('lbar-btn');
    if (lBtn) {
        const m = LANG_META[lang];
        lBtn.innerHTML = `<span class="df">${flagImg(m.flag, 20)}</span>${esc(m.label)}<span class="drop-arrow">▼</span>`;
        lBtn.onclick = () => toggleDrop('lbar-list', 'sbar-list');
    }
    const lList = q('lbar-list');
    if (lList) {
        lList.querySelectorAll('.drop-item').forEach(item => {
            item.classList.toggle('act', item.dataset.lang === lang);
        });
    }
    // Relanzar VAST al cambiar idioma: al terminar carga el nuevo idioma
    noticeClosed = false;
    _vastDone = false;
    _vastDismissCallback = function() { buildServerBar(lang); };
    _vastPreflightDone=true;
    if(_vastStartPreroll) _vastStartPreroll(); else buildServerBar(lang);
}

function tryNext() {
    q('epanel').classList.remove('on');
    const srvs  = embeds[curLang] || {};
    const names = Object.keys(srvs);
    if (!names.length) { q('nocont').classList.add('on'); return; }
    // Reintentar todo el ciclo desde el primer servidor de la lista
    loadEmbed(names[0], srvs[names[0]]);
}

function setDot(btn, cls) {
    if(!btn) return;
    btn.classList.remove('sl','so','se');
    if(cls) btn.classList.add(cls);
    // Reflejar estado en el botón del dropdown si este item es el activo
    if (btn.dataset && btn.dataset.srv && btn.dataset.srv === curSrv) {
        const sBarBtn = q('sbar-btn');
        if (sBarBtn) {
            sBarBtn.classList.remove('sl','so','se');
            if(cls) sBarBtn.classList.add(cls);
        }
    }
}
let _noticeTimer = null;
function closeNotice(){
  noticeClosed=true;
  const n = document.getElementById('notice');
  if(n){ n.style.opacity='0'; n.style.transform='translateY(10px)'; n.style.transition='opacity .25s,transform .25s'; setTimeout(()=>n.classList.remove('on'),260); }
  if(_noticeTimer){ clearTimeout(_noticeTimer); _noticeTimer=null; }
}
function showNoticeWithBar(durationMs){
  const n   = document.getElementById('notice');
  const bar = document.getElementById('notice-bar');
  if(!n) return;
  // Reset animación
  n.style.opacity=''; n.style.transform=''; n.style.transition='';
  n.classList.add('on');
  if(bar){
    bar.style.transition='none';
    bar.style.width='100%';
    // forzar reflow
    bar.getBoundingClientRect();
    bar.style.transition=`width ${durationMs}ms linear`;
    bar.style.width='0%';
  }
  if(_noticeTimer) clearTimeout(_noticeTimer);
  _noticeTimer = setTimeout(()=>{ if(!noticeClosed) closeNotice(); }, durationMs);
}
function q(id){ return document.getElementById(id); }
function el(tag,cls){ const e=document.createElement(tag); e.className=cls; return e; }
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function capitalize(s){ return s.charAt(0).toUpperCase()+s.slice(1); }

// (drag-to-scroll eliminado: sbar ahora es un dropdown)
