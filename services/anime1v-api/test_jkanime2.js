const axios = require('axios');
const cheerio = require('cheerio');

axios.get('https://jkanime.net/', {
  headers: {
    'User-Agent': 'Mozilla/5.0'
  }
}).then(r => {
  const $ = cheerio.load(r.data);
  console.log('Donghuas length:', $('#donghuas .anime__item').length);
  console.log('Ovas length:', $('#ovas .anime__item').length);
  
  // also check if there is a 'tipo' section for donghuas
  console.log('Donghua link:', $('a[href*="donghua"]').attr('href'));
}).catch(console.error);
