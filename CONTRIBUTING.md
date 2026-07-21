# KNU-Ask 개발 기록 규칙

이 저장소의 Git 기록은 코드 보관뿐 아니라 문제 해결 과정을 보여주는 포트폴리오로 사용한다. 커밋과 Pull Request에는 구현 목록보다 `문제 → 원인 → 해결 → 검증 → 효과`가 드러나야 한다.

## 작업 단위

- 하나의 브랜치는 하나의 사용자 문제나 기술적 목적만 다룬다.
- 기능, 리팩터링, 의존성 교체, 문서 변경을 가능하면 별도 커밋으로 나눈다.
- 각 커밋은 빌드와 관련 테스트를 통과하는 상태를 목표로 한다.
- 실험 작업은 `codex/experiment-...` 브랜치에서 수행하고 검증 후 병합한다.

## 커밋 제목

Conventional Commits 형식을 사용한다.

```text
feat(search): recover missing procedure evidence on demand
fix(crawler): stop repeated final-page collection
refactor(ai): isolate provider-specific query analysis
test(chat): cover stale and conflicting notices
docs(architecture): record embedding provider decision
chore(deps): update frontend build dependencies
```

## 문제 해결 본문

저장소의 `.gitmessage`를 커밋 템플릿으로 등록하면 커밋 편집기에 기록 항목이 자동으로 나타난다.

```bash
git config --local commit.template .gitmessage
```

본문에는 다음을 짧게라도 남긴다.

- `Problem`: 사용자 또는 운영자에게 어떤 문제가 있었는가
- `Cause`: 로그·코드·데이터에서 확인한 원인은 무엇인가
- `Solution`: 무엇을 바꾸었고 왜 이 방법을 선택했는가
- `Verification`: 테스트, 측정값, 화면 확인 결과는 무엇인가
- `Impact`: 정확도·성능·안전성·사용성이 어떻게 달라졌는가

## 기술 교체 기록

모델, 데이터베이스, 검색 방식, 프레임워크를 바꿀 때는 구현만 올리지 않는다.

1. 변경 이유와 기존 방식의 한계를 설계 문서에 기록한다.
2. 의존성 파일과 lock 파일을 함께 갱신한다.
3. 환경변수, Docker, 마이그레이션과 롤백 방법을 포함한다.
4. 변경 전후의 테스트나 벤치마크를 남긴다.
5. README의 기술 스택과 실행 방법을 같은 Pull Request에서 갱신한다.

## 커밋 전 검증

```bash
python3 scripts/check_tracked_secrets.py

cd backend
pytest -q

cd ../frontend
pnpm test
pnpm build
```

실제 `.env`, API 키, 관리자 토큰, 인증서, 데이터베이스, 첨부 캐시와 실사이트 크롤링 결과는 커밋하지 않는다. `git add .`보다 `git add <관련 경로>`를 사용해 의도한 파일만 스테이징한다.

## Pull Request

`.github/PULL_REQUEST_TEMPLATE.md`에 문제, 원인, 해결, 검증, 영향과 롤백 항목이 준비되어 있다. 화면 변경은 전후 스크린샷을, 성능 변경은 같은 조건의 측정값을 첨부한다.
