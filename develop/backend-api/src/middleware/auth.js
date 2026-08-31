const jwt = require('jsonwebtoken');
const { getSecurityConfig, SecurityConfigError } = require('../config/security');

function createAuthMiddleware(getConfig = getSecurityConfig) {
  return (req, res, next) => {
    // 1. 헤더에서 토큰 추출
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
        return res.status(401).json({ error: '인증 토큰이 제공되지 않았습니다.' });
    }

    const token = authHeader.split(' ')[1];

    try {
        // 2. 토큰 검증
        const decoded = jwt.verify(token, getConfig().jwtSecret, { algorithms: ['HS256'] });

        // 3. req 객체에 유저 정보 저장
        req.user = decoded; // { user_id, login_id }
        next();
    } catch (err) {
        if (err instanceof SecurityConfigError) {
            return res.status(503).json({ error: '인증 설정을 사용할 수 없습니다.' });
        }
        if (err.name === 'TokenExpiredError') {
            return res.status(401).json({ error: '토큰이 만료되었습니다.' });
        }
        return res.status(401).json({ error: '유효하지 않은 토큰입니다.' });
    }
  };
}

module.exports = createAuthMiddleware();
module.exports.createAuthMiddleware = createAuthMiddleware;
