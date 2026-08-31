const logger = require('../utils/logger');

/**
 * 전역 에러 핸들러 미들웨어
 * express-validator 오류 및 일반 서버 오류를 통합 처리합니다.
 */
const errorHandler = (err, req, res, next) => {
  const candidateStatus = Number(err?.statusCode);
  const statusCode = Number.isInteger(candidateStatus)
    && candidateStatus >= 400
    && candidateStatus < 600
    ? candidateStatus
    : 500;
  const message = statusCode === 404
    ? '요청한 경로를 찾을 수 없습니다.'
    : statusCode < 500
      ? 'Request failed.'
      : '서버 내부 오류가 발생했습니다.';

  logger.error(
    `[${req.method}] ${req.path} >> StatusCode:: ${statusCode}`,
    logger.errorMetadata(err)
  );

  res.status(statusCode).json({
    success: false,
    statusCode,
    message,
  });
};

/**
 * 정의되지 않은 라우트 처리 (404)
 */
const notFoundHandler = (req, res, next) => {
  const error = new Error('요청한 경로를 찾을 수 없습니다.');
  error.statusCode = 404;
  next(error);
};

module.exports = { errorHandler, notFoundHandler };
