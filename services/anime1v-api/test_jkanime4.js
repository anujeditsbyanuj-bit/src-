const axios = require('axios');
const cheerio = require('cheerio');

axios.get('https://jkanime.net/', {
  headers: {
    'User-Agent': 'Mozilla/5.0'
  }
}).then(r => {
  const $ = cheerio.load(r.data);
  console.log('Donghuas HTML:');
  console.log($('#donghuas').html());
}).catch(console.error);
