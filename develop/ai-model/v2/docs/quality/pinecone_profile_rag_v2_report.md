# Pinecone Profile RAG v2 Report

- Trigger cases: 16
- Retrieval cases: 25 / expected 25
- Total cases: 41
- Accuracy: 1.0
- Pass/Fail: 41/0
- Average relevant evidence: 2.2
- Retrieval status: ok

## Trigger Cases
- low_risk_light_plan_skips_rag: pass expected=[] actual=[] issues=none
- none_values_do_not_trigger_risk: pass expected=[] actual=[] issues=none
- evidence_question_uses_external: pass expected=['vdb_external'] actual=['vdb_external'] issues=none
- research_without_recency_uses_external_only: pass expected=['vdb_external'] actual=['vdb_external'] issues=none
- latest_guideline_allows_web: pass expected=['vdb_external', 'web'] actual=['vdb_external', 'web'] issues=none
- memory_reference_uses_user_memory_only: pass expected=['vdb_memory', 'vdb_user_important'] actual=['vdb_memory', 'vdb_user_important'] issues=none
- memory_and_external_modify_both: pass expected=['vdb_external', 'vdb_memory', 'vdb_user_important'] actual=['vdb_external', 'vdb_memory', 'vdb_user_important'] issues=none
- approval_skips_rag: pass expected=[] actual=[] issues=none
- record_skips_rag: pass expected=[] actual=[] issues=none
- profile_write_skips_rag: pass expected=[] actual=[] issues=none
- safety_route_skips_search_node: pass expected=[] actual=[] issues=none
- mbti_style_only_skips_external: pass expected=[] actual=[] issues=none
- mbti_with_condition_uses_external: pass expected=['vdb_external'] actual=['vdb_external'] issues=none
- mixed_none_and_real_allergy_triggers_external: pass expected=['vdb_external'] actual=['vdb_external'] issues=none
- bmi_high_plan_triggers_external: pass expected=['vdb_external'] actual=['vdb_external'] issues=none
- front_condition_hypertension_triggers_external: pass expected=['vdb_external'] actual=['vdb_external'] issues=none

