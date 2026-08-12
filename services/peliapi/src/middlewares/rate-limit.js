const { ApiError } = require("../utils/api-error");

const usageByDayAndKey = new Map();

function getUtcDayStamp(date) {
  return date.toISOString().slice(0, 10);
}

function getNextUtcMidnightEpochSeconds(now) {
  const resetDate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1, 0, 0, 0));
  return Math.floor(resetDate.getTime() / 1000);
}

function cleanupOldEntries(currentDayStamp) {
  if (usageByDayAndKey.size < 2000) {
    return;
  }

  for (const key of usageByDayAndKey.keys()) {
    if (!key.endsWith(`:${currentDayStamp}`)) {
      usageByDayAndKey.delete(key);
    }
  }
}

function dailyRateLimit(req, res, next) {
  if (String(process.env.DISABLE_RATE_LIMIT).toLowerCase() === "true") {
    return next();
  }

  const now = new Date();
  const dayStamp = getUtcDayStamp(now);
  const limit = Number(process.env.DAILY_REQUEST_LIMIT || 100);
  const usageKey = `${req.apiKey || "anonymous"}:${dayStamp}`;

  cleanupOldEntries(dayStamp);

  const currentUsage = usageByDayAndKey.get(usageKey) || 0;
  const resetAt = getNextUtcMidnightEpochSeconds(now);

  res.setHeader("X-RateLimit-Limit", String(limit));
  res.setHeader("X-RateLimit-Reset", String(resetAt));

  if (currentUsage >= limit) {
    res.setHeader("X-RateLimit-Remaining", "0");
    return next(new ApiError(403, `Limite de requests alcanzado. Tu plan permite ${limit} requests/dia.`));
  }

  const updatedUsage = currentUsage + 1;
  usageByDayAndKey.set(usageKey, updatedUsage);
  res.setHeader("X-RateLimit-Remaining", String(Math.max(0, limit - updatedUsage)));

  return next();
}

module.exports = { dailyRateLimit };
