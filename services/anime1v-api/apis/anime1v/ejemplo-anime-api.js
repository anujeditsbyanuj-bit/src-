// Ejemplo de uso de la API de Anime1v
// API pública en: http://localhost:3001/api/v1/anime

const API_KEY = 'tu_api_key_aqui'; // Obtén tu API key en https://localhost:3001
const BASE_URL = 'http://localhost:3001/api/v1/anime';

// Función auxiliar para hacer requests
async function apiRequest(endpoint, options = {}) {
  const response = await fetch(`${BASE_URL}${endpoint}`, {
    ...options,
    headers: {
      'X-API-Key': API_KEY,
      'Content-Type': 'application/json',
      ...options.headers
    }
  });
  return response.json();
}

// ============================================
// 1. Buscar un anime
// ============================================
async function searchAnime(query) {
  console.log(`\n Buscando: ${query}`);
  
  const result = await apiRequest(
    `/search?q=${encodeURIComponent(query)}&domain=animeav1.com`
  );
  
  console.log(result);
  return result.data.results;
}

// ============================================
// 2. Obtener información completa del anime
// ============================================
async function getAnimeInfo(animeUrl) {
  console.log(`\n Obteniendo info de: ${animeUrl}`);
  
  const result = await apiRequest(
    `/info?url=${encodeURIComponent(animeUrl)}`
  );
  
  console.log(`Título: ${result.data.title}`);
  console.log(`Episodios: ${result.data.totalEpisodes}`);
  console.log(`Géneros: ${result.data.genres.join(', ')}`);
  
  return result.data;
}

// ============================================
// 3. Obtener enlaces de descarga de un episodio
// ============================================
async function getEpisodeLinks(episodeUrl) {
  console.log(`\n Obteniendo enlaces del episodio: ${episodeUrl}`);
  
  const result = await apiRequest(
    `/episode?url=${encodeURIComponent(episodeUrl)}`
  );
  
  console.log(`\nEnlaces de descarga disponibles:`);
  result.data.downloadLinks.forEach(link => {
    console.log(`- ${link.quality} (${link.size}) - ${link.server}: ${link.url}`);
  });
  
  return result.data;
}

// ============================================
// 4. Obtener enlaces con filtro de Mega
// ============================================
async function getEpisodeLinksWithoutMega(episodeUrl) {
  console.log(`\n Obteniendo enlaces (sin Mega): ${episodeUrl}`);
  
  // Por defecto Mega ya está excluido, pero puedes ser explícito
  const result = await apiRequest(
    `/episode?url=${encodeURIComponent(episodeUrl)}&includeMega=false`
  );
  
  console.log(`\nServidores SUB (sin Mega):`);
  result.data.servers.sub.forEach(server => {
    console.log(`- ${server.server}: ${server.url}`);
  });
  
  return result.data;
}

// ============================================
// 5. Incluir Mega explícitamente
// ============================================
async function getEpisodeLinksWithMega(episodeUrl) {
  console.log(`\n Obteniendo enlaces (con Mega): ${episodeUrl}`);
  
  const result = await apiRequest(
    `/episode?url=${encodeURIComponent(episodeUrl)}&includeMega=true`
  );
  
  console.log(`\nServidores SUB (con Mega):`);
  result.data.servers.sub.forEach(server => {
    console.log(`- ${server.server}: ${server.url}`);
  });
  
  return result.data;
}

// ============================================
// 6. Excluir múltiples servidores
// ============================================
async function getEpisodeLinksCustomFilter(episodeUrl, excludeServers = []) {
  console.log(`\n Obteniendo enlaces (filtro custom): ${episodeUrl}`);
  console.log(`Servidores excluidos: ${excludeServers.join(', ')}`);
  
  const params = new URLSearchParams({
    url: episodeUrl,
    excludeServers: excludeServers.join(',')
  });
  
  const result = await apiRequest(`/episode?${params}`);
  
  console.log(`\nServidores disponibles:`);
  result.data.servers.sub.forEach(server => {
    console.log(`- ${server.server}: ${server.url}`);
  });
  
  return result.data;
}

// ============================================
// EJEMPLO DE USO COMPLETO
// ============================================
async function ejemploCompleto() {
  try {
    // 1. Buscar anime
    console.log('='.repeat(50));
    const searchResults = await searchAnime('naruto');
    
    if (searchResults.length === 0) {
      console.log('No se encontraron resultados');
      return;
    }
    
    console.log(`\n Encontrados ${searchResults.length} resultados`);
    console.log(`Primer resultado: ${searchResults[0].title}`);
    
    // 2. Obtener info del primer resultado
    console.log('\n' + '='.repeat(50));
    const animeUrl = searchResults[0].url;
    const animeInfo = await getAnimeInfo(animeUrl);
    
    // 3. Obtener enlaces del primer episodio (sin Mega por defecto)
    console.log('\n' + '='.repeat(50));
    const firstEpisode = animeInfo.episodes[0];
    const episodeLinks = await getEpisodeLinks(firstEpisode.url);
    
    // 4. Obtener enlaces incluyendo Mega
    console.log('\n' + '='.repeat(50));
    const episodeLinksWithMega = await getEpisodeLinksWithMega(firstEpisode.url);
    
    // 5. Obtener enlaces con filtro personalizado
    console.log('\n' + '='.repeat(50));
    await getEpisodeLinksCustomFilter(firstEpisode.url, ['mega', 'fembed']);
    
    console.log('\n' + '='.repeat(50));
    console.log(' Ejemplo completo ejecutado exitosamente!');
    
  } catch (error) {
    console.error(' Error:', error.message);
  }
}

