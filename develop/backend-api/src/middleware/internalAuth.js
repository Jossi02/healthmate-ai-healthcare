/**
 * 서버 간 인증 미들웨어
 * FastAPI(AI) → WAS 호출 시 x-api-key 헤더로 인증
 */
const logger = require('../utils/logger');
const {
    getSecurityConfig,
    safeSecretEqual,
    SecurityConfigError,
} = require('../config/security');

function createInternalAuth(getConfig = getSecurityConfig) {
    return (req, res, next) => {
        let expectedApiKey;
        try {
            expectedApiKey = getConfig().internalApiKey;
        } catch (error) {
            if (!(error instanceof SecurityConfigError)) throw error;
            logger.error('Internal API authentication configuration is unavailable.');
            return res.status(503).json({ error: 'Service authentication unavailable.' });
        }

        const apiKey = req.headers['x-api-key'];
        if (!safeSecretEqual(apiKey, expectedApiKey)) {
            logger.warn(`Internal API 인증 실패: ${req.method} ${req.path}`);
            return res.status(403).json({ error: 'Forbidden: Invalid API Key' });
        }

        next();
    };
}

module.exports = createInternalAuth();
module.exports.createInternalAuth = createInternalAuth;
