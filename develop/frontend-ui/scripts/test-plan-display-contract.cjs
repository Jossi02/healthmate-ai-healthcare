/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ts = require('typescript');

const sourcePath = path.join(__dirname, '..', 'lib', 'planDisplay.ts');
const source = fs.readFileSync(sourcePath, 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
});

const moduleContext = { exports: {} };
vm.runInNewContext(compiled.outputText, {
  module: moduleContext,
  exports: moduleContext.exports,
  require,
  console,
});

const {
  cleanDietPlanValue,
  classifyWorkoutGroup,
  getDietDisplay,
  normalizeDietKcal,
  splitCompoundDietText,
} = moduleContext.exports;

function assertNoEvidenceText(value) {
  const text = String(value || '').toLowerCase();
  for (const forbidden of ['근거', '이유', '고려', '알레르기', 'reason', 'evidence', 'allergy']) {
    assert.equal(text.includes(forbidden.toLowerCase()), false, `${value} should not include ${forbidden}`);
  }
}

const cleaned = cleanDietPlanValue('현미밥, 닭가슴살 샐러드 / 근거: 단백질 보충과 체중 감량 목표 고려');
assert.equal(cleaned, '현미밥, 닭가슴살 샐러드');
assertNoEvidenceText(cleaned);

const display = getDietDisplay({
  type: 'Lunch',
  name: '현미밥, 닭가슴살 샐러드 / reason: protein target and allergy profile',
  desc: '근거: 유제품 알레르기 고려',
  kcal: '420 kcal',
});
assert.equal(display.kcal, '420 kcal');
assert.equal(display.subtitle, '');
assertNoEvidenceText(display.title);
assertNoEvidenceText(display.detail);

const compoundDiet = splitCompoundDietText(
  '아침: 잡곡밥 + 구운 고등어 + 시금치나물, 점심: 닭가슴살 샐러드, 현미밥, 저녁: 두부구이, 버섯볶음, 보리밥'
);
assert.deepEqual(
  Array.from(compoundDiet, (item) => item.type),
  ['Breakfast', 'Lunch', 'Dinner']
);
assert.equal(compoundDiet[0].name, '잡곡밥 + 구운 고등어 + 시금치나물');
assert.equal(compoundDiet[1].name, '닭가슴살 샐러드, 현미밥');
assert.equal(compoundDiet[2].name, '두부구이, 버섯볶음, 보리밥');
assert.equal(normalizeDietKcal('0 kcal'), '');

assert.equal(
  classifyWorkoutGroup({
    title: '덤벨 로우',
    type: '스트레칭 루틴',
    level: '스트레칭 루틴',
  }),
  'upper_body'
);
assert.equal(
  classifyWorkoutGroup({
    title: '전신 가벼운 근력 운동',
    type: '스트레칭 루틴',
  }),
  'full_body'
);
assert.equal(
  classifyWorkoutGroup({
    title: '전신 스트레칭',
    type: '유산소 루틴',
  }),
  'stretching'
);

console.log('[plan-display-contract] 7/7 passed');
