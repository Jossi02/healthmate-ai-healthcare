const express = require('express');
const router = express.Router();
const chatController = require('../controllers/chatController');
const authMiddleware = require('../middleware/auth');

// 모든 chat 관련 API에 JWT 토큰 인증 강제 적용
router.use(authMiddleware);
// AI 채팅 메시지 전송 (DataFormat_3_ai)
router.get('/threads', chatController.listThreads);
router.get('/threads/:session_id', chatController.getThread);
router.delete('/threads/:session_id', chatController.deleteThread);
router.post('/', chatController.sendMessage);
router.post('/feedback', chatController.submitFeedback);

module.exports = router;
