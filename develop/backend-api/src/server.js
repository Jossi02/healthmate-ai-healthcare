require('dotenv').config();
const { getSecurityConfig } = require('./config/security');

// Refuse to open a port when required authentication configuration is unsafe.
getSecurityConfig();

const app = require('./app');
const logger = require('./utils/logger');

const PORT = process.env.PORT || 5000;

const server = app.listen(PORT, () => {
  logger.info(`========================================`);
  logger.info(`  AI 헬스케어 백엔드 서버 시작`);
  logger.info(`  환경:  ${process.env.NODE_ENV || 'development'}`);
  logger.info(`  포트:  http://localhost:${PORT}`);
  logger.info(`  헬스체크: http://localhost:${PORT}/api/health`);
  logger.info(`========================================`);
});

// ─── 예상치 못한 종료 처리 ────────────────────────────────────────────
// 처리되지 않은 Promise rejection
process.on('unhandledRejection', (reason, promise) => {
  logger.error('처리되지 않은 Promise Rejection.', logger.errorMetadata(reason));
  // 서버 정상 종료 후 프로세스 재시작 (PM2 등 프로세스 매니저 활용 시)
  server.close(() => process.exit(1));
});

// 예상치 못한 예외
process.on('uncaughtException', (error) => {
  logger.error('처리되지 않은 예외.', logger.errorMetadata(error));
  server.close(() => process.exit(1));
});

// SIGTERM 신호 처리 (Docker, 클라우드 환경에서 graceful shutdown)
process.on('SIGTERM', () => {
  logger.info('SIGTERM 신호 수신. 서버를 정상 종료합니다...');
  server.close(() => {
    logger.info('서버 종료 완료.');
    process.exit(0);
  });
});
