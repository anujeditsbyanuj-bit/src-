const axios = require('axios');
const cheerio = require('cheerio');
const animeService = require('../src/services/anime.service');
const { extractVoe } = require('../src/utils/resolvers/voe.resolver');
const { extractStreamwish } = require('../src/utils/resolvers/streamwish.resolver');
const { extractStreamtape } = require('../src/utils/resolvers/streamtape.resolver');

const UA_FIREFOX = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0';

async function testLiveEpisode() {
  console.log('===================================================');
  console.log('🔥 INICIANDO PRUEBAS EN TIEMPO REAL CON EPISODIO RECIENTE');
  console.log('===================================================');

  let targetEpisodeUrl = '';

  try {
    console.log('1. Obteniendo la página principal para buscar el episodio más reciente...');
    const response = await axios.get('https://www4.animeflv.net/', {
      headers: { 'User-Agent': UA_FIREFOX },
      timeout: 10000
    });

    const $ = cheerio.load(response.data);
    
    // El primer enlace a /ver/ en el home es el episodio más fresco de hoy
    const latestPath = $('a[href^="/ver/"]').first().attr('href');

    if (!latestPath) {
      console.log('⚠️ No se pudo determinar el último episodio desde el home, usando fallback...');
      targetEpisodeUrl = 'https://www4.animeflv.net/ver/one-piece-tv-1';
    } else {
      targetEpisodeUrl = `https://www4.animeflv.net${latestPath}`;
      console.log(`✨ ¡Encontrado episodio fresquísimo! URL: ${targetEpisodeUrl}`);
    }
  } catch (err) {
    console.warn('⚠️ Error al cargar el home, usando One Piece como fallback:', err.message);
    targetEpisodeUrl = 'https://www4.animeflv.net/ver/one-piece-tv-1';
  }

  console.log(`\n2. Scrapeando enlaces de video del episodio...`);
  let episodeResponse;
  try {
    episodeResponse = await animeService.getEpisodeLinks(targetEpisodeUrl, true, '');
  } catch (err) {
    console.error('❌ Error al obtener enlaces del episodio:', err.message);
    return;
  }

  const servers = episodeResponse?.data?.servers?.sub || [];
  const downloads = episodeResponse?.data?.downloadLinks?.SUB || [];
  const allLinks = [...servers, ...downloads];

  if (allLinks.length === 0) {
    console.log('⚠️ No se encontraron enlaces para este episodio.');
    return;
  }

  console.log(`✨ Enlaces de servidores encontrados: ${allLinks.length}`);

  // Filtrar enlaces de interés
  const voeLinks = allLinks.filter(l => l.url.includes('voe'));
  const swLinks = allLinks.filter(l => l.url.includes('wish') || l.url.includes('playnix') || l.url.includes('medix') || l.url.includes('awish'));
  const stLinks = allLinks.filter(l => l.url.includes('tape'));

  console.log(`   - Enlaces VOE: ${voeLinks.length}`);
  console.log(`   - Enlaces Streamwish: ${swLinks.length}`);
  console.log(`   - Enlaces Streamtape: ${stLinks.length}`);

  // --- Probar VOE ---
  if (voeLinks.length > 0) {
    console.log('\n--- 🧪 Probando Resolutor VOE con Enlace Real ---');
    const link = voeLinks[0].url;
    console.log(`Embed URL: ${link}`);
    const start = Date.now();
    try {
      const res = await extractVoe(link);
      const elapsed = Date.now() - start;
      if (res) {
        console.log(`✅ ¡ÉXITO! Obtenido en ${elapsed}ms`);
        console.log(`🔗 URL Directa: ${res.slice(0, 100)}...`);
      } else {
        console.log(`❌ El resolutor devolvió vacío (enlace no activo o caída).`);
      }
    } catch (e) {
      console.error(`❌ Error en resolutor VOE:`, e.message);
    }
  } else {
    console.log('\n⚠️ No se encontró enlace de VOE activo para probar.');
  }

  // --- Probar Streamwish ---
  if (swLinks.length > 0) {
    console.log('\n--- 🧪 Probando Resolutor Streamwish con Enlace Real ---');
    const link = swLinks[0].url;
    console.log(`Embed URL: ${link}`);
    const start = Date.now();
    try {
      const res = await extractStreamwish(link);
      const elapsed = Date.now() - start;
      if (res) {
        console.log(`✅ ¡ÉXITO! Obtenido en ${elapsed}ms`);
        console.log(`🔗 URL Directa HLS: ${res.slice(0, 100)}...`);
      } else {
        console.log(`❌ El resolutor devolvió vacío.`);
      }
    } catch (e) {
      console.error(`❌ Error en resolutor Streamwish:`, e.message);
    }
  } else {
    console.log('\n⚠️ No se encontró enlace de Streamwish activo para probar.');
  }

  // --- Probar Streamtape ---
  if (stLinks.length > 0) {
    console.log('\n--- 🧪 Probando Resolutor Streamtape con Enlace Real ---');
    const link = stLinks[0].url;
    console.log(`Embed URL: ${link}`);
    const start = Date.now();
    try {
      const res = await extractStreamtape(link);
      const elapsed = Date.now() - start;
      if (res) {
        console.log(`✅ ¡ÉXITO! Obtenido en ${elapsed}ms`);
        console.log(`🔗 URL Directa MP4: ${res.slice(0, 100)}...`);
      } else {
        console.log(`❌ El resolutor devolvió vacío.`);
      }
    } catch (e) {
      console.error(`❌ Error en resolutor Streamtape:`, e.message);
    }
  } else {
    console.log('\n⚠️ No se encontró enlace de Streamtape activo para probar.');
  }

  console.log('\n===================================================');
  console.log('🏁 PRUEBAS DE INTEGRACIÓN FINALIZADAS');
  console.log('===================================================');
}

testLiveEpisode();
