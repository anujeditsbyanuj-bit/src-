const axios = require('axios');
const cheerio = require('cheerio');

async function testUnlimplaySeries() {
  try {
    const res = await axios.get('https://unlimplay.com/play/embed/tv/5920/1/1');
    const $ = cheerio.load(res.data);
    const scripts = $('script').map((i, el) => $(el).html()).get().filter(s => s && (s.includes('server') || s.includes('source') || s.includes('EMBEDS')));
    console.log('Unlimplay Series status:', res.status);
    console.log('Title:', $('title').text());
    console.log('Scripts:', scripts.length);
    if (scripts.length > 0) {
      console.log(scripts[0].substring(0, 500));
    }
  } catch (error) {
    console.error('Unlimplay Series error:', error.message);
  }
}

testUnlimplaySeries();