// ============================================
// EJEMPLO: Listar todos los episodios
// ============================================
async function listarTodosLosEpisodios(animeUrl) {
  try {
    console.log('='.repeat(50));
    console.log(' Listando todos los episodios...');
    
    // Obtener info del anime
    const animeInfo = await getAnimeInfo(animeUrl);
    
    console.log(`\n ${animeInfo.title}`);
    console.log(` Score: ${animeInfo.score}`);
    console.log(` Total de episodios: ${animeInfo.totalEpisodes}`);
    console.log(`\nEpisodios disponibles:`);
    
    animeInfo.episodes.forEach(ep => {
      console.log(`  ${ep.number}. ${ep.title || 'Episodio ' + ep.number}`);
      console.log(`     URL: ${ep.url}`);
    });
    
  } catch (error) {
    console.error(' Error:', error.message);
  }
}

// ============================================
// EJEMPLO: Comparar servidores con/sin Mega
// ============================================
async function compararServidores(episodeUrl) {
  try {
    console.log('='.repeat(50));
    console.log(' Comparando disponibilidad de servidores...\n');
    
    // Sin Mega (default)
    const sinMega = await apiRequest(
      `/episode?url=${encodeURIComponent(episodeUrl)}`
    );
    
    // Con Mega
    const conMega = await apiRequest(
      `/episode?url=${encodeURIComponent(episodeUrl)}&includeMega=true`
    );
    
    console.log(`Sin Mega: ${sinMega.data.servers.sub.length} servidores SUB`);
    sinMega.data.servers.sub.forEach(s => console.log(`  - ${s.server}`));
    
    console.log(`\nCon Mega: ${conMega.data.servers.sub.length} servidores SUB`);
    conMega.data.servers.sub.forEach(s => console.log(`  - ${s.server}`));
    
    const diferencia = conMega.data.servers.sub.length - sinMega.data.servers.sub.length;
    console.log(`\n Diferencia: ${diferencia} servidor(es) adicional(es) con Mega`);
    
  } catch (error) {
    console.error(' Error:', error.message);
  }
}

// ============================================
// EJECUTAR EJEMPLOS
// ============================================

// Descomenta la función que quieras probar:

// Opción 1: Ejemplo completo (buscar, obtener info, obtener enlaces)
// ejemploCompleto();

// Opción 2: Listar todos los episodios de un anime
// listarTodosLosEpisodios('https://animeav1.com/media/leviathan');

// Opción 3: Comparar servidores con y sin Mega
// compararServidores('https://animeav1.com/media/leviathan/1');

// Opción 4: Uso específico personalizado
/*
(async () => {
  // Buscar anime
  const results = await searchAnime('one piece');
  console.log(`Encontrados: ${results.length} animes`);
  results.slice(0, 5).forEach((anime, i) => {
    console.log(`${i + 1}. ${anime.title}`);
  });
  
  // Obtener info de un anime específico
  const info = await getAnimeInfo('https://animeav1.com/media/one-piece');
  console.log(`\n${info.title}`);
  console.log(`Géneros: ${info.genres.join(', ')}`);
  console.log(`Episodios: ${info.totalEpisodes}`);
  
  // Obtener enlaces de un episodio sin Mega
  const episodeLinks = await getEpisodeLinks(info.episodes[0].url);
  console.log(`\nServidores disponibles:`);
  episodeLinks.servers.sub.forEach(server => {
    console.log(`- ${server.server}`);
  });
})();
*/

console.log(`
╔════════════════════════════════════════════════╗
║       ANIME1V API - EJEMPLOS                ║
╚════════════════════════════════════════════════╝

 API Base URL: http://localhost:3001/api/v1/anime

Para usar esta API, necesitas:
1.  Registrarte en https://localhost:3001
2.  Obtener tu API Key Open Source
3.  Reemplazar 'tu_api_key_aqui' con tu key real

 Endpoints disponibles:
- GET  /search?q=nombre&domain=animeav1.com
- GET  /info?url=...
- GET  /episode?url=...

 Parámetros especiales:
- includeMega=true     → Incluir servidor Mega
- excludeServers=mega  → Excluir servidores específicos

 Rate Limiting:
- Plan Free: 100 requests/día
- Plan Premium: Próximamente
- Plan Enterprise: Próximamente

 Descomenta alguna función arriba para probar!
 Documentación: https://github.com/FxxMorgan/anime1v-api
`);

