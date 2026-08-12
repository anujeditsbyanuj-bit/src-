const axios = require('axios');
const cheerio = require('cheerio');

axios.get('https://jkanime.net/', {
  headers: {
    'User-Agent': 'Mozilla/5.0'
  }
}).then(r => {
  const $ = cheerio.load(r.data);
  const ids = [];
  $('div[id]').each((i, el) => {
    ids.push($(el).attr('id'));
  });
  console.log(ids.filter(id => id.includes('donghua') || id.includes('ova')));
  
  // They probably use class names instead of IDs for the tabs content
  const classes = [];
  $('.anime__item').each((i, el) => {
     // what is the parent container?
     classes.push($(el).closest('div[id]').attr('id') || $(el).closest('div[class]').attr('class'));
  });
  console.log([...new Set(classes)]);
  
}).catch(console.error);
