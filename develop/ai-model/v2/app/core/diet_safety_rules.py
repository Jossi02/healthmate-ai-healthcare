"""Shared diet safety terms used by answer validation and home recommendations."""
from __future__ import annotations

COMMON_SODIUM_HEAVY_TERMS = (
    "ramen",
    "instant noodle",
    "sausage",
    "bacon",
    "processed meat",
    "pickle",
    "brine",
    "soup broth",
    "salty",
)

COMMON_SUGAR_HEAVY_TERMS = (
    "soda",
    "juice",
    "smoothie",
    "syrup",
    "cookie",
    "candy",
    "sweetened",
)

COMMON_KIDNEY_HIGH_PROTEIN_TERMS = (
    "protein shake",
    "high protein",
    "protein bar",
    "protein powder",
    "whey",
    "casein",
)

COMMON_GOUT_PURINE_TERMS = (
    "purine",
    "beer",
    "anchovy",
    "sardine",
    "mackerel",
    "organ meat",
    "liver",
    "shellfish",
    "clam",
)

COMMON_PREGNANCY_FOOD_SAFETY_TERMS = (
    "raw fish",
    "raw egg",
    "alcohol",
    "unpasteurized",
    "deli meat",
    "high mercury",
    "tuna steak",
)

COMMON_EATING_DISORDER_RISK_TERMS = (
    "fasting",
    "detox",
    "cleanse",
    "one meal a day",
    "omad",
    "very low calorie",
)
