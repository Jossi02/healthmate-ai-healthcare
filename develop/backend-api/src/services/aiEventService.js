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

exports.notifyProfileUpdated = async (userId, changedFields = [], profileVersion = null) => {
  try {
    const payload = {
      user_id: userId,
      changed_fields: changedFields,
    };

    if (profileVersion !== null) {
      payload.profile_version = profileVersion;
    }

    const response = await axios.post(
      `${FASTAPI_URL}/internal/events/profile-updated`,
      payload,
      {
        timeout: AI_TIMEOUT,
        headers: buildFastApiHeaders(),
      }
    );

    logger.info(
      'Profile update event pushed: user_id=%s fields=%s',
      userId,
      changedFields.join(',')
    );
    return response.data;
  } catch (error) {
    logger.error(
      'Profile update event push failed: user_id=%s error=%s',
      userId,
      error.message
    );
    return null;
  }
};
