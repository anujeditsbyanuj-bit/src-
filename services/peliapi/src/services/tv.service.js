/**
 * TV en vivo Service
 * Proveedor de canales de TV, iptv y streams de YouTube.
 */

const channels = [
  // Noticias / General (YouTube)
  {
    id: "yt-dw-espanol",
    name: "DW Español (Noticias)",
    category: "Noticias",
    type: "youtube",
    url: "W3VnbFv0oAE", // YouTube ID, they often change live streams so this is an example, but we can also put a permanent one or use a channel ID
    logo: "https://yt3.googleusercontent.com/ytc/AIdro_k4qZ2n6oErzFqjX6rZqG-_p4z9Q8w2a4z0b_f01A=s176-c-k-c0x00ffffff-no-rj"
  },
  {
    id: "yt-adn40",
    name: "ADN 40 En Vivo",
    category: "Noticias",
    type: "youtube",
    url: "pQ-D7V6f6pY", // Usually ADN40 has a live stream 24/7
    logo: "https://yt3.googleusercontent.com/ytc/AIdro_lzG8W3H_H4O8M3D4E7h8V6X3D9W5N3D1V4r8b9qQ=s176-c-k-c0x00ffffff-no-rj"
  },
  {
    id: "yt-lofi-girl",
    name: "Lofi Girl (Música 24/7)",
    category: "Música",
    type: "youtube",
    url: "jfKfPfyJRdk",
    logo: "https://yt3.googleusercontent.com/ytc/AIdro_n8h2V9A9a7a9G3e6J5M1E4V8N3W2X5T1V9f7B8=s176-c-k-c0x00ffffff-no-rj"
  },
  
  // Canales Pluto TV (M3U8) - Estos son ejemplos, requerirían scraping dinámico idealmente para estar 100% frescos, pero los m3u8 directos suelen durar o se consiguen de listas IPTV estables.
  // Utilizaremos los de iptv-org que son muy estables.
  {
    id: "iptv-pluto-anime",
    name: "Pluto TV Anime",
    category: "Anime",
    type: "m3u8",
    url: "https://service-stitcher.clusters.pluto.tv/stitch/hls/channel/5d812bdcc3c1d4a8e6377b0d/master.m3u8?advertisingId=&appName=web&appVersion=unknown&appStoreUrl=&architecture=&buildVersion=&clientTime=0&deviceDNT=0&deviceId=unknown&deviceMake=web&deviceModel=web&deviceType=web&deviceVersion=unknown&includeExtendedEvents=false&sid=unknown&userId=",
    logo: "https://i.imgur.com/G5g27k8.png"
  },
  {
    id: "iptv-rtve-24h",
    name: "Canal 24 Horas (RTVE)",
    category: "Noticias",
    type: "m3u8",
    url: "https://ztnr.rtve.es/ztnr/1694255.m3u8",
    logo: "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/Canal_24_horas_2012.svg/512px-Canal_24_horas_2012.svg.png"
  },
  {
    id: "iptv-redbull",
    name: "Red Bull TV",
    category: "Deportes",
    type: "m3u8",
    url: "https://rbmn-live.akamaized.net/hls/live/590964/BoRB-AT/master.m3u8",
    logo: "https://upload.wikimedia.org/wikipedia/en/thumb/f/f5/Red_Bull_TV_logo.svg/1200px-Red_Bull_TV_logo.svg.png"
  },
  {
    id: "iptv-mileniotv",
    name: "Milenio Televisión",
    category: "Noticias",
    type: "m3u8",
    url: "https://mdstrm.com/live-stream-playlist/5a5e3b6d4b47c9085d7b5b5c.m3u8",
    logo: "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d4/Milenio_Televisi%C3%B3n.svg/1200px-Milenio_Televisi%C3%B3n.svg.png"
  },
  {
    id: "iptv-france24",
    name: "France 24 (Español)",
    category: "Noticias",
    type: "m3u8",
    url: "https://static.france24.com/live/F24_ES_HI_HLS/live_web.m3u8",
    logo: "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/France_24_logo.svg/512px-France_24_logo.svg.png"
  }
];

class TvService {
  /**
   * Obtiene la lista completa de canales en vivo.
   * A futuro, esto puede hacer un fetch a un repositorio IPTV en vivo.
   */
  async getLiveChannels() {
    return channels;
  }
}

module.exports = new TvService();
