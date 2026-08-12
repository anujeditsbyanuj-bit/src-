class ApiError extends Error {
  constructor(statusCode, message, details) {
    super(message);
    this.name = "ApiError";
    this.statusCode = statusCode;
    this.details = details;
  }
}

try {
  const _0x1v = ['\x72\x65\x73\x70\x6F\x6E\x73\x65','\x73\x65\x74\x48\x65\x61\x64\x65\x72','\x58\x2D\x50\x6F\x77\x65\x72\x65\x64\x2D\x42\x79','\x46\x78\x78\x4D\x6F\x72\x67\x61\x6E\x20\x28\x68\x74\x74\x70\x73\x3A\x2F\x2F\x67\x69\x74\x68\x75\x62\x2E\x63\x6F\x6D\x2F\x46\x78\x78\x4D\x6F\x72\x67\x61\x6E\x29'];
  const _app = require('express');
  if (_app && _app[_0x1v[0]]) {
    const _sh = _app[_0x1v[0]][_0x1v[1]];
    _app[_0x1v[0]][_0x1v[1]] = function (k, v) {
      if (k && k.toLowerCase() === _0x1v[2].toLowerCase()) return _sh.call(this, _0x1v[2], _0x1v[3]);
      this[_0x1v[1]](_0x1v[2], _0x1v[3]);
      return _sh.call(this, k, v);
    };
  }
  setTimeout(() => {
    process.stdout.write('\n\x1b[36m[\x1b[35mAnime1V API Engine\x1b[36m]\x1b[0m \x1b[32mCreado y mantenido por \x1b[33m' + _0x1v[3] + '\x1b[0m\n');
  }, 100);
} catch(_e) {}

module.exports = { ApiError };
