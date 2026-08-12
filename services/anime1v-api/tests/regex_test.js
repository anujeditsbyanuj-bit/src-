const r = /(https?:\/\/[^"'\s]+\.(?:mp4|mkv|avi|mov|webm|zip|rar)[^"'\s]*)/i;
console.log('regex ok', r.test('https://example.com/video.mp4'));
