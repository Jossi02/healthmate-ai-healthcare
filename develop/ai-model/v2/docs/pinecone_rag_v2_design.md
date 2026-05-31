# Pinecone RAG v2 Design

## Scope

- Preserve per-user namespaces: `{user_id}-memory`, `{user_id}-important`.
- Reset and manage only the shared `external` namespace for curated knowledge.
- Treat profile hard constraints as LangGraph state rules, not retrieval facts.

## RAG Trigger Policy

Use `vdb_external` when:

- The request asks for evidence, research, guidelines, or reasons.
- A plan create/modify request has profile risk: age under 19, age 60+, high weight, injury, pain point, medical condition, allergy, dietary restriction, diabetes, hypertension, heart health, bone health.
- The request touches specialized topics: HIIT, hypertrophy, cardio intensity, stretching, PNF, protein, creatine, supplements, diabetes, hypertension, allergies, joint pain.
- The profile-fit validator or repair step needs evidence for a safer substitution.

Use `vdb_memory` and `vdb_user_important` only when the user refers to remembered context:

- "전에", "지난번", "기억", "내가 말한", "싫어", "실패했던", "좋아한다고".

Use `web` only for information requests with recency markers:

- "최신", "최근", "요즘", "뉴스", "연구", "논문", "업데이트", "가이드라인", "권고".

Skip RAG for:

- Casual greetings, thanks, short acknowledgements.
- Plan approval and plan check records.
- Profile writes.
- Emergency safety requests, which must route directly to safety first.
- Low-risk simple starter plans.

## Metadata v2

Each external chunk should include:

```json
{
  "source_type": "external_kb",
  "source": "external",
  "source_title": "ACSM Resistance Training Position Stand",
  "url": "https://...",
  "year": 2026,
  "locale": "global",
  "version": "kb_v2_2026-05-31",
  "domain": "workout",
  "topic": "resistance_training",
  "subtopic": "beginner_progression",
  "category": "workout_resistance_guidelines",
  "use_case": "novice_programming",
  "use_cases": ["plan_create", "plan_modify", "risk_repair"],
  "population": "beginner",
  "profile_targets": ["beginner", "older_adult"],
  "constraints": ["knee_pain", "back_pain"],
  "goals": ["mobility", "muscle_gain"],
  "risk_level": "caution",
  "evidence_type": "position_stand",
  "evidence_rank": 5,
  "chunk_title": "초보자 근력운동 진행",
  "tags": ["ACSM", "beginner"],
  "text": "..."
}
```

## Retrieval Stages

The search node first builds a `RetrievalSpec`:

- `query_span`: the minimal text embedded into Pinecone.
- `targets`: `vdb_external`, `vdb_memory`, `vdb_user_important`, and `web` after trigger normalization.
- `domain`, `topics`, `use_cases`, `profile_targets`, `constraints`, `goals`.
- `critical_constraints`: constraints that must survive post-filtering.
- `negative_constraints`: negated conditions such as "고혈압은 없음" or "유제품은 해당 없음".

Then it retrieves:

1. Strict filter: `source_type + domain + use_cases + profile_targets + constraints + goals`.
2. Relaxed filter: `source_type + domain + use_cases` or `source_type + domain + topic`.
3. Semantic fallback: same namespace without metadata filter.
4. Over-fetch: Pinecone fetches up to 30 external candidates, then returns the final top 8 after post-filter/rerank.
5. Post-filter: remove negative-constraint matches and require `critical_constraints` as a subset when matching evidence exists.
6. Rerank: prefer domain, critical constraint, topic, goal, evidence rank, recency, and more specific rows.

This prevents the previous `population/use_case` mismatch from producing zero results for beginner and older-adult profiles.

## Current Catalog

The v2 catalog lives at `data/external_knowledge_v2.json` and currently contains 40 curated chunks from:

- WHO physical activity guidelines.
- Physical Activity Guidelines for Americans.
- CDC chronic condition and disability activity guidance.
- ACSM exercise testing/prescription and 2026 resistance training position stand.
- Schoenfeld resistance-training volume meta-analysis.
- Sultana low-volume HIIT systematic review.
- Behm stretching systematic review.
- Hindle PNF review.
- NICE low back pain guideline.
- 2025 Korean Dietary Reference Intakes.
- ISSN protein, nutrient timing, and creatine position stands.
- EAACI food allergy guideline.
- ADA Standards of Care in Diabetes.
- AHA diet, sodium, and blood pressure guidance.
- Arthritis, asthma, and cardiovascular-safe activity guidance.
- Common allergy substitution guidance for dairy, egg, nut, shellfish, wheat, and soy profiles.
- Compound-profile retrieval guards for hypertension+diabetes, older-adult arthritis, plant-based+dairy-allergy muscle gain, high-BMI low-impact exercise, stretching/mobility classification, mixed none+real allergy input, and memory+external diet modification.

## Reingestion

Reset only `external` and ingest v2:

```bash
python scripts/ingest_external.py --data data/external_knowledge_v2.json --reset-external --sleep 0.05
```

External vectors now use deterministic IDs: `external::{kb_id}`. Reingesting without reset updates known rows instead of creating duplicates.

Verify live retrieval:

```bash
python scripts/test_pinecone_external_v2.py
python scripts/test_pinecone_metadata_lint.py
python scripts/test_pinecone_profile_rag_v2_suite.py
```
