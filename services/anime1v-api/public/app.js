/* ==========================================================================
   ANIME1V STREAM - LÓGICA DE APLICACIÓN SPA (VANILLA JS & HLS.JS)
   Búsqueda Unificada | Resolución Multi-Servidor | Home Dinámico
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  
  // --- ELEMENTOS DEL DOM ---
  const searchInput = document.getElementById('search-input');
  const searchBtn = document.getElementById('search-btn');
  const resultsGrid = document.getElementById('results-grid');
  const genreFilters = document.getElementById('genre-filters');
  const scrollSentinel = document.getElementById('scroll-sentinel');
  
  const viewTitle = document.getElementById('view-title');
  const viewSubtitle = document.getElementById('view-subtitle');
  
  // Vistas (SPA Panels)
  const searchView = document.getElementById('search-view');
  const detailView = document.getElementById('detail-view');
  const playerView = document.getElementById('player-view');
  
  // Botones de navegación
  const navHome = document.getElementById('nav-home');
  const navPopular = document.getElementById('nav-popular');
  const btnBackToSearch = document.getElementById('btn-back-to-search');
  const btnBackToDetail = document.getElementById('btn-back-to-detail');
  
  // Panel de Detalle del Anime
  const animeTitle = document.getElementById('anime-title');
  const animeDescription = document.getElementById('anime-description');
  const animeType = document.getElementById('anime-type');
  const animePoster = document.getElementById('anime-poster');
  const animeGenres = document.getElementById('anime-genres');
  const animeHeroBanner = document.getElementById('anime-hero-banner');
  const episodesGrid = document.getElementById('episodes-grid');
  
  // Panel del Reproductor
  const playingTitle = document.getElementById('playing-title');
  const playingSubtitle = document.getElementById('playing-subtitle');
  const videoLoader = document.getElementById('video-loader');
  const mainPlayer = document.getElementById('main-player');
  const serversList = document.getElementById('servers-list');
  const playerPrevBtn = document.getElementById('player-prev-btn');
  const playerCatalogBtn = document.getElementById('player-catalog-btn');
  const playerExternalBtn = document.getElementById('player-external-btn');
  const playerNextBtn = document.getElementById('player-next-btn');
  
  // --- VARIABLES DE ESTADO ---
  let hlsInstance = null;
  let currentAnimeData = null;
  let currentEpisodeLinks = null;
  let currentEpisodeNumber = null;
  let catalogPage = 1;
  let catalogGenre = '';
  let catalogTitle = 'Catálogo';
  let catalogSubtitle = 'Explora el catálogo completo de anime';
  let isLoadingMore = false;
  let hasMorePages = true;
  let currentMode = 'catalog'; // 'catalog' | 'search'
  let infiniteObserver = null;
  
  const API_KEY = "dev-anime1v-key";
  
  const getHeaders = () => ({
    'Content-Type': 'application/json',
    'x-api-key': API_KEY
  });

  // --- NAVEGACIÓN Y CAMBIO DE PANTALLAS (SPA) ---
  function switchView(targetPanel) {
    if (targetPanel !== playerView) {
      stopVideoPlayer();
    }
    
    [searchView, detailView, playerView].forEach(panel => {
      panel.classList.remove('active');
    });
    
    targetPanel.classList.add('active');

    // Mostrar/ocultar filtros de género según la vista
    if (targetPanel === searchView && currentMode === 'catalog') {
      genreFilters.style.display = 'flex';
    } else {
      genreFilters.style.display = 'none';
    }
  }

  // --- GENERACIÓN DE SKELETONS DE CARGA ---
  function showGridSkeletons(count = 12) {
    resultsGrid.innerHTML = '';
    for (let i = 0; i < count; i++) {
      const skeleton = document.createElement('div');
      skeleton.className = 'skeleton-card';
      resultsGrid.appendChild(skeleton);
    }
  }

  function appendGridSkeletons(count = 4) {
    for (let i = 0; i < count; i++) {
      const skeleton = document.createElement('div');
      skeleton.className = 'skeleton-card';
      resultsGrid.appendChild(skeleton);
    }
  }

  function removeSkeletons() {
    document.querySelectorAll('.skeleton-card').forEach(s => s.remove());
  }

  // --- SCROLL INFINITO CON INTERSECTION OBSERVER ---
  function setupInfiniteScroll() {
    if (infiniteObserver) infiniteObserver.disconnect();
    
    infiniteObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && !isLoadingMore && hasMorePages && currentMode === 'catalog') {
          loadMoreCatalog();
        }
      }
    }, { rootMargin: '200px' });

    infiniteObserver.observe(scrollSentinel);
  }

  function showScrollSentinel() {
    scrollSentinel.classList.add('visible');
  }

  function hideScrollSentinel() {
    scrollSentinel.classList.remove('visible');
  }

  // --- 1. FUNCIÓN: HOME DINÁMICO (CATÁLOGO) ---
  async function loadCatalog(resetGrid = true) {
    currentMode = 'catalog';
    switchView(searchView);
    
    if (catalogGenre) {
      viewTitle.textContent = `Género: ${catalogGenre.charAt(0).toUpperCase() + catalogGenre.slice(1)}`;
      viewSubtitle.textContent = `Explorando el catálogo filtrado por género`;
    } else {
      viewTitle.textContent = catalogTitle;
      viewSubtitle.textContent = catalogSubtitle;
    }

    if (resetGrid) {
      catalogPage = 1;
      hasMorePages = true;
      showGridSkeletons();
    }

    try {
      let url = `/api/v1/anime/catalog?page=${catalogPage}`;
      if (catalogGenre) url += `&genre=${encodeURIComponent(catalogGenre)}`;
      
      const res = await fetch(url, { headers: getHeaders() });
      const responseData = await res.json();

      if (resetGrid) resultsGrid.innerHTML = '';

      if (responseData.success && responseData.data?.results?.length > 0) {
        appendAnimeCards(responseData.data.results, 'AnimeAV1');
        hasMorePages = responseData.data.hasMore;
        
        if (hasMorePages) {
          showScrollSentinel();
        } else {
          hideScrollSentinel();
        }
      } else {
        if (resetGrid) renderNoResults();
        hasMorePages = false;
        hideScrollSentinel();
      }
    } catch (err) {
      console.error('Error al cargar catálogo:', err);
      if (resetGrid) renderErrorView();
      hideScrollSentinel();
    }
  }

  async function loadMoreCatalog() {
    if (isLoadingMore || !hasMorePages) return;
    isLoadingMore = true;
    catalogPage++;
    
    appendGridSkeletons(4);

    try {
      let url = `/api/v1/anime/catalog?page=${catalogPage}`;
      if (catalogGenre) url += `&genre=${encodeURIComponent(catalogGenre)}`;
      
      const res = await fetch(url, { headers: getHeaders() });
      const responseData = await res.json();

      removeSkeletons();

      if (responseData.success && responseData.data?.results?.length > 0) {
        appendAnimeCards(responseData.data.results, 'AnimeAV1');
        hasMorePages = responseData.data.hasMore;
      } else {
        hasMorePages = false;
      }

      if (!hasMorePages) hideScrollSentinel();
    } catch (err) {
      console.error('Error al cargar más catálogo:', err);
      removeSkeletons();
      hasMorePages = false;
      hideScrollSentinel();
    } finally {
      isLoadingMore = false;
    }
  }

  // --- 2. FUNCIÓN: BUSCAR ANIMES (UNIFICADA — SIN SELECTOR DE PROVEEDOR) ---
  async function performSearch(query = '') {
    const q = query.trim();
    if (!q) return;

    currentMode = 'search';
    switchView(searchView);
    genreFilters.style.display = 'none';
    viewTitle.textContent = `Buscando "${q}"`;
    viewSubtitle.textContent = `Consultando todos los proveedores simultáneamente...`;
    showGridSkeletons();
    hideScrollSentinel();

    try {
      // No enviamos domain → el backend prueba TODOS los proveedores automáticamente
      const res = await fetch(`/api/v1/anime/search?q=${encodeURIComponent(q)}`, {
        headers: getHeaders()
      });
      const responseData = await res.json();

      if (responseData.success && responseData.data?.results?.length > 0) {
        const providerLabel = responseData.source || 'Multi';
        viewSubtitle.textContent = `${responseData.data.count} resultados encontrados (vía ${providerLabel})`;
        resultsGrid.innerHTML = '';
        appendAnimeCards(responseData.data.results, providerLabel);
      } else {
        renderNoResults();
      }
    } catch (err) {
      console.error('Error al realizar búsqueda:', err);
      renderErrorView();
    }
  }

  // Renderizar tarjetas de Anime (reutilizable para catálogo y búsqueda)
  function appendAnimeCards(results, providerLabel = '') {
    results.forEach(anime => {
      const card = document.createElement('div');
      card.className = 'anime-card';
      card.setAttribute('data-url', anime.url);
      
      const imageUrl = anime.image 
        ? `/api/v1/anime/image-proxy?url=${encodeURIComponent(anime.image)}&apiKey=${API_KEY}` 
        : '';

      const imgTag = imageUrl
        ? `<img src="${imageUrl}" class="card-image" alt="${anime.title}" loading="lazy">`
        : `<div class="card-image" style="background:var(--bg-tertiary);display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:13px;text-align:center;padding:12px;">${anime.title}</div>`;

      card.innerHTML = `
        <div class="card-image-wrapper">
          ${imgTag}
          <span class="card-badge">${anime.provider || providerLabel}</span>
        </div>
        <div class="card-content">
          <h4 class="card-title">${anime.title}</h4>
          <div class="card-meta">
            <span>${anime.type || 'Serie'}</span>
            <span>${anime.year || ''}</span>
          </div>
        </div>
      `;

      card.addEventListener('click', () => {
        loadAnimeDetail(anime.url);
      });

      resultsGrid.appendChild(card);
    });
  }

  function renderNoResults() {
    resultsGrid.innerHTML = `
      <div style="grid-column: 1 / -1; text-align: center; padding: 48px; color: var(--text-muted);">
        <i class="fa-solid fa-face-frown" style="font-size: 48px; margin-bottom: 16px; color: var(--accent-purple);"></i>
        <h3>No se encontraron resultados</h3>
        <p>Intenta con otros términos de búsqueda.</p>
      </div>
    `;
  }

  function renderErrorView() {
    resultsGrid.innerHTML = `
      <div style="grid-column: 1 / -1; text-align: center; padding: 48px; color: var(--text-muted);">
        <i class="fa-solid fa-triangle-exclamation" style="font-size: 48px; margin-bottom: 16px; color: #ff3838;"></i>
        <h3>Error al consultar la API</h3>
        <p>El servidor puede estar offline o los proveedores están bloqueando la petición.</p>
      </div>
    `;
  }

  // --- 3. FUNCIÓN: CARGAR DETALLES DEL ANIME ---
  async function loadAnimeDetail(animeUrl) {
    switchView(detailView);
    
    animeTitle.textContent = "Cargando detalles...";
    animeDescription.textContent = "Obteniendo información del catálogo...";
    animeType.textContent = "Anime";
    animePoster.style.backgroundImage = 'none';
    animeGenres.innerHTML = '';
    episodesGrid.innerHTML = '<div style="grid-column:1/-1; text-align:center;">Cargando lista de capítulos...</div>';

    try {
      const res = await fetch(`/api/v1/anime/info?url=${encodeURIComponent(animeUrl)}`, {
        headers: getHeaders()
      });
      const responseData = await res.json();

      if (responseData.success && responseData.data) {
        currentAnimeData = responseData.data;
        renderAnimeDetail(currentAnimeData);
      } else {
        animeTitle.textContent = "Error al cargar";
        animeDescription.textContent = "No se pudo recuperar la información del anime.";
      }
    } catch (err) {
      console.error('Error al cargar detalle:', err);
      animeTitle.textContent = "Error";
      animeDescription.textContent = "Ocurrió una falla de red al contactar con el backend.";
    }
  }

  function renderAnimeDetail(data) {
    animeTitle.textContent = data.title;
    animeDescription.textContent = data.description || 'Sin sinopsis disponible.';
    animeType.textContent = data.type || 'Serie';
    
    // Póster
    const posterUrl = data.image 
      ? `/api/v1/anime/image-proxy?url=${encodeURIComponent(data.image)}&apiKey=${API_KEY}` 
      : '';
    animePoster.style.backgroundImage = posterUrl ? `url('${posterUrl}')` : 'none';
    
    // Géneros
    animeGenres.innerHTML = '';
    if (data.genres && data.genres.length > 0) {
      data.genres.forEach(genre => {
        const tag = document.createElement('span');
        tag.className = 'genre-tag';
        tag.textContent = genre.name;
        animeGenres.appendChild(tag);
      });
    }

    // Episodios
    episodesGrid.innerHTML = '';
    if (data.episodes && data.episodes.length > 0) {
      const sortedEps = [...data.episodes].sort((a, b) => a.number - b.number);
      
      sortedEps.forEach(ep => {
        const btn = document.createElement('button');
        btn.className = 'episode-btn';
        btn.innerHTML = `<i class="fa-solid fa-play" style="font-size:12px; margin-right:4px;"></i> Ep ${ep.number}`;
        
        btn.addEventListener('click', () => {
          playEpisode(ep.url, ep.number);
        });
        
        episodesGrid.appendChild(btn);
      });
    } else {
      episodesGrid.innerHTML = '<div style="grid-column:1/-1; text-align:center; color:var(--text-muted);">No se encontraron episodios disponibles para ver online.</div>';
    }
  }

  // --- 4. FUNCIÓN: OBTENER SERVIDORES Y RESOLUCIÓN MULTI-SERVIDOR EN CASCADA ---
  async function playEpisode(episodeUrl, episodeNumber) {
    switchView(playerView);
    currentEpisodeNumber = episodeNumber;
    playingTitle.textContent = `${currentAnimeData.title}`;
    playingSubtitle.textContent = `Cargando Episodio ${episodeNumber}...`;
    
    playerExternalBtn.style.display = 'none';
    playerExternalBtn.href = '#';
    
    // Configurar botones de control del reproductor (Anterior / Siguiente / Catálogo)
    if (currentAnimeData && currentAnimeData.episodes) {
      const eps = [...currentAnimeData.episodes].sort((a, b) => parseFloat(a.number) - parseFloat(b.number));
      const currentNum = parseFloat(episodeNumber);
      const currentIdx = eps.findIndex(ep => parseFloat(ep.number) === currentNum);

      const prevEp = currentIdx > 0 ? eps[currentIdx - 1] : null;
      const nextEp = currentIdx !== -1 && currentIdx < eps.length - 1 ? eps[currentIdx + 1] : null;

      if (prevEp) {
        playerPrevBtn.disabled = false;
        playerPrevBtn.onclick = () => playEpisode(prevEp.url, prevEp.number);
      } else {
        playerPrevBtn.disabled = true;
        playerPrevBtn.onclick = null;
      }

      if (nextEp) {
        playerNextBtn.disabled = false;
        playerNextBtn.onclick = () => playEpisode(nextEp.url, nextEp.number);
      } else {
        playerNextBtn.disabled = true;
        playerNextBtn.onclick = null;
      }

      playerCatalogBtn.onclick = () => {
        stopVideoPlayer();
        switchView(searchView);
      };
    }
    
    videoLoader.style.opacity = '1';
    videoLoader.style.pointerEvents = 'all';
    videoLoader.innerHTML = `
      <div class="spinner"></div>
      <p>Obteniendo servidores del episodio ${episodeNumber}...</p>
    `;
    serversList.innerHTML = '<div style="color:var(--text-muted);">Consultando servidores de video...</div>';

    try {
      const res = await fetch(`/api/v1/anime/episode?url=${encodeURIComponent(episodeUrl)}`, {
        headers: getHeaders()
      });
      const responseData = await res.json();

      if (responseData.success && responseData.data?.servers?.sub) {
        currentEpisodeLinks = responseData.data.servers.sub;
        resolveMultiServer(currentEpisodeLinks);
      } else {
        playingSubtitle.textContent = "Error al obtener servidores";
        videoLoader.innerHTML = '<i class="fa-solid fa-face-frown" style="font-size:36px; color:var(--accent-purple);"></i> <p>No se encontraron servidores de streaming para este episodio.</p>';
      }
    } catch (err) {
      console.error('Error al obtener servidores:', err);
      playingSubtitle.textContent = "Error de red";
      videoLoader.innerHTML = '<i class="fa-solid fa-triangle-exclamation" style="font-size:36px; color:#ff3838;"></i> <p>Falla al contactar con la API.</p>';
    }
  }

  // Resolución multi-servidor en cascada:
  // Recopila TODOS los servidores soportados y envía sus URLs al backend.
  // El backend los prueba en orden y devuelve el primer resultado exitoso.
  async function resolveMultiServer(servers) {
    // Identificar servidores soportados (que tienen resolutor nativo)
    const supportedServers = servers.filter(s => {
      const n = (s.server || '').toLowerCase() + ' ' + (s.url || '').toLowerCase();
      return n.includes('voe') || n.includes('wish') || n.includes('tape') || n.includes('playnix') || n.includes('medix') || n.includes('awish');
    });

    // Renderizar TODOS los botones de servidor (soportados + no soportados)
    renderAllServerButtons(servers, supportedServers);

    if (supportedServers.length === 0) {
      // Sin servidores soportados: ir directo a iframe con el primero disponible
      if (servers.length > 0) {
        playingSubtitle.textContent = `Reproduciendo Episodio ${currentEpisodeNumber} (Iframe: ${servers[0].server})`;
        loadIframePlayer(servers[0].url);
      } else {
        videoLoader.innerHTML = '<p>No hay reproductores disponibles.</p>';
      }
      return;
    }

    // Mostrar progreso visual
    videoLoader.innerHTML = `
      <div class="spinner"></div>
      <p>Probando ${supportedServers.length} servidor${supportedServers.length > 1 ? 'es' : ''} en cascada para encontrar el mejor stream sin anuncios...</p>
    `;
    videoLoader.style.opacity = '1';
    videoLoader.style.pointerEvents = 'all';

    // Marcar todos como "trying" visualmente
    supportedServers.forEach((s, i) => {
      const btn = document.querySelector(`.server-btn[data-idx="${servers.indexOf(s)}"]`);
      if (btn && i === 0) btn.classList.add('server-trying');
    });

    // Enviar TODOS los URLs soportados al backend para cascada
    const urlsArray = supportedServers.map(s => s.url);

    try {
      const res = await fetch(`/api/v1/anime/resolve?urls=${encodeURIComponent(JSON.stringify(urlsArray))}`, {
        headers: getHeaders()
      });
      const responseData = await res.json();

      // Limpiar clases de estado en botones
      document.querySelectorAll('.server-btn').forEach(b => {
        b.classList.remove('server-trying');
      });

      if (responseData.success && responseData.streamUrl) {
        const streamUrl = responseData.streamUrl;
        const mediaType = responseData.mediaType;
        const resolvedFrom = responseData.resolvedFrom || '';
        
        // Marcar el servidor exitoso como activo
        const winnerIdx = servers.findIndex(s => s.url === resolvedFrom);
        if (winnerIdx !== -1) {
          const winBtn = document.querySelector(`.server-btn[data-idx="${winnerIdx}"]`);
          if (winBtn) winBtn.classList.add('active');
        }

        // Marcar los que fallaron (los que están antes del ganador en la lista soportada)
        const winnerSupportedIdx = supportedServers.findIndex(s => s.url === resolvedFrom);
        for (let i = 0; i < winnerSupportedIdx; i++) {
          const failIdx = servers.indexOf(supportedServers[i]);
          const failBtn = document.querySelector(`.server-btn[data-idx="${failIdx}"]`);
          if (failBtn) failBtn.classList.add('server-failed');
        }

        console.log(`[CASCADA] Stream resuelto exitosamente desde: ${resolvedFrom}`);
        playingSubtitle.textContent = `Streaming Directo Limpio (${responseData.server}) — Capítulo ${currentEpisodeNumber}`;
        
        videoLoader.style.opacity = '0';
        videoLoader.style.pointerEvents = 'none';

        startVideoPlayer(streamUrl, mediaType, servers[0]?.url);
      } else {
        throw new Error("Cascada agotada — ningún servidor devolvió stream");
      }
    } catch (err) {
      console.warn(`[CASCADA] Todos los servidores fallaron:`, err.message);
      
      // Marcar todos los soportados como fallidos
      document.querySelectorAll('.server-btn').forEach(b => {
        b.classList.remove('server-trying');
      });
      supportedServers.forEach(s => {
        const idx = servers.indexOf(s);
        const btn = document.querySelector(`.server-btn[data-idx="${idx}"]`);
        if (btn) btn.classList.add('server-failed');
      });

      // Fallback: cargar el primer servidor disponible en iframe
      playingSubtitle.textContent = `Reproduciendo Episodio ${currentEpisodeNumber} (Iframe de Respaldo)`;
      loadIframePlayer(servers[0].url);
    }
  }

  // Renderizar la lista de botones de servidor
  function renderAllServerButtons(allServers, supportedServers) {
    serversList.innerHTML = '';
    
    allServers.forEach((s, idx) => {
      const isSupported = supportedServers.includes(s);
      const btn = document.createElement('button');
      btn.className = 'server-btn';
      btn.setAttribute('data-idx', idx);
      
      if (isSupported) {
        btn.innerHTML = `<i class="fa-solid fa-bolt"></i> ${s.server} (Sin Anuncios)`;
      } else {
        btn.innerHTML = `<i class="fa-solid fa-external-link"></i> ${s.server} (Iframe)`;
      }
      
      btn.addEventListener('click', () => {
        // Clic manual: resolver este servidor individual
        document.querySelectorAll('.server-btn').forEach(b => b.classList.remove('active', 'server-trying'));
        btn.classList.add('active');
        
        if (isSupported) {
          resolveSingleServer(s.url, s.server);
        } else {
          playingSubtitle.textContent = `Reproduciendo Episodio ${currentEpisodeNumber} (Iframe: ${s.server})`;
          loadIframePlayer(s.url);
        }
      });
      
      serversList.appendChild(btn);
    });
  }

  // Resolución de un servidor individual (clic manual)
  async function resolveSingleServer(embedUrl, serverName) {
    videoLoader.innerHTML = `
      <div class="spinner"></div>
      <p>Resolviendo enlace nativo de <strong>${serverName}</strong>...</p>
    `;
    videoLoader.style.opacity = '1';
    videoLoader.style.pointerEvents = 'all';
    stopVideoPlayer();

    try {
      const res = await fetch(`/api/v1/anime/resolve?url=${encodeURIComponent(embedUrl)}`, {
        headers: getHeaders()
      });
      const responseData = await res.json();

      if (responseData.success && responseData.streamUrl) {
        console.log(`[STREAMING] Enlace directo resuelto: ${responseData.streamUrl}`);
        playingSubtitle.textContent = `Streaming Directo (${serverName}) — Capítulo ${currentEpisodeNumber}`;
        
        videoLoader.style.opacity = '0';
        videoLoader.style.pointerEvents = 'none';
        startVideoPlayer(responseData.streamUrl, responseData.mediaType, embedUrl);
      } else {
        throw new Error("No se devolvió un enlace válido");
      }
    } catch (err) {
      console.warn(`[RESOLVER] Falló resolución de ${serverName}:`, err.message);
      playingSubtitle.textContent = `Reproduciendo Episodio ${currentEpisodeNumber} (Iframe: ${serverName})`;
      loadIframePlayer(embedUrl);
    }
  }

  // --- 5. CONTROLES DEL REPRODUCTOR MULTIMEDIA ---
  function startVideoPlayer(url, mediaType, fallbackUrl = null) {
    stopVideoPlayer();

    if (fallbackUrl) {
      playerExternalBtn.style.display = 'flex';
      playerExternalBtn.href = fallbackUrl;
    } else {
      playerExternalBtn.style.display = 'none';
    }

    const iframe = document.getElementById('iframe-player');
    if (iframe) iframe.remove();
    mainPlayer.style.display = 'block';

    let fallbackTriggered = false;
    const triggerFallback = () => {
      if (fallbackTriggered) return;
      fallbackTriggered = true;
      if (fallbackUrl) {
        console.warn(`[PLAYER] Fallback automático a iframe: ${fallbackUrl}`);
        playingSubtitle.textContent = `Reproduciendo Episodio ${currentEpisodeNumber} (Iframe de Respaldo)`;
        loadIframePlayer(fallbackUrl);
      } else {
        stopVideoPlayer();
      }
    };

    mainPlayer.onerror = () => {
      console.warn('[HTML5 Video] Error nativo detectado');
      triggerFallback();
    };

    if (mediaType === 'hls') {
      if (Hls.isSupported()) {
        console.log('[HLS.js] Inicializando streaming HLS...');
        hlsInstance = new Hls({
          maxMaxBufferLength: 30,
          enableWorker: true,
          manifestLoadingMaxRetry: 3,
          levelLoadingMaxRetry: 3,
          fragLoadingMaxRetry: 3
        });
        hlsInstance.loadSource(url);
        hlsInstance.attachMedia(mainPlayer);
        hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => {
          mainPlayer.play().catch(() => console.log("Auto-play bloqueado"));
        });

        hlsInstance.on(Hls.Events.ERROR, function (event, data) {
          if (data.fatal) {
            switch (data.type) {
              case Hls.ErrorTypes.NETWORK_ERROR:
                console.log("[HLS] Error de red fatal, reintentando...");
                hlsInstance.startLoad();
                setTimeout(() => {
                  if (mainPlayer.paused || mainPlayer.readyState === 0) {
                    triggerFallback();
                  }
                }, 4000);
                break;
              case Hls.ErrorTypes.MEDIA_ERROR:
                console.log("[HLS] Error multimedia, recuperando...");
                hlsInstance.recoverMediaError();
                break;
              default:
                triggerFallback();
                break;
            }
          }
        });
      } 
      else if (mainPlayer.canPlayType('application/vnd.apple.mpegurl')) {
        mainPlayer.src = url;
        mainPlayer.addEventListener('loadedmetadata', () => {
          mainPlayer.play().catch(() => {});
        });
      } else {
        triggerFallback();
      }
    } 
    else {
      mainPlayer.src = url;
      mainPlayer.load();
      mainPlayer.addEventListener('canplay', () => {
        mainPlayer.play().catch(() => {});
      });
    }
  }

  function stopVideoPlayer() {
    mainPlayer.pause();
    mainPlayer.removeAttribute('src');
    mainPlayer.load();
    mainPlayer.onerror = null;

    if (hlsInstance) {
      hlsInstance.destroy();
      hlsInstance = null;
    }

    const iframe = document.getElementById('iframe-player');
    if (iframe) {
      iframe.src = 'about:blank';
      iframe.remove();
    }
  }

  function loadIframePlayer(embedUrl) {
    stopVideoPlayer();
    mainPlayer.style.display = 'none';

    const oldIframe = document.getElementById('iframe-player');
    if (oldIframe) oldIframe.remove();

    const iframe = document.createElement('iframe');
    iframe.id = 'iframe-player';
    iframe.src = embedUrl;
    
    playerExternalBtn.style.display = 'flex';
    playerExternalBtn.href = embedUrl;
    iframe.style.position = 'absolute';
    iframe.style.top = '0';
    iframe.style.left = '0';
    iframe.style.width = '100%';
    iframe.style.height = '100%';
    iframe.setAttribute('allowfullscreen', 'true');

    document.getElementById('video-container').appendChild(iframe);
    
    videoLoader.style.opacity = '0';
    videoLoader.style.pointerEvents = 'none';
  }

  // --- FILTROS DE GÉNERO ---
  genreFilters.addEventListener('click', (e) => {
    const chip = e.target.closest('.genre-chip');
    if (!chip) return;
    
    document.querySelectorAll('.genre-chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    
    catalogGenre = chip.getAttribute('data-genre') || '';
    loadCatalog(true);
  });

  // --- VÍNCULOS Y NAVEGACIÓN GENERAL ---
  searchBtn.addEventListener('click', () => performSearch(searchInput.value));
  searchInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') performSearch(searchInput.value);
  });

  navHome.addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    navHome.classList.add('active');
    searchInput.value = '';
    catalogTitle = 'Catálogo';
    catalogSubtitle = 'Explora el catálogo completo de anime';
    catalogGenre = '';
    document.querySelectorAll('.genre-chip').forEach(c => c.classList.remove('active'));
    document.querySelector('.genre-chip[data-genre=""]').classList.add('active');
    loadCatalog(true);
  });

  navPopular.addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    navPopular.classList.add('active');
    searchInput.value = '';
    catalogTitle = 'Tendencias';
    catalogSubtitle = 'Los animes más populares y recomendados del catálogo';
    catalogGenre = '';
    document.querySelectorAll('.genre-chip').forEach(c => c.classList.remove('active'));
    document.querySelector('.genre-chip[data-genre=""]').classList.add('active');
    loadCatalog(true);
  });

  btnBackToSearch.addEventListener('click', () => {
    switchView(searchView);
  });

  btnBackToDetail.addEventListener('click', () => {
    switchView(detailView);
  });

  // --- CARGA INICIAL ---
  loadCatalog(true);
  setupInfiniteScroll();
});
