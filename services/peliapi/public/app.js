// Web GUI Application State
const state = {
  activeType: "movie",
  results: [],
  selectedMedia: null,
  activeSeason: 1,
  activeEpisode: 1,
  downloads: new Map(), // id -> download record
  pollingIntervals: new Map(), // id -> timeout id
  
  // Streaming Premium & Watch-First states
  filters: {
    type: 'all',       // 'all', 'movie', 'series', 'anime'
    genre: '',         // slug del género
    minRating: 0,      // rating mínimo
    sortBy: 'default'  // 'default', 'rating-desc', 'rating-asc', 'title-asc', 'title-desc'
  },
  genres: [],           // lista de géneros
  currentPage: 1,       // página actual
  hasNextPage: false,   // si hay página siguiente
  isLoadingMore: false, // previene llamadas simultáneas
  heroItems: [],        // películas en rotación del banner
  heroIndex: 0,
  heroInterval: null
};

const API_BASE = "/api/v1/content";

// DOM Elements
const searchForm = document.getElementById("search-form");
const searchInput = document.getElementById("search-input");
const filterTabs = document.querySelectorAll(".filter-tab");
const resultsGrid = document.getElementById("results-grid");
const resultsLoading = document.getElementById("results-loading");
const resultsEmpty = document.getElementById("results-empty");
const resultsCount = document.getElementById("results-count");
const resultsTitle = document.getElementById("results-title");

const detailsPanel = document.getElementById("details-panel");
const detailsEmpty = document.getElementById("details-empty");
const detailsContent = document.getElementById("details-content");
const detailsPoster = document.getElementById("details-poster");
const detailsType = document.getElementById("details-type");
const detailsTitle = document.getElementById("details-title");
const detailsOriginalTitle = document.getElementById("details-original-title");
const detailsYear = document.getElementById("details-year");
const detailsRating = document.getElementById("details-rating");
const detailsGenres = document.getElementById("details-genres");
const detailsSynopsis = document.getElementById("details-synopsis");
const detailsDirector = document.getElementById("details-director");
const seasonsSection = document.getElementById("seasons-section");
const seasonsTabsContainer = document.getElementById("seasons-tabs-container");
const episodesContainer = document.getElementById("episodes-container");
const serversSectionTitle = document.getElementById("servers-section-title");
const serversLoading = document.getElementById("servers-loading");
const serversContainer = document.getElementById("servers-container");

const videoPlayerContainer = document.getElementById("video-player-container");
const playerIframe = document.getElementById("player-iframe");
const playerTitle = document.getElementById("player-title");
const closePlayerBtn = document.getElementById("close-player-btn");

const downloadsContainer = document.getElementById("downloads-container");
const downloadsEmpty = document.getElementById("downloads-empty");
const clearCompletedBtn = document.getElementById("clear-completed-downloads");

// New DOM Elements for Cinematic SPA Design
const netflixNavbar = document.getElementById("netflix-navbar");
const homeView = document.getElementById("home-view");
const gridView = document.getElementById("grid-view");
const downloadsDrawer = document.getElementById("downloads-drawer");
const drawerBackdrop = document.getElementById("drawer-backdrop");
const downloadsBadge = document.getElementById("downloads-badge");

// Initialize Application
document.addEventListener("DOMContentLoaded", () => {
  setupEventListeners();
  checkBackendStatus();
  loadHomepage();
  loadGenres();
  setupInfiniteScroll();
});

// Window Scroll Effect on Navbar
window.addEventListener("scroll", () => {
  if (window.scrollY > 40) {
    netflixNavbar.classList.add("scrolled");
  } else {
    netflixNavbar.classList.remove("scrolled");
  }
});

// SPA View Navigation Functions
function showHomeView() {
  homeView.classList.remove("hidden");
  gridView.classList.add("hidden");
  
  // Manage Navbar active classes
  document.querySelectorAll(".netflix-navbar li").forEach(li => li.classList.remove("active"));
  document.getElementById("nav-item-home").classList.add("active");
  
  // Clear search input
  searchInput.value = "";
}

function showGridView(titleText) {
  homeView.classList.add("hidden");
  gridView.classList.remove("hidden");
  resultsTitle.textContent = titleText;
}

// Downloads Drawer Toggle
function toggleDownloadsDrawer() {
  downloadsDrawer.classList.toggle("open");
  drawerBackdrop.classList.toggle("open");
}

// Close Immersive Overlay Details Modal
function closeDetailsModal() {
  detailsPanel.classList.add("hidden");
  // Instantly cut video playback source and hide video player wrapper
  playerIframe.src = "";
  videoPlayerContainer.classList.add("hidden");
}

