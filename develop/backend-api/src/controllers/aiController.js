const axios = require('axios');
const supabase = require('../config/db');
const logger = require('../utils/logger');

const FASTAPI_URL = process.env.FASTAPI_URL || 'http://localhost:8000';
const AI_TIMEOUT = parseInt(process.env.AI_REQUEST_TIMEOUT) || 90000;

// @route   GET /api/v1/ai/plans
// @desc    특정 기간의 운동/식단 플랜 조회
// @access  Public (프로토타입)
exports.getPlans = async (req, res) => {
  try {
    const { user_id, startDate, endDate } = req.query;
    if (!user_id) return res.status(400).json({ error: 'user_id가 필요합니다.' });
    if (!startDate || !endDate) return res.status(400).json({ error: 'startDate와 endDate가 필요합니다.' });

    // 운동 플랜 조회
    const { data: exercises, error: exErr } = await supabase
      .from('user_exercise_plans')
      .select('*')
      .eq('user_id', user_id)
      .gte('target_date', startDate)
      .lte('target_date', endDate)
      .order('target_date', { ascending: true });

    // 식단 플랜 조회
    const { data: meals, error: mlErr } = await supabase
      .from('user_meal_plans')
      .select('*')
      .eq('user_id', user_id)
      .gte('target_date', startDate)
      .lte('target_date', endDate)
      .order('target_date', { ascending: true });

    if (exErr) throw exErr;
    if (mlErr) throw mlErr;

    res.json({ exercises: exercises || [], meals: meals || [] });
  } catch (err) {
    logger.error('Legacy AI plans error.', err);
    res.status(500).json({ error: '서버 에러가 발생했습니다.' });
  }
};

// @route   POST /api/v1/ai/process-meal
// @desc    식단 기록 → AI 서버로 칼로리 분석 중계 (DataFormat_2_ai_api 1번)
// @access  Public (프로토타입)
exports.processMeal = async (req, res) => {
  try {
    const { user_id, user_message } = req.body;
    if (!user_id) return res.status(400).json({ error: 'user_id가 필요합니다.' });
    if (!user_message) return res.status(400).json({ error: '식단 메시지를 입력해주세요.' });

    // 유저 프로필 조회
    const { data: profile } = await supabase
      .from('user_health_profiles')
      .select('*')
      .eq('user_id', user_id)
      .single();

    const payload = {
      user_id,
      user_profile: profile ? {
        gender: profile.gender,
        age: profile.age,
        bmi: profile.bmi,
        goal: profile.goal,
        medical_history: profile.medical_history,
        allergies: profile.allergies
      } : {},
      user_message
    };

    try {
      const response = await axios.post(`${FASTAPI_URL}/process-meal`, payload, { timeout: AI_TIMEOUT });
      return res.json(response.data);
    } catch (aiError) {
      logger.error('Legacy AI meal request failed.', aiError);
      return res.json({
        status: 'fallback',
        data: {
          calories: 0,
          message: '현재 AI 서버와 통신할 수 없습니다. 잠시 후 다시 시도해주세요.'
        }
      });
    }

  } catch (err) {
    logger.error('Legacy AI meal processing error.', err);
    res.status(500).json({ error: '서버 에러가 발생했습니다.' });
  }
};

// @route   POST /api/v1/ai/recommend
// @desc    운동/식단 추천 요청 (DataFormat_2_ai_api 2번)
// @access  Public (프로토타입)
exports.recommend = async (req, res) => {
  try {
    const { user_id } = req.body;
    if (!user_id) return res.status(400).json({ error: 'user_id가 필요합니다.' });

    // 유저 프로필 조회
    const { data: profile } = await supabase
      .from('user_health_profiles')
      .select('*')
      .eq('user_id', user_id)
      .single();

    const payload = {
      user_id,
      user_profile: profile ? {
        gender: profile.gender,
        age: profile.age,
        bmi: profile.bmi,
        goal: profile.goal,
        activity_level: profile.activity_level
      } : {}
    };

    try {
      const response = await axios.post(`${FASTAPI_URL}/recommend`, payload, { timeout: AI_TIMEOUT });
      return res.json(response.data);
    } catch (aiError) {
      logger.error('Legacy AI recommendation request failed.', aiError);
      return res.json({
        status: 'fallback',
        data: {
          recommended_exercise: { name: '가벼운 산책', burn_calories: 150 },
          recommended_meal: { name: '닭가슴살 샐러드', calories: 350 }
        }
      });
    }

  } catch (err) {
    logger.error('Legacy AI recommendation error.', err);
    res.status(500).json({ error: '서버 에러가 발생했습니다.' });
  }
};

// @route   POST /api/v1/ai/instruction
// @desc    사용자 지시사항(User Instruction) 데이터 AI 서버로 전달
// @access  Public (프로토타입)
exports.saveInstruction = async (req, res) => {
  try {
    const { user_id, user_instruction } = req.body;

    if (!user_id) return res.status(400).json({ error: 'user_id가 필요합니다.' });
    if (!user_instruction) return res.status(400).json({ error: '지시사항(user_instruction)을 입력해주세요.' });

    const payload = {
      user_id,
      user_instruction
    };

    try {
      // AI 서버의 지정 엔드포인트(예: /user-instruction)로 전송
      const response = await axios.post(`${FASTAPI_URL}/user-instruction`, payload, { timeout: AI_TIMEOUT });

      // AI 서버 측 응답을 클라이언트에게 그대로 전달
      return res.json(response.data);
    } catch (aiError) {
      logger.error('Legacy AI instruction request failed.', aiError);
      return res.status(502).json({
        error: 'AI 서버로 지시사항을 전송하는데 실패했습니다.',
      });
    }

  } catch (err) {
    logger.error('Legacy AI instruction error.', err);
    res.status(500).json({ error: '서버 에러가 발생했습니다.' });
  }
};