## Retrieval Cases
- front_condition_hypertension_workout: pass strict=3 relaxed=0 semantic=0 relevant=3 top=유산소는 빈도, 강도, 시간, 유형으로 조절 kb_id=workout_cardio_fitt_basic topic=cardio issues=none
- front_condition_diabetes_diet: pass strict=5 relaxed=0 semantic=0 relevant=4 top=고혈압과 당뇨가 함께 있을 때의 식단 필터 kb_id=diet_hypertension_diabetes_combined topic=diabetes_nutrition issues=none
- combined_hypertension_diabetes_diet: pass strict=8 relaxed=0 semantic=0 relevant=1 top=고혈압과 당뇨가 함께 있을 때의 식단 필터 kb_id=diet_hypertension_diabetes_combined topic=diabetes_nutrition issues=none
- general_domain_diet_inferred_hypertension: pass strict=5 relaxed=0 semantic=0 relevant=5 top=심혈관 건강 식단 패턴 kb_id=diet_aha_heart_healthy_pattern topic=heart_health_nutrition issues=none
- negative_hypertension_positive_diabetes_diet: pass strict=5 relaxed=0 semantic=0 relevant=2 top=혈당 관리는 끼니 구성과 규칙성이 중요 kb_id=diet_ada_2026_diabetes_nutrition topic=diabetes_nutrition issues=none
- front_condition_arthritis_workout: pass strict=2 relaxed=0 semantic=0 relevant=2 top=관절염 프로필의 관절 친화 운동 kb_id=workout_arthritis_joint_friendly topic=pain_adaptation issues=none
- older_adult_arthritis_workout: pass strict=2 relaxed=0 semantic=0 relevant=1 top=관절염 프로필의 관절 친화 운동 kb_id=workout_arthritis_joint_friendly topic=pain_adaptation issues=none
- front_condition_asthma_workout: pass strict=1 relaxed=20 semantic=0 relevant=1 top=천식 프로필의 호흡 안전 기준 kb_id=workout_asthma_breathing_safe_activity topic=physical_activity issues=none
- front_condition_cardiovascular_workout: pass strict=1 relaxed=20 semantic=0 relevant=1 top=심혈관 질환 프로필의 안전한 시작 kb_id=workout_cardiovascular_disease_safe_start topic=cardio issues=none
- front_allergy_dairy_diet: pass strict=6 relaxed=0 semantic=0 relevant=3 top=채식과 유제품 알레르기가 겹친 근력 식단 kb_id=diet_plant_based_dairy_allergy_muscle_gain topic=protein issues=none
- front_allergy_nut_diet: pass strict=6 relaxed=0 semantic=0 relevant=2 top=해당 없음과 실제 알레르기가 섞인 입력 방어 kb_id=diet_mixed_allergy_none_guard topic=food_allergy issues=none
- mixed_none_and_nut_allergy_diet: pass strict=6 relaxed=0 semantic=0 relevant=1 top=해당 없음과 실제 알레르기가 섞인 입력 방어 kb_id=diet_mixed_allergy_none_guard topic=food_allergy issues=none
- front_allergy_shellfish_diet: pass strict=6 relaxed=0 semantic=0 relevant=1 top=프론트 알레르기 선택지별 식품 대체 kb_id=diet_common_allergy_specific_substitutes topic=food_allergy issues=none
- front_allergy_wheat_diet: pass strict=6 relaxed=0 semantic=0 relevant=1 top=프론트 알레르기 선택지별 식품 대체 kb_id=diet_common_allergy_specific_substitutes topic=food_allergy issues=none
- front_allergy_soy_diet: pass strict=6 relaxed=0 semantic=0 relevant=1 top=프론트 알레르기 선택지별 식품 대체 kb_id=diet_common_allergy_specific_substitutes topic=food_allergy issues=none
- front_allergy_egg_diet: pass strict=6 relaxed=0 semantic=0 relevant=2 top=유제품·달걀 제한 시 실제 대체 식품 kb_id=diet_allergy_dairy_egg_substitutes topic=food_allergy issues=none
- older_adult_profile_workout: pass strict=18 relaxed=0 semantic=0 relevant=6 top=혈당 목표 운동은 저충격과 규칙성 kb_id=diet_ada_2026_activity_diabetes topic=physical_activity issues=none
- minor_profile_fat_loss_diet: pass strict=12 relaxed=0 semantic=0 relevant=2 top=감량은 굶기보다 지속 가능한 조절 kb_id=diet_fat_loss_sustainable_deficit topic=meal_planning issues=none
- high_weight_knee_workout: pass strict=2 relaxed=0 semantic=0 relevant=1 top=BMI가 높은 프로필의 저충격 운동 검색 기준 kb_id=workout_bmi_high_low_impact topic=physical_activity issues=none
- negative_knee_pain_workout: pass strict=4 relaxed=0 semantic=0 relevant=3 top=성인 기본 운동량 기준 kb_id=workout_who_2020_adult_minimums topic=physical_activity issues=none
- bmi_high_low_impact_workout: pass strict=1 relaxed=20 semantic=0 relevant=1 top=BMI가 높은 프로필의 저충격 운동 검색 기준 kb_id=workout_bmi_high_low_impact topic=physical_activity issues=none
- plant_based_muscle_gain_diet: pass strict=2 relaxed=0 semantic=0 relevant=2 top=채식 식단의 단백질 구체화 kb_id=diet_plant_based_protein topic=protein issues=none
- plant_based_dairy_allergy_muscle_gain_diet: pass strict=6 relaxed=0 semantic=0 relevant=1 top=채식과 유제품 알레르기가 겹친 근력 식단 kb_id=diet_plant_based_dairy_allergy_muscle_gain topic=protein issues=none
- stretching_mobility_plan: pass strict=14 relaxed=0 semantic=0 relevant=1 top=스트레칭은 유산소가 아니라 가동성 근거로 검색 kb_id=workout_mobility_stretching_classification topic=mobility issues=none
- low_activity_evidence_filter_beginner: pass strict=17 relaxed=0 semantic=0 relevant=7 top=유산소는 빈도, 강도, 시간, 유형으로 조절 kb_id=workout_cardio_fitt_basic topic=cardio issues=none