// Event Listeners Configuration
function setupEventListeners() {
  // Dynamic event bindings to completely bypass strict Content Security Policy inline restrictions
  document.getElementById("brand-logo").addEventListener("click", () => {
    state.filters.type = 'all';
    state.filters.genre = '';
    state.filters.minRating = 0;
    state.filters.sortBy = 'default';
    
    // Sync filters UI
    document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
    document.querySelector('.filter-chip[data-type="all"]').classList.add('active');
    document.getElementById('genre-filter').value = '';
    document.getElementById('rating-filter').value = '';
    document.getElementById('sort-filter').value = 'default';
    
    showHomeView();
  });
  
  document.getElementById("nav-link-home").addEventListener("click", () => {
    state.filters.type = 'all';
    state.filters.genre = '';
    state.filters.minRating = 0;
    state.filters.sortBy = 'default';
    
    // Sync filters UI
    document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
    document.querySelector('.filter-chip[data-type="all"]').classList.add('active');
    document.getElementById('genre-filter').value = '';
    document.getElementById('rating-filter').value = '';
    document.getElementById('sort-filter').value = 'default';
    
    showHomeView();
  });
  
  document.getElementById("btn-downloads-toggle").addEventListener("click", toggleDownloadsDrawer);
  document.getElementById("downloads-panel-close").addEventListener("click", toggleDownloadsDrawer);
  document.getElementById("drawer-backdrop").addEventListener("click", toggleDownloadsDrawer);
  
  document.getElementById("modal-backdrop-close").addEventListener("click", closeDetailsModal);
  document.getElementById("btn-close-modal").addEventListener("click", closeDetailsModal);

  // Search submission
  searchForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const query = searchInput.value.trim();
    if (query) {
      performSearch(query);
    }
  });

  // Category filter tabs in navbar
  filterTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      // Manage Active Classes in Navigation
      document.querySelectorAll(".netflix-navbar li").forEach((li) => li.classList.remove("active"));
      tab.closest("li").classList.add("active");
      
      const type = tab.getAttribute("data-type");
      state.activeType = type;
      state.filters.type = type;
      
      // Update filter UI chips
      document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
      const activeChip = document.querySelector(`.filter-chip[data-type="${type}"]`);
      if (activeChip) activeChip.classList.add("active");

      // Reset search field
      searchInput.value = "";
      
      const label = type === "movie" ? "Películas" : type === "series" ? "Series" : "Anime";
      showGridView(`Catálogo: ${label}`);
      loadFilteredCatalog(true);
    });
  });

  // Premium Filter Toolbar Event Handlers
  document.querySelectorAll('.filter-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      state.filters.type = chip.getAttribute('data-type');
      loadFilteredCatalog(true);
    });
  });

  document.getElementById('genre-filter').addEventListener('change', (e) => {
    state.filters.genre = e.target.value;
    loadFilteredCatalog(true);
  });

  document.getElementById('rating-filter').addEventListener('change', (e) => {
    state.filters.minRating = Number(e.target.value) || 0;
    loadFilteredCatalog(true);
  });

  document.getElementById('sort-filter').addEventListener('change', (e) => {
    state.filters.sortBy = e.target.value;
    // Client-side sort is instant
    const filtered = applyClientFilters(state.results);
    displayResults(filtered);
  });

  // Clear completed downloads
  clearCompletedBtn.addEventListener("click", () => {
    for (const [id, record] of state.downloads.entries()) {
      if (record.status === "completed" || record.status === "failed") {
        removeDownloadCard(id);
      }
    }
  });

  // Close video player
  closePlayerBtn.addEventListener("click", () => {
    videoPlayerContainer.classList.add("hidden");
    playerIframe.src = "";
  });
}

const abortControllers = new Map();

// Fetch helper with AbortController to cancel redundant identical requests
async function apiFetch(endpoint, options = {}) {
  const key = endpoint;
  if (abortControllers.has(key)) {
    abortControllers.get(key).abort();
  }

  const controller = new AbortController();
  abortControllers.set(key, controller);

  try {
    const headers = {
      "Content-Type": "application/json",
      ...(options.headers || {})
    };

    const url = endpoint === "/health" ? "/health" : `${API_BASE}${endpoint}`;
    const response = await fetch(url, {
      ...options,
      headers,
      signal: controller.signal
    });

    const json = await response.json();
    if (!response.ok || !json.success) {
      throw new Error(json.message || `API Error: ${response.status}`);
    }

    return json.data;
  } catch (err) {
    if (err.name === 'AbortError') {
      return null;
    }
    throw err;
  } finally {
    abortControllers.delete(key);
  }
}

// Secure element creation helper (Anti-XSS)
function createEl(tag, classes = [], attrs = {}, text = '') {
  const el = document.createElement(tag);
  if (classes.length) {
    el.classList.add(...classes);
  }
  Object.entries(attrs).forEach(([k, v]) => {
    if (v !== null && v !== undefined) {
      el.setAttribute(k, v);
    }
  });
  if (text) {
    el.textContent = text;
  }
  return el;
}

