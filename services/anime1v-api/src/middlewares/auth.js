const { ApiError } = require("../utils/api-error");

function getConfiguredApiKeys() {
  const raw = process.env.API_KEYS || "";
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function requireApiKey(req, _res, next) {
  if (String(process.env.DISABLE_AUTH).toLowerCase() === "true") {
    req.apiKey = "local-dev";
    return next();
  }

  const apiKeyFromHeader = req.header("x-api-key");
  const apiKeyFromQuery = typeof req.query.apiKey === "string" ? req.query.apiKey : "";
  const apiKey = (apiKeyFromHeader || apiKeyFromQuery || "").trim();

  if (!apiKey) {
    return next(new ApiError(401, "API Key requerida. Usa el header X-API-Key o parametro apiKey"));
  }

  const configuredKeys = getConfiguredApiKeys();
  if (configuredKeys.length > 0 && !configuredKeys.includes(apiKey)) {
    return next(new ApiError(401, "API Key invalida o expirada"));
  }

  req.apiKey = apiKey;
  return next();
}

module.exports = { requireApiKey };
