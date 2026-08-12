const ds = require('../src/services/download.service');
const svc = require('../src/services/anime.service');
(async () => {
  try {
    const info = await svc.getAnimeInfo('https://www4.animeflv.net/anime/luck-and-logic');
    const episodes = info.data && info.data.episodes;
    const ep1 = episodes && (episodes.find(e => e.number === 1) || episodes[0]);
    if (!ep1 || !ep1.url) {
      console.error('No ep1 url');
      process.exit(1);
    }

    const recordMeta = ds.createDownload({ url: ep1.url, quality: '720p' }, 'http://localhost');
    console.log('created', recordMeta);
    const id = recordMeta.downloadId;

    for (let i = 0; i < 60; i++) {
      try {
        const s = ds.getDownload(id);
        console.log(new Date().toISOString(), s.status, s.progress, s.currentServer || '-', s.error || '-');
        if (s.status === 'completed' || s.status === 'failed') break;
      } catch (e) {
        console.error('getDownload error', e.message);
      }
      await new Promise(r => setTimeout(r, 2000));
    }
  } catch (err) {
    console.error('fatal', err.message || err);
    process.exit(1);
  }
})();