// Premium non-blocking toast notifications
function showToast(message, type = 'error', duration = 4000) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = createEl('div', ['toast', type]);
  
  let iconName = 'alert-circle-outline';
  if (type === 'success') iconName = 'checkmark-circle-outline';
  if (type === 'info') iconName = 'information-circle-outline';
  
  const icon = createEl('ion-icon', [], { name: iconName });
  const text = createEl('span', [], {}, message);
  
  toast.append(icon, text);
  
  toast.addEventListener('click', () => {
    toast.style.animation = 'slideOut 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  });

  container.appendChild(toast);

  setTimeout(() => {
    if (toast.parentNode) {
      toast.style.animation = 'slideOut 0.3s ease forwards';
      setTimeout(() => toast.remove(), 300);
    }
  }, duration);
}

// Check Backend Health
async function checkBackendStatus() {
  const statusBadge = document.getElementById("status-badge");
  try {
    const data = await apiFetch("/health");
    if (data && data.status === "ok") {
      statusBadge.className = "api-status-badge online";
      statusBadge.querySelector(".status-text").textContent = "Online";
    }
  } catch (err) {
    statusBadge.className = "api-status-badge offline";
    statusBadge.querySelector(".status-text").textContent = "Offline";
    console.error("Backend health check failed:", err.message);
  }
}

// Update Active Downloads Count Badge
function updateDownloadsBadge() {
  let activeCount = 0;
  for (const [id, record] of state.downloads.entries()) {
    if (record.status === "downloading" || record.status === "preparing" || record.status === "queued") {
      activeCount++;
    }
  }
  
  if (activeCount > 0) {
    downloadsBadge.textContent = activeCount;
    downloadsBadge.style.display = "flex";
  } else {
    downloadsBadge.style.display = "none";
  }
}

// Fetch Genres and populate Filter Toolbar select box
async function loadGenres() {
  try {
    const genres = await apiFetch("/genres");
    state.genres = genres || [];
    populateGenreFilter(state.genres);
    return state.genres;
  } catch (err) {
    console.error("Error loading genres:", err.message);
    return [];
  }
}

// Populate Genre Selector in Grid View sticky toolbar
function populateGenreFilter(genres) {
  const select = document.getElementById("genre-filter");
  if (!select) return;
  select.innerHTML = '<option value="">Todos los Géneros</option>';
  genres.forEach(g => {
    const opt = document.createElement("option");
    opt.value = g.slug;
    opt.textContent = g.name;
    select.appendChild(opt);
  });
}

// Helper to create genre rows on homepage dynamically
function createGenreCarouselRow(genre) {
  const container = document.getElementById("carousel-section-container");
  if (!container) return null;
  
  const row = createEl("div", ["carousel-row"]);
  const title = createEl("h2", [], {}, `Películas de ${genre.name}`);
  const carouselContainer = createEl("div", ["carousel-container"], { id: `carousel-genre-${genre.slug}` });
  
  // Show skeletons initially
  showCarouselSkeletons(carouselContainer);
  
  row.append(title, carouselContainer);
  container.appendChild(row);
  
  return carouselContainer;
}

// --- Home Screen Parallel Load and Setup ---
async function loadHomepage() {
  const moviesCarousel = document.getElementById("carousel-movies");
  const seriesCarousel = document.getElementById("carousel-series");
  const animeCarousel = document.getElementById("carousel-anime");
  
  // Show skeletons initially
  showCarouselSkeletons(moviesCarousel);
  showCarouselSkeletons(seriesCarousel);
  showCarouselSkeletons(animeCarousel);
  
  // Clear any dynamic rows from previous loads
  const dynamicRows = document.querySelectorAll("#carousel-section-container .carousel-row");
  dynamicRows.forEach((row, idx) => {
    if (idx >= 3) row.remove(); // keep original 3
  });

  try {
    // Parallel fetching of top-level catalogs to maximize speed
    const [moviesData, seriesData, animeData] = await Promise.all([
      apiFetch("/catalog?type=movie").catch(() => null),
      apiFetch("/catalog?type=series").catch(() => null),
      apiFetch("/catalog?type=anime").catch(() => null)
    ]);
    
    const movies = moviesData ? (moviesData.items || []) : [];
    const series = seriesData ? (seriesData.items || []) : [];
    const anime = animeData ? (animeData.items || []) : [];
    
    renderCarousel(moviesCarousel, movies, "movie");
    renderCarousel(seriesCarousel, series, "series");
    renderCarousel(animeCarousel, anime, "anime");
    
    // Setup Hero Banner with Auto-Rotation
    const allHeroCandidates = [...movies, ...series].filter(i => parseFloat(i.rating) >= 7.5);
    setupHeroRotation(allHeroCandidates.length > 0 ? allHeroCandidates : movies);

    // Parallel load dynamic carousels for the 2 most popular genres to look rich
    const genres = state.genres.length > 0 ? state.genres : await loadGenres();
    const popularGenres = genres.slice(0, 2);

    popularGenres.forEach(async (genre) => {
      const carouselContainer = createGenreCarouselRow(genre);
      if (carouselContainer) {
        try {
          const data = await apiFetch(`/catalog?type=movie&genre=${genre.slug}`);
          renderCarousel(carouselContainer, data.items || [], "movie");
        } catch (genreErr) {
          console.error(`Error loading genre carousel for ${genre.name}:`, genreErr.message);
          carouselContainer.innerHTML = `<p style="padding: 20px; color: var(--text-muted);">No se pudieron cargar películas para este género.</p>`;
        }
      }
    });

  } catch (err) {
    console.error("Error loading home page catalog data:", err.message);
    showToast("Error al obtener catálogo de inicio", "error");
  }
}

function showCarouselSkeletons(container) {
  container.innerHTML = "";
  const skeletonRow = createEl("div", ["skeleton-row"]);
  for (let i = 0; i < 6; i++) {
    skeletonRow.appendChild(createEl("div", ["skeleton-card"]));
  }
  container.appendChild(skeletonRow);
}

function renderCarousel(container, items, type) {
  container.innerHTML = "";
  if (items.length === 0) {
    container.innerHTML = `
      <div class="downloads-empty" style="padding: 20px;">
        <ion-icon name="alert-circle-outline"></ion-icon>
        <p>No hay contenidos disponibles en Cypher.</p>
      </div>
    `;
    return;
  }
  
  items.forEach((item) => {
    const card = createEl("div", ["media-card", "carousel-card"]);
    
    const posterWrapper = createEl("div", ["poster-wrapper"]);
    const posterSrc = item.poster || "https://www.pelisplushd.la/static/img/favicon.png";
    const img = createEl("img", ["poster-img"], { 
      src: posterSrc, 
      alt: item.title, 
      loading: "lazy" 
    });
    
    img.onerror = () => {
      img.onerror = null;
      img.src = "/favicon.png";
    };

    const overlay = createEl("div", ["poster-overlay"]);
    const icon = createEl("ion-icon", ["overlay-icon"], { name: "play-circle-sharp" });
    overlay.appendChild(icon);

    posterWrapper.appendChild(img);
    posterWrapper.appendChild(overlay);

    if (item.rating) {
      const rating = createEl("span", ["rating-badge"]);
      const star = createEl("ion-icon", [], { name: "star" });
      rating.appendChild(star);
      rating.appendChild(document.createTextNode(item.rating));
      posterWrapper.appendChild(rating);
    }

    const typeBadge = createEl("span", ["type-badge"], {}, type);
    posterWrapper.appendChild(typeBadge);

    const info = createEl("div", ["media-info"]);
    const titleText = item.title.replace("VER ", "").replace(" Online Gratis HD", "");
    const title = createEl("h3", [], {}, titleText);
    
    const typeLabel = type === "movie" ? "Película" : type === "series" ? "Serie" : "Anime";
    const subtitle = createEl("p", [], {}, typeLabel);

    info.appendChild(title);
    info.appendChild(subtitle);

    card.appendChild(posterWrapper);
    card.appendChild(info);

    card.addEventListener("click", () => {
      loadMediaDetails(item);
    });

    container.appendChild(card);
  });
}

// Setup Dynamic Featured Hero Banner
async function setupHeroBanner(item) {
  const heroBanner = document.getElementById("hero-banner");
  const heroTitle = document.getElementById("hero-title");
  const heroSynopsis = document.getElementById("hero-synopsis");
  const heroRating = document.getElementById("hero-rating");
  const heroYear = document.getElementById("hero-year");
  const heroType = document.getElementById("hero-type");
  const heroPlayBtn = document.getElementById("hero-play-btn");
  const heroInfoBtn = document.getElementById("hero-info-btn");
  
  // Set basic data first
  const cleanTitle = item.title.replace("VER ", "").replace(" Online Gratis HD", "");
  heroTitle.textContent = cleanTitle;
  heroRating.textContent = item.rating || "N/A";
  heroType.textContent = item.type === "movie" ? "PELÍCULA RECOMENDADA" : item.type === "series" ? "SERIE RECOMENDADA" : "ANIME RECOMENDADO";
  
  if (item.poster) {
    heroBanner.style.backgroundImage = `url('${item.poster}')`;
  }
  
  // Bind actions
  heroPlayBtn.onclick = () => {
    loadMediaDetails(item);
  };
  heroInfoBtn.onclick = () => {
    loadMediaDetails(item);
  };
  
  // Fetch details in the background to show rich info
  try {
    const fullInfo = await apiFetch(`/info/${item.slug}?type=${item.type}&provider=${item.provider || 'pelisplus'}`);
    if (fullInfo) {
      if (fullInfo.synopsis) {
        heroSynopsis.textContent = fullInfo.synopsis;
      }
      if (fullInfo.year) {
        heroYear.textContent = fullInfo.year;
      }
      if (fullInfo.rating) {
        heroRating.textContent = fullInfo.rating;
      }
    }
  } catch (err) {
    console.error("Error loading detailed background info for Hero:", err);
  }
}

// Hero Banner System with Auto-Rotation & Dots
function setupHeroRotation(items) {
  if (!items || items.length === 0) return;
  
  // Take up to 5 top items
  state.heroItems = items.slice(0, 5);
  state.heroIndex = 0;
  
  // Setup dots indicators UI
  const dotsContainer = document.getElementById("hero-dots");
  if (dotsContainer) {
    dotsContainer.innerHTML = "";
    state.heroItems.forEach((_, idx) => {
      const dot = createEl("div", ["hero-dot"]);
      if (idx === 0) dot.classList.add("active");
      dot.addEventListener("click", () => {
        if (state.heroInterval) clearInterval(state.heroInterval);
        state.heroIndex = idx;
        transitionHero(state.heroItems[idx]);
        startHeroInterval();
      });
      dotsContainer.appendChild(dot);
    });
  }

  // Load first item
  setupHeroBanner(state.heroItems[0]);
  
  // Start rotation timer
  startHeroInterval();
}

function startHeroInterval() {
  if (state.heroInterval) clearInterval(state.heroInterval);
  state.heroInterval = setInterval(() => {
    state.heroIndex = (state.heroIndex + 1) % state.heroItems.length;
    transitionHero(state.heroItems[state.heroIndex]);
  }, 8000);
}

function transitionHero(item) {
  const heroBanner = document.getElementById("hero-banner");
  if (!heroBanner) return;
  
  // Apply quick fade out transition
  heroBanner.style.opacity = "0.3";
  setTimeout(() => {
    setupHeroBanner(item);
    
    // Sync dots UI active classes
    const dots = document.querySelectorAll(".hero-dot");
    dots.forEach((dot, idx) => {
      if (idx === state.heroIndex) dot.classList.add("active");
      else dot.classList.remove("active");
    });
    
    heroBanner.style.opacity = "1";
  }, 350);
}

// Search Scraper Aggregator
async function performSearch(query) {
  showGridView(`Búsqueda: "${query}"`);
  showResultsLoading(true);
  resultsEmpty.classList.add("hidden");
  resultsGrid.classList.add("hidden");
  document.getElementById("scroll-sentinel").classList.add("hidden");
  
  try {
    // Search is handled automatically by provider aggregations in backend routes
    const items = await apiFetch(`/search?s=${encodeURIComponent(query)}`);
    
    state.results = items || [];
    state.hasNextPage = false; // Search doesn't paginate infinite scroll
    
    // Client-side filters
    const filtered = applyClientFilters(state.results);
    displayResults(filtered);
  } catch (err) {
    showResultsError(err.message);
  } finally {
    showResultsLoading(false);
  }
}

// Client-Side Filters for Rating and Sort Orders
function applyClientFilters(items) {
  let filtered = [...items];
  
  // 1. Rating Minimum Filter
  if (state.filters.minRating > 0) {
    filtered = filtered.filter(item => {
      const r = parseFloat(item.rating);
      return !isNaN(r) && r >= state.filters.minRating;
    });
  }
  
  // 2. Sorting Rules
  switch (state.filters.sortBy) {
    case 'rating-desc':
      filtered.sort((a, b) => (parseFloat(b.rating) || 0) - (parseFloat(a.rating) || 0));
      break;
    case 'rating-asc':
      filtered.sort((a, b) => (parseFloat(a.rating) || 0) - (parseFloat(b.rating) || 0));
      break;
    case 'title-asc':
      filtered.sort((a, b) => a.title.localeCompare(b.title));
      break;
    case 'title-desc':
      filtered.sort((a, b) => b.title.localeCompare(a.title));
      break;
  }
  
  return filtered;
}

// Load Advanced Filtered Catalog (Server/Client Hybrid)
async function loadFilteredCatalog(resetPage = true) {
  if (resetPage) {
    state.currentPage = 1;
    resultsGrid.innerHTML = "";
    document.getElementById("scroll-sentinel").classList.add("hidden");
  }
  
  showResultsLoading(resetPage);
  state.isLoadingMore = true;
  
  const type = state.filters.type;
  const genre = state.filters.genre;
  
  try {
    let allItems = [];
    let hasNext = false;

    // If type is 'all' and no genre selected, parallel fetch all catalogs to merge
    if (type === 'all' && !genre) {
      const [movies, series, anime] = await Promise.all([
        apiFetch(`/catalog?type=movie&page=${state.currentPage}`).catch(() => null),
        apiFetch(`/catalog?type=series&page=${state.currentPage}`).catch(() => null),
        apiFetch(`/catalog?type=anime&page=${state.currentPage}`).catch(() => null)
      ]);
      
      allItems = [
        ...(movies?.items || []),
        ...(series?.items || []),
        ...(anime?.items || [])
      ];
      
      hasNext = (movies?.hasNextPage || series?.hasNextPage || anime?.hasNextPage) || false;
    } else {
      // Standard single catalog call
      const actualType = type === 'all' ? 'movie' : type;
      const data = await apiFetch(`/catalog?type=${actualType}&genre=${genre}&page=${state.currentPage}`);
      
      allItems = data ? (data.items || []) : [];
      hasNext = data ? (data.hasNextPage || false) : false;
    }
    
    state.hasNextPage = hasNext;
    state.results = resetPage ? allItems : [...state.results, ...allItems];
    
    // Apply Client filters (rating, sort)
    const filtered = applyClientFilters(state.results);
    displayResults(filtered);
    
    // Manage Infinite scroll sentinel visibility
    const sentinel = document.getElementById("scroll-sentinel");
    if (state.hasNextPage) {
      sentinel.classList.remove("hidden");
    } else {
      sentinel.classList.add("hidden");
    }
    
  } catch (err) {
    showResultsError(err.message);
  } finally {
    showResultsLoading(false);
    state.isLoadingMore = false;
  }
}

// Setup Intersection Observer Infinite Scroll Sentinel
function setupInfiniteScroll() {
  const sentinel = document.getElementById("scroll-sentinel");
  if (!sentinel) return;
  
  const observer = new IntersectionObserver((entries) => {
    const entry = entries[0];
    if (entry.isIntersecting && state.hasNextPage && !state.isLoadingMore) {
      state.currentPage++;
      loadFilteredCatalog(false); // append data
    }
  }, { threshold: 0.1 });
  
  observer.observe(sentinel);
}

// Show/Hide results loading
function showResultsLoading(show) {
  if (show) {
    resultsLoading.classList.remove("hidden");
  } else {
    resultsLoading.classList.add("hidden");
  }
}

// Show search errors
function showResultsError(message) {
  resultsGrid.classList.add("hidden");
  resultsEmpty.classList.remove("hidden");
  resultsEmpty.querySelector("p").textContent = `Error: ${message}. Servidor no disponible.`;
  resultsEmpty.querySelector("ion-icon").name = "warning-outline";
  resultsCount.textContent = "0 items";
  document.getElementById("scroll-sentinel").classList.add("hidden");
}

// Render Results Grid in Grid Section (Anti-XSS)
function displayResults(items = null) {
  const renderList = items !== null ? items : state.results;
  resultsGrid.innerHTML = "";
  
  resultsCount.textContent = `${renderList.length} items`;

  if (renderList.length === 0) {
    resultsGrid.classList.add("hidden");
    resultsEmpty.classList.remove("hidden");
    resultsEmpty.querySelector("p").textContent = "No se encontraron resultados para los filtros seleccionados.";
    resultsEmpty.querySelector("ion-icon").name = "alert-circle-outline";
    return;
  }

  resultsEmpty.classList.add("hidden");
  resultsGrid.classList.remove("hidden");

  renderList.forEach((item) => {
    const card = createEl("div", ["media-card"]);
    
    const posterWrapper = createEl("div", ["poster-wrapper"]);
    const posterSrc = item.poster || "https://www.pelisplushd.la/static/img/favicon.png";
    const img = createEl("img", ["poster-img"], { 
      src: posterSrc, 
      alt: item.title, 
      loading: "lazy" 
    });
    
    img.onerror = () => {
      img.onerror = null;
      img.src = "/favicon.png";
    };

    const overlay = createEl("div", ["poster-overlay"]);
    const icon = createEl("ion-icon", ["overlay-icon"], { name: "play-circle-sharp" });
    overlay.appendChild(icon);

    posterWrapper.appendChild(img);
    posterWrapper.appendChild(overlay);

    if (item.rating) {
      const rating = createEl("span", ["rating-badge"]);
      const star = createEl("ion-icon", [], { name: "star" });
      rating.appendChild(star);
      rating.appendChild(document.createTextNode(item.rating));
      posterWrapper.appendChild(rating);
    }

    const typeBadge = createEl("span", ["type-badge"], {}, item.type);
    posterWrapper.appendChild(typeBadge);

    const info = createEl("div", ["media-info"]);
    const titleText = item.title.replace("VER ", "").replace(" Online Gratis HD", "");
    const title = createEl("h3", [], {}, titleText);
    
    const typeLabel = item.type === "movie" ? "Película" : item.type === "series" ? "Serie" : "Anime";
    const subtitle = createEl("p", [], {}, typeLabel);

    info.appendChild(title);
    info.appendChild(subtitle);

    card.appendChild(posterWrapper);
    card.appendChild(info);

    card.addEventListener("click", () => {
      loadMediaDetails(item);
    });

    resultsGrid.appendChild(card);
  });
}

// Load Details in Modal
async function loadMediaDetails(item) {
  detailsEmpty.classList.add("hidden");
  detailsContent.classList.add("hidden");
  seasonsSection.classList.add("hidden");
  serversContainer.innerHTML = "";
  
  // Show glass modal instantly
  detailsPanel.classList.remove("hidden");

  try {
    // Show static info parsed from item card first for speedy feedback
    detailsPoster.src = item.poster || "https://www.pelisplushd.la/static/img/favicon.png";
    detailsTitle.textContent = item.title.replace("VER ", "").replace(" Online Gratis HD", "");
    detailsOriginalTitle.textContent = item.slug;
    detailsType.textContent = item.type === "movie" ? "PELICULA" : "SERIE";
    detailsYear.textContent = "Cargando...";
    detailsRating.textContent = item.rating || "N/A";
    
    detailsContent.classList.remove("hidden");

    // Fetch full scraping details from backend (auto-aggregation handles providers)
    const fullInfo = await apiFetch(`/info/${item.slug}?type=${item.type}&provider=${item.provider || ''}`);
    if (!fullInfo) return;
    state.selectedMedia = fullInfo;

    // Populate advanced fields
    detailsTitle.textContent = fullInfo.title;
    detailsOriginalTitle.textContent = fullInfo.originalTitle || fullInfo.slug;
    detailsYear.textContent = fullInfo.year || "N/A";
    detailsRating.textContent = fullInfo.rating || "N/A";
    detailsSynopsis.textContent = fullInfo.synopsis || "Sin sinopsis disponible.";
    
    detailsDirector.textContent = "";
    const strong = createEl("strong", [], {}, "Director: ");
    detailsDirector.append(strong, document.createTextNode(fullInfo.directors.join(", ") || "Desconocido"));

    // Genres Tags
    detailsGenres.innerHTML = "";
    if (fullInfo.genres && fullInfo.genres.length > 0) {
      fullInfo.genres.forEach((genre) => {
        const tag = createEl("span", ["genre-tag"], {}, genre.name);
        detailsGenres.appendChild(tag);
      });
    }

    if (fullInfo.type === "movie") {
      seasonsSection.classList.add("hidden");
      serversSectionTitle.textContent = "Servidores de Reproducción (Película)";
      renderServers(fullInfo.servers || []);
    } else {
      // It's a Series / Anime
      seasonsSection.classList.remove("hidden");
      serversSectionTitle.textContent = "Servidores del Capítulo";
      renderSeasonsTabs(fullInfo.seasons || []);
    }
  } catch (err) {
    showToast(`Error al obtener detalles: ${err.message}`, 'error');
  }
}

// Render Seasons Tabs
function renderSeasonsTabs(seasons = []) {
  seasonsTabsContainer.innerHTML = "";
  episodesContainer.innerHTML = "";

  if (seasons.length === 0) {
    seasonsSection.classList.add("hidden");
    return;
  }

  seasons.forEach((season, index) => {
    const tab = document.createElement("button");
    tab.className = `season-tab ${index === 0 ? "active" : ""}`;
    tab.textContent = season.name || `Temp ${season.number}`;
    
    tab.addEventListener("click", () => {
      seasonsTabsContainer.querySelectorAll(".season-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.activeSeason = season.number;
      renderEpisodes(season.episodes || []);
    });

    seasonsTabsContainer.appendChild(tab);
  });

  // Render first season by default
  state.activeSeason = seasons[0].number;
  renderEpisodes(seasons[0].episodes || []);
}

// Render Episode Buttons
function renderEpisodes(episodes = []) {
  episodesContainer.innerHTML = "";

  episodes.forEach((episode, index) => {
    const btn = document.createElement("button");
    btn.className = `episode-btn ${index === 0 ? "active" : ""}`;
    btn.textContent = episode.number;

    btn.addEventListener("click", () => {
      episodesContainer.querySelectorAll(".episode-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.activeEpisode = episode.number;
      fetchEpisodeServers(episode);
    });

    episodesContainer.appendChild(btn);
  });

  // Fetch servers for first episode by default
  if (episodes.length > 0) {
    state.activeEpisode = episodes[0].number;
    fetchEpisodeServers(episodes[0]);
  }
}

// Fetch Servers for Episode
async function fetchEpisodeServers(episode) {
  serversContainer.innerHTML = "";
  serversLoading.classList.remove("hidden");

  try {
    const data = await apiFetch(`/servers?slug=${state.selectedMedia.slug}&season=${state.activeSeason}&episode=${episode.number}&provider=${state.selectedMedia.provider || ''}`);
    renderServers(data.servers || [], episode.url);
  } catch (err) {
    const errorDiv = createEl("div", ["downloads-empty"]);
    const errorText = createEl("p", [], {}, `Error al obtener servidores: ${err.message}`);
    errorDiv.appendChild(errorText);
    serversContainer.appendChild(errorDiv);
  } finally {
    serversLoading.classList.add("hidden");
  }
}

// Render Servers list rows (Watch-First / Deprioritized Downloads)
function renderServers(servers = [], contentUrl = null) {
  serversContainer.innerHTML = "";

  if (servers.length === 0) {
    serversContainer.innerHTML = `
      <div class="downloads-empty">
        <ion-icon name="alert-circle-outline"></ion-icon>
        <p>No hay servidores disponibles en Cypher.</p>
      </div>
    `;
    return;
  }

  const mediaUrl = contentUrl || state.selectedMedia.url;
  // Standard Watch-Only if provider is Cuevana3 (doesn't support direct download API)
  const isWatchOnly = state.selectedMedia.provider === "cuevana3";

  // Prioritize "Latino" / "Español Latino" servers
  const sortedServers = [...servers].sort((a, b) => {
    const aLat = (a.language || "").toLowerCase().includes("latino");
    const bLat = (b.language || "").toLowerCase().includes("latino");
    if (aLat && !bLat) return -1;
    if (!aLat && bLat) return 1;
    return 0;
  });

  sortedServers.forEach((server) => {
    const isLatino = (server.language || "").toLowerCase().includes("latino");
    const row = createEl("div", ["server-row"]);
    if (isLatino) row.classList.add("latino-highlight");

    const nameGroup = createEl("div", ["server-name-group"]);
    const logo = createEl("ion-icon", ["server-logo"], { name: "play-circle" });
    if (isLatino) logo.classList.add("latino-icon");

    const info = createEl("div", ["server-info"]);
    const title = createEl("span", ["server-title"], {}, server.name + " ");
    if (isLatino) {
      const badge = createEl("span", ["latino-badge"], {}, "Latino");
      title.appendChild(badge);
    }
    const lang = createEl("span", ["server-lang"], {}, server.language);
    info.append(title, lang);
    nameGroup.append(logo, info);

    const actions = createEl("div", ["server-actions"]);
    const playBtn = createEl("button", ["btn-action", "play"], { title: "Ver ahora en el reproductor integrado" });
    const playIcon = createEl("ion-icon", [], { name: "play-outline" });
    playBtn.append(playIcon, document.createTextNode(" Ver"));
    actions.appendChild(playBtn);

    // Only render download action if not Cuevana3 (watch only)
    if (!isWatchOnly) {
      const downloadBtn = createEl("button", ["btn-action", "download"], { title: "Descargar video en segundo plano" });
      const downloadIcon = createEl("ion-icon", [], { name: "cloud-download-outline" });
      downloadBtn.appendChild(downloadIcon);
      actions.appendChild(downloadBtn);
      
      downloadBtn.addEventListener("click", () => {
        startDownloadJob(mediaUrl, server.language, server.server);
      });
    }
    row.append(nameGroup, actions);

    // Action: Play Embed in Integrated Player
    playBtn.addEventListener("click", () => {
      playEmbedVideo(server.embedUrl, server.name, server.language);
    });

    serversContainer.appendChild(row);
  });
}

// Play embed video in the integrated player
function playEmbedVideo(embedUrl, serverName, language) {
  const title = state.selectedMedia.title;
  const episodeLabel = state.selectedMedia.type === "series" || state.selectedMedia.type === "anime"
    ? ` — Temp ${state.activeSeason} Cap ${state.activeEpisode}`
    : "";
  playerTitle.textContent = `Reproduciendo: ${title}${episodeLabel} — ${serverName.toUpperCase()} (${language})`;
  playerIframe.src = embedUrl;
  videoPlayerContainer.classList.remove("hidden");
  
  // Smooth scroll video player container into view inside modal
  videoPlayerContainer.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Start Download Job via API POST
async function startDownloadJob(mediaUrl, language, serverToken) {
  try {
    const response = await apiFetch("/download", {
      method: "POST",
      body: JSON.stringify({
        url: mediaUrl,
        variant: language,
        preferredServer: serverToken
      })
    });

    const downloadId = response.downloadId;
    registerDownloadProgressCard(downloadId, mediaUrl, serverToken);
    
    // Automatically open downloads drawer to show progress
    downloadsDrawer.classList.add("open");
    drawerBackdrop.classList.add("open");
    
    showToast("¡Descarga iniciada con éxito!", "success");
  } catch (err) {
    showToast(`Error al iniciar descarga: ${err.message}`, "error");
  }
}

// Register Download Task Card & Start Polling
function registerDownloadProgressCard(id, url, server) {
  // Show / Hide empty downloads state
  downloadsEmpty.classList.add("hidden");

  // Create card DOM
  const card = createEl("div", ["download-card"], { id: `dl-card-${id}` });

  const header = createEl("div", ["download-card-header"]);
  const titleGroup = createEl("div", ["download-title-group"]);
  const slugText = url.split("/").pop() || "content";
  const title = createEl("span", ["download-card-title"], {}, slugText);
  const meta = createEl("span", ["download-card-meta"], {}, `Server: ${server.toUpperCase()}`);
  titleGroup.append(title, meta);
  
  const badge = createEl("span", ["status-pill", "queued"], { id: `dl-badge-${id}` }, "Queued");
  header.append(titleGroup, badge);

  const progressContainer = createEl("div", ["progress-container"]);
  const progressWrapper = createEl("div", ["progress-bar-wrapper"]);
  const progressFill = createEl("div", ["progress-bar-fill"], { id: `dl-fill-${id}`, style: "width: 0%" });
  progressWrapper.appendChild(progressFill);
  
  const progressPct = createEl("span", ["progress-percent"], { id: `dl-pct-${id}` }, "0%");
  progressContainer.append(progressWrapper, progressPct);

  const footer = createEl("div", ["download-card-footer"]);
  const footerText = createEl("span", [], { id: `dl-speed-${id}` }, "Preparando...");
  const actionContainer = createEl("div", [], { id: `dl-action-container-${id}` });
  footer.append(footerText, actionContainer);

  card.append(header, progressContainer, footer);
  downloadsContainer.insertBefore(card, downloadsContainer.firstChild);

  // Set in state Map
  state.downloads.set(id, { id, status: "queued", progress: 0 });

  // Update navbar active count badge
  updateDownloadsBadge();

  // Start Real-time Polling with Exponential Backoff
  let delay = 2500;
  const maxDelay = 30000; // 30 seconds max interval
  let attempts = 0;
  const maxAttempts = 480; // ~20 minutes maximum before timeout

  function scheduleNextPoll() {
    if (attempts >= maxAttempts) {
      updateDownloadUI(id, { status: "failed", error: "Timeout: Tiempo de sondeo excedido" });
      return;
    }

    const timeoutId = setTimeout(async () => {
      try {
        const data = await apiFetch(`/download/${id}`);
        if (!data) {
          delay = Math.min(delay * 2, maxDelay);
          attempts++;
          scheduleNextPoll();
          return;
        }

        updateDownloadUI(id, data);

        if (data.status === "completed" || data.status === "failed") {
          return;
        }

        // Backoff factor 1.5
        delay = Math.min(delay * 1.5, maxDelay);
        attempts++;
        scheduleNextPoll();
      } catch (err) {
        delay = Math.min(delay * 2, maxDelay);
        attempts++;
        scheduleNextPoll();
      }
    }, delay);

    state.pollingIntervals.set(id, timeoutId);
  }

  scheduleNextPoll();
}

// Update Download UI Elements
function updateDownloadUI(id, data) {
  const badge = document.getElementById(`dl-badge-${id}`);
  const fill = document.getElementById(`dl-fill-${id}`);
  const pctText = document.getElementById(`dl-pct-${id}`);
  const footerText = document.getElementById(`dl-speed-${id}`);
  const actionContainer = document.getElementById(`dl-action-container-${id}`);

  if (!badge) {
    const timeoutId = state.pollingIntervals.get(id);
    if (timeoutId) clearTimeout(timeoutId);
    state.pollingIntervals.delete(id);
    return;
  }

  state.downloads.set(id, data);

  badge.className = `status-pill ${data.status}`;
  badge.textContent = data.status;

  fill.style.width = `${data.progress}%`;
  pctText.textContent = `${data.progress}%`;

  if (data.status === "downloading") {
    footerText.textContent = `Descargando desde: ${data.currentServer ? data.currentServer.toUpperCase() : "..."}`;
  } else if (data.status === "preparing") {
    footerText.textContent = `Resolviendo: ${data.currentServer ? data.currentServer.toUpperCase() : "..."}`;
  } else if (data.status === "completed") {
    footerText.textContent = `¡Completado! ${(Number(data.fileSize || 0) / (1024 * 1024)).toFixed(1)} MB`;
    
    const timeoutId = state.pollingIntervals.get(id);
    if (timeoutId) clearTimeout(timeoutId);
    state.pollingIntervals.delete(id);

    actionContainer.innerHTML = "";
    const saveBtn = createEl("a", ["btn-download-file"], { 
      href: data.downloadUrl, 
      download: "" 
    });
    const saveIcon = createEl("ion-icon", [], { name: "download-outline" });
    saveBtn.append(saveIcon, document.createTextNode(" Guardar"));
    actionContainer.appendChild(saveBtn);
    
    // Play complete toast
    showToast("¡Descarga de archivo finalizada!", "success");
  } else if (data.status === "failed") {
    footerText.textContent = `Error: ${data.error || "Desconocido"}`;
    footerText.style.color = "var(--danger-color)";
    
    const timeoutId = state.pollingIntervals.get(id);
    if (timeoutId) clearTimeout(timeoutId);
    state.pollingIntervals.delete(id);
  }

  // Always refresh badge count when state updates
  updateDownloadsBadge();
}

// Remove download card from UI
function removeDownloadCard(id) {
  const timeoutId = state.pollingIntervals.get(id);
  if (timeoutId) clearTimeout(timeoutId);
  
  const card = document.getElementById(`dl-card-${id}`);
  if (card) {
    card.remove();
  }
  
  state.downloads.delete(id);
  state.pollingIntervals.delete(id);

  if (state.downloads.size === 0) {
    downloadsEmpty.classList.remove("hidden");
  }

  // Refresh active count badge
  updateDownloadsBadge();
}
