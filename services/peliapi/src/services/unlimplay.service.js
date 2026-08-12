const axios = require("axios");
const { ApiError } = require("../utils/api-error");

const UNLIMPLAY_URL = "https://unlimplay.com/play/embed";

/**
 * Normaliza los nombres de los servidores de Unlimplay.
 */
function normalizeServerName(name) {
  if (!name) return "Desconocido";
  const lower = name.toLowerCase();
  if (lower.includes("vidhide")) return "Vidhide";
  if (lower.includes("streamwish")) return "Streamwish";
  if (lower.includes("voe")) return "Voe";
  if (lower.includes("doodstream")) return "DoodStream";
  if (lower.includes("streamtape")) return "Streamtape";
  if (lower.includes("filemoon")) return "Filemoon";
  if (lower.includes("netu") || lower.includes("waaw")) return "Netu";
  if (lower.includes("remux")) return "Remux";
  
  // Capitalize first letter
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/**
 * Convierte el JSON estructurado de Unlimplay en nuestra estructura de servidores.
 */
function parseUnlimplayEmbeds(embedsJson) {
  const result = {
    servers: [],
    sub: [],
    lat: [],
    cast: [],
  };

  if (!embedsJson || typeof embedsJson !== "object") return result;

  const processLinks = (group, targetArray, languageCode) => {
    if (!group) return;
    
    // El objeto tiene la forma { "streamwish": "url", "vidhide": "url", ... }
    for (const [serverKey, url] of Object.entries(group)) {
      if (typeof url !== "string" || !url.startsWith("http")) continue;
      
      const serverName = normalizeServerName(serverKey);
      
      const serverObj = {
        server: serverName,
        url: url,
        lang: languageCode
      };
      
      targetArray.push(serverObj);
      result.servers.push(serverObj);
    }
  };

  // Latino
  processLinks(embedsJson["latino"], result.lat, "LAT");
  // Subtitulado
  processLinks(embedsJson["subtitulado"], result.sub, "SUB");
  // Castellano / Español de España
  processLinks(embedsJson["español"] || embedsJson["espanol"], result.cast, "CAST");

  return result;
}

/**
 * Obtiene los servidores para una película o serie basándose en su TMDB ID.
 */
async function getServers(tmdbId, season, episode, isAnime = false) {
  if (!tmdbId) {
    throw new ApiError(400, "tmdbId es requerido para unlimplay");
  }

  // Si tiene temporada y episodio mayor a 0, se asume que es Serie o Anime.
  // Sin embargo, si es película, season y episode son ignorados o 0/1, pero dependerá de cómo se llame.
  const isSeries = (season > 0 && season !== null) && (episode > 0 && episode !== null);
  
  let targetUrl;
  if (isSeries) {
    targetUrl = `${UNLIMPLAY_URL}/tv/${tmdbId}/${season}/${episode}`;
  } else {
    targetUrl = `${UNLIMPLAY_URL}/movie/${tmdbId}`;
  }

  try {
    const response = await axios.get(targetUrl, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
      },
      timeout: 10000
    });

    const html = response.data;

    // Buscar el JSON incrustado: const EMBEDS = {...};
    const embedMatch = html.match(/const\s+EMBEDS\s*=\s*({.*?});/i);
    if (!embedMatch) {
      // Si no existe, podría significar que el TMDB ID no existe en la db de unlimplay o no está disponible aún
      console.warn(`[Unlimplay] No EMBEDS found for tmdbId ${tmdbId} (URL: ${targetUrl})`);
      return { servers: [], sub: [], lat: [], cast: [] };
    }

    const embedsData = JSON.parse(embedMatch[1]);
    
    return parseUnlimplayEmbeds(embedsData);

  } catch (error) {
    console.error(`[Unlimplay Error] tmdbId: ${tmdbId}, Url: ${targetUrl}, Error: ${error.message}`);
    throw new ApiError(500, "Error obteniendo datos de Unlimplay", error.message);
  }
}

/**
 * Para compatibilidad con el enrutador que pasa (slug, season, episode, overrideUrl)
 * Asumiremos que si recibe un tmdbId explícito, se pasará en otra parte o en slug si se formatea así.
 * En nuestro content.routes.js pasaremos el tmdbId de forma explícita.
 */
async function getEpisodeServers(tmdbId, season, episode) {
  // Para películas de peliapi, la season y episode suelen venir como 1 y 1
  // Para distinguir serie de película, necesitaremos saber si es serie (season/episode > 0).
  // Pelisplus usa season=1 y episode=1 por defecto si no vienen, por lo cual es un problema identificar si es movie o tv.
  // Pero lo manejaremos desde el content.routes.js pasándole parámetros precisos.
  
  // Vamos a usar un truco: Si viene season == 0, significa que es explícitamente Película
  const isActuallySeries = season > 0; 
  return getServers(tmdbId, season, episode, isActuallySeries);
}

module.exports = {
  getServers,
  getEpisodeServers
};
