const axios = require('axios');
const cheerio = require('cheerio');

axios.get('https://jkanime.net/', {
  headers: {
    'User-Agent': 'Mozilla/5.0'
  }
}).then(r => {
  const $ = cheerio.load(r.data);
  $('a').each((i, el) => {
    const text = $(el).text().trim().toLowerCase();
    if (text.includes('donghua') || text.includes('ova')) {
      console.log($(el).text().trim(), $(el).attr('href'));
    }
  });
}).catch(console.error);
