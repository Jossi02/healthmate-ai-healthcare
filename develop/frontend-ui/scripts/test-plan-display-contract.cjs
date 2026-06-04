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

const { cleanDietPlanValue, classifyWorkoutGroup, getDietDisplay } = moduleContext.exports;

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

console.log('[plan-display-contract] 5/5 passed');
