const supabase = require('../config/db');
const logger = require('../utils/logger');

// @route   GET /api/v1/admin/stats
// @desc    전체 이용 통계 조회 (관리자 전용)
// @access  Public (프로토타입 - 추후 관리자 권한 체크 추가)
exports.getStats = async (req, res) => {
  try {
    // 통계 집계 병렬 처리
    const [
      { count: totalProfiles },
      { count: totalExercises },
      { count: totalMeals }
    ] = await Promise.all([
      supabase.from('user_health_profiles').select('*', { count: 'exact', head: true }),
      supabase.from('user_exercise_plans').select('*', { count: 'exact', head: true }),
      supabase.from('user_meal_plans').select('*', { count: 'exact', head: true })
    ]);

    res.json({
      overview: {
        totalProfiles: totalProfiles || 0,
        totalExercises: totalExercises || 0,
        totalMeals: totalMeals || 0
      }
    });
    
  } catch (err) {
    logger.error('Admin stats error.', err);
    res.status(500).json({ error: '서버 에러가 발생했습니다.' });
  }
};
