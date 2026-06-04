const express = require('express');
const router = express.Router();
const userController = require('../controllers/userController');
const authMiddleware = require('../middleware/auth');

// All users APIs require JWT auth.
router.use(authMiddleware);

// 1. Health profile (user_health_profiles)
router.get('/profile', userController.getProfile);
router.post('/profile', userController.saveProfile);
router.patch('/:user_id/settings/persona', userController.updatePersonaSetting);

// 1-1. Calendar aggregation (user_exercise_plans, user_meal_plans)
router.get('/calendar', userController.getCalendar);
router.put('/plans/check', userController.checkTodayPlanItem);
router.delete('/plans', userController.deletePlansForDates);
router.delete('/plans/:item_id', userController.deletePlanItem);

// 2. Exercise plans (user_exercise_plans, exercise_items)
router.put('/exercises/items/:item_id', userController.updateExerciseItem);
router.post('/exercises/recommend-add', userController.addRecommendedExercise);

// 3. Meal plans (user_meal_plans)
// Keep the static route before the dynamic :id route, otherwise
// "/meals/recommend-replace" is parsed as a meal id.
router.put('/meals/recommend-replace', userController.replaceRecommendedMeal);
router.put('/meals/:id', userController.updateMealStatus);

module.exports = router;
