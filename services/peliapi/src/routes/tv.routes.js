const express = require("express");
const router = express.Router();
const tvService = require("../services/tv.service");

function asyncHandler(handler) {
  return (req, res, next) => {
    Promise.resolve(handler(req, res, next)).catch(next);
  };
}

/**
 * Obtener todos los canales en vivo
 * GET /channels
 */
router.get(
  "/channels",
  asyncHandler(async (req, res) => {
    const channels = await tvService.getLiveChannels();
    res.status(200).json({
      success: true,
      data: channels,
    });
  })
);

module.exports = router;
