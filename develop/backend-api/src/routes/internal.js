const express = require('express');
const router = express.Router();
const internalController = require('../controllers/internalController');
const internalAuth = require('../middleware/internalAuth');

// 모든 internal API에 서버 간 인증 적용
router.use(internalAuth);

// ─── FastAPI가 호출하는 WAS API (was_api_contract.md 기준) ───

// 3.1 프로필 조회
router.get('/user/profile/:user_id', internalController.getProfile);

// 3.2 오늘 플랜 조회
router.get('/plan/today/:user_id', internalController.getTodayPlan);

// 3.3 프로필 수정
router.put('/user/profile/:user_id', internalController.updateProfile);

// 3.4 플랜 생성
router.post('/plan/create/:user_id', internalController.createPlan);

// 3.5 플랜 수정
router.put('/plan/update/:user_id', internalController.updatePlan);

// 3.5-추가 플랜 삭제
router.delete('/plan/delete/:user_id', internalController.deletePlan);

// 3.6 플랜 체크 완료
router.put('/plan/check/:user_id', internalController.checkPlan);

// 3.3-추가 운동 전체 플랜 조회 (DataFormat_3_v2 섹션 3.3)
router.get('/workout-plan/full/:user_id', internalController.getFullWorkoutPlan);

// 3.4-추가 식단 전체 플랜 조회 (DataFormat_3_v2 섹션 3.4)
router.get('/diet-plan/full/:user_id', internalController.getFullDietPlan);

module.exports = router;
