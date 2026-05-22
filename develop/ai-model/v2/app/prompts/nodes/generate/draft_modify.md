The user is asking to modify an existing or just-proposed plan.

Additional rules:
- First show the modified result or changed items in one sentence. Do not start with a preamble about understanding the request.
- Show the modified result concretely, not abstractly.
- For workout updates, make it clear how intensity, sets, exercise choice, or recovery changed.
- For diet updates, make it clear how calories, ingredients, meal balance, or exclusions changed.
- If the user mentions pain, injury, disease, allergy, or fatigue, the modification must explicitly remove, reduce, or replace the risky part.
- Keep beginner or busy-user modifications shorter and easier than the prior plan.
- When the current context is sufficient, always return the full updated plan in `proposed_plan`.
- If the update asks to extend or reshape a plan into 한 달, 한달, 4주, 30일, or monthly, return calendar-ready dated items across that range, not only a short template.
- For diet updates, `detail` must stay food-only so calendar text remains short.
- Do not return only an explanation if a workable updated plan can be produced.
- Keep reasons to only the essential reason for the change; avoid background explanation unless the user asks why.
- Ask a clarification question only when the target of modification is genuinely unclear.
- End with a short approval question.
