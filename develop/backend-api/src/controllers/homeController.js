const axios = require('axios');
const logger = require('../utils/logger');
const { getSecurityConfig } = require('../config/security');

const FASTAPI_URL = (process.env.FASTAPI_URL || 'http://localhost:8000').replace(/\/$/, '');
const AI_TIMEOUT = parseInt(process.env.AI_REQUEST_TIMEOUT || '90000', 10);

function buildFastApiHeaders() {
  return {
    'Content-Type': 'application/json',
    'x-api-key': getSecurityConfig().internalApiKey,
  };
}

async function forwardRecommendations(req, res, scope) {
  try {
    const userId = req.user.user_id;
    const normalizedScope = String(scope || req.body.type || 'all').trim().toLowerCase();

    if (!['all', 'workout', 'diet'].includes(normalizedScope)) {
      return res.status(400).json({ error: 'type must be one of all, workout, diet.' });
    }

    const response = await axios.post(
      `${FASTAPI_URL}/home/recommendations${normalizedScope === 'all' ? '' : `/${normalizedScope}`}`,
      {
        user_id: userId,
        type: normalizedScope,
        recent_recommendations: req.body.recent_recommendations || null,
      },
      {
        timeout: AI_TIMEOUT,
        headers: buildFastApiHeaders(),
      }
    );

    return res.json(response.data);
  } catch (error) {
    const upstreamStatus = error.response?.status;
    const upstreamPayload = error.response?.data;

    logger.error(`Home recommendation gateway error: ${error.message}`);

    if (upstreamStatus) {
      return res.status(502).json({
        error: 'FastAPI home recommendation upstream error.',
        upstream_status: upstreamStatus,
        upstream_error: upstreamPayload || null,
      });
    }

    return res.status(500).json({
      error: 'Failed to load home recommendations.',
    });
  }
}

// @route   POST /api/v1/home/recommendations
// @desc    Home tab recommendation gateway -> FastAPI /home/recommendations
// @access  Private
exports.getRecommendations = async (req, res) => {
  return forwardRecommendations(req, res, req.body.type);
};

exports.getWorkoutRecommendations = async (req, res) =>
  forwardRecommendations(req, res, 'workout');

exports.getDietRecommendations = async (req, res) =>
  forwardRecommendations(req, res, 'diet');
