const { extractVoe } = require('../src/utils/resolvers/voe.resolver');
const { extractStreamwish } = require('../src/utils/resolvers/streamwish.resolver');
const { extractStreamtape } = require('../src/utils/resolvers/streamtape.resolver');

// URLs de prueba por defecto (pueden ser sobreescritas por argumentos)
const testUrls = {
  voe: [
    'https://voe.sx/e/oexs8a1v5qkw',
    'https://voe.sx/e/aeqe0x9g6h9m'
  ],
  streamwish: [
    'https://streamwish.com/e/6y8as2f60pzp',
    'https://awish.pro/e/d4o1s0x7r0n9'
  ],
  streamtape: [
    'https://streamtape.com/e/8x8vqp8qB3TzJ6g',
    'https://streamtape.com/e/6j9kLm1P2a3b4c'
  ]
};

async function runTest() {
  console.log('===================================================');
  console.log('🤖 PRUEBAS AUTOMATIZADAS DE RESOLUTORES DE VÍDEO');
  console.log('===================================================');

  // Si se pasan URLs por línea de comando
  const args = process.argv.slice(2);
  if (args.length > 0) {
    console.log(`\nProbando URL personalizada suministrada: ${args[0]}`);
    const url = args[0];
    if (url.includes('voe')) {
      const start = Date.now();
      const res = await extractVoe(url);
      console.log(`⏱️ Tiempo transcurrido: ${Date.now() - start}ms`);
      console.log(`✨ Resultado VOE:`, res ? `EXITOSO -> ${res}` : '❌ FALLIDO');
    } else if (url.includes('wish') || url.includes('playnix') || url.includes('medix') || url.includes('awish')) {
      const start = Date.now();
      const res = await extractStreamwish(url);
      console.log(`⏱️ Tiempo transcurrido: ${Date.now() - start}ms`);
      console.log(`✨ Resultado Streamwish:`, res ? `EXITOSO -> ${res}` : '❌ FALLIDO');
    } else if (url.includes('tape')) {
      const start = Date.now();
      const res = await extractStreamtape(url);
      console.log(`⏱️ Tiempo transcurrido: ${Date.now() - start}ms`);
      console.log(`✨ Resultado Streamtape:`, res ? `EXITOSO -> ${res}` : '❌ FALLIDO');
    } else {
      console.log('Servidor no reconocido en la URL personalizada. Intenta con una URL de voe, streamwish o streamtape.');
    }
    return;
  }

  // Pruebas por defecto
  console.log('\n--- 1. Probando Resolutor VOE ---');
  for (const url of testUrls.voe) {
    console.log(`Resolviendo: ${url}`);
    const start = Date.now();
    const res = await extractVoe(url);
    console.log(`⏱️ Tiempo: ${Date.now() - start}ms`);
    console.log(`➡️ Resultado: ${res ? '✅ OK (Obtenida: ' + res.slice(0, 75) + '...)' : '❌ ERROR'}\n`);
  }

  console.log('\n--- 2. Probando Resolutor Streamwish ---');
  for (const url of testUrls.streamwish) {
    console.log(`Resolviendo: ${url}`);
    const start = Date.now();
    const res = await extractStreamwish(url);
    console.log(`⏱️ Tiempo: ${Date.now() - start}ms`);
    console.log(`➡️ Resultado: ${res ? '✅ OK (Obtenida: ' + res.slice(0, 75) + '...)' : '❌ ERROR'}\n`);
  }

  console.log('\n--- 3. Probando Resolutor Streamtape ---');
  for (const url of testUrls.streamtape) {
    console.log(`Resolviendo: ${url}`);
    const start = Date.now();
    const res = await extractStreamtape(url);
    console.log(`⏱️ Tiempo: ${Date.now() - start}ms`);
    console.log(`➡️ Resultado: ${res ? '✅ OK (Obtenida: ' + res.slice(0, 75) + '...)' : '❌ ERROR'}\n`);
  }
}

runTest();
