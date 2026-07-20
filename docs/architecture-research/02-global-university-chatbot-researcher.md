# 해외 대학 학생지원 챗봇·지식검색 사례 연구

> 역할: Global University Chatbot Researcher  
> 조사 기준일: 2026-07-20  
> 대상: KNU-Ask 저장소의 공지 기반 학생지원 챗봇 MVP

## 1. 결론 요약

해외 사례가 보여주는 핵심은 “챗봇을 얼마나 사람처럼 말하게 했는가”가 아니라 **어떤 학생 과업을, 어떤 공식 데이터로, 어떤 사람 지원 체계와 연결했는가**이다.

1. 효과가 가장 명확한 유형은 입학 서류, 등록, 장학, 수강처럼 **기한과 완료 여부가 분명한 행정 과업**이다. Georgia State의 Pounce는 학생별 미완료 과업 데이터를 이용한 선제 알림과 질의응답으로, 대학 등록 의사가 있던 학생의 정시 등록을 3.3%p 높이고 summer melt를 21% 줄였다.
2. 광범위한 “무엇이든 물어보세요”보다 **도메인별 어시스턴트와 공식 원문 근거**가 신뢰와 운영 책임을 명확히 한다. Michigan의 Maizey, UC San Diego의 TritonGPT, University of Arizona의 서비스별 Assistant가 이 방향이다.
3. 개인화의 가치가 크지만, 공지 검색과 학생 개인 상태 조회는 보안 등급이 다르다. 공개 공지 RAG와 학사정보시스템(SIS) 기반 개인화는 별도 경계, 권한, 감사 로그를 가져야 한다.
4. 상담원을 줄이는 것이 아니라 **반복 문의는 자동 처리하고 예외·고위험 문의는 담당자에게 맥락과 함께 이관**하는 것이 성공 패턴이다.
5. 사용량이나 대화 수만으로 성공을 선언하면 안 된다. 최근 장기 RCT에서는 시간 민감 행정 과업 완료는 좋아졌지만 학업성취·지속에는 효과가 없었고, 별도 RAG 챗봇 RCT에서도 학습 결과의 유의미한 개선이 없었다. KNU-Ask는 “정답 근거율, 과업 완료율, 이관 품질, 최신성”을 중심으로 평가해야 한다.

KNU-Ask는 이미 공지 원문 링크, 공지 상태, 담당 부서, 변경 감지, 하이브리드 검색, 낮은 근거에서의 답변 거부를 갖춘 좋은 출발점이다. 다음 단계의 우선순위는 생성 모델 교체가 아니라 **첨부파일 수집 완성 → 근거 단위 인용 → 콘텐츠 소유자 검수 → 이관 티켓 → 과업 중심 평가 → 제한적 개인화** 순서가 적절하다.

## 2. 조사 범위와 해석 원칙

- 대학 공식 서비스 페이지와 대학이 발표한 운영 수치를 우선했다.
- 인과 효과는 가능한 경우 무작위 대조시험(RCT) 또는 동료검토 연구로 확인했다.
- 대학 홍보 페이지의 다운로드 수·대화 수·해결률은 유용하지만 자기보고 운영 지표이므로 인과 효과와 구분했다.
- 전통적인 규칙/의도 분류 챗봇, 선제 문자형 챗봇, 생성형 RAG 어시스턴트를 같은 범주로 뭉뚱그리지 않고 세대와 목적을 나누어 비교했다.
- 현재 저장소의 README와 `backend/app/services`, 모델·스키마·테스트, 프런트엔드 흐름을 직접 검토했다.

## 3. 대표 사례

| 대학·서비스 | 주된 문제와 접근 | 확인된 결과 | KNU-Ask에 주는 시사점 |
|---|---|---|---|
| Georgia State University, **Pounce** | 등록 예정자의 FAFSA, 예방접종, 오리엔테이션 등 90개 이상의 입학 과업을 학생별 상태와 연결해 문자로 선제 안내하고, 미응답 질문은 지식베이스 보강에 사용 | 2016 RCT에서 등록 의사가 확인된 처리군의 정시 등록이 3.3%p 증가, summer melt 21% 감소. 대학은 첫 여름 20만 건 이상 답변 및 이후 22% 감소를 보고 | 공지 검색만 제공하지 말고 “이번 주 내가 해야 할 일”이라는 과업 모델로 확장. 전체 학생에게 같은 알림을 보내지 말고 대상·상태·기한으로 타기팅 |
| Arizona State University, **Sunny** | 입학·재학생에게 문자 알림, 일정·지원 자원 안내, 결석 등 신호 기반 넛지 | 대학은 온디맨드 지원과 상담 여력 확보를 제시하지만, 학생 매체 인터뷰에서는 “유용한 알림이나 핵심 자원은 아니다”라는 혼합 반응도 확인 | 친근한 캐릭터와 대량 푸시가 유용성을 보장하지 않는다. 알림 빈도 제어, 구독 해지, 학생에게 실제로 해당하는 메시지인지가 중요 |
| Staffordshire University, **Beacon** | 로그인 기반 모바일/웹/Teams 디지털 가이드. 시간표, 교직원 연락처, 문서 요청, 캠퍼스 길찾기, 복지·도서관·취업 FAQ를 한 채널에서 제공 | 2019년 출범 당시 FAQ 약 400개와 개인화 기능. 현재도 웹·앱·Teams에서 서비스 범위를 명시 | 대화형 검색의 종착점은 답변만이 아니라 문서 발급, 담당자 찾기, 길찾기 등 “행동”이다. 다만 모든 기능을 한 번에 넣기보다 고빈도 행동부터 연결 |
| Deakin University, **Genie → GEM** | Genie는 시간표·과제·도서대출·캠퍼스 정보까지 묶은 개인화 컨시어지. 현재 GEM은 대학이 지원하는 생성형 AI와 신뢰할 수 있는 대학 콘텐츠 접근을 제공 | Genie는 2019년 25,000 다운로드와 피크일 12,000 대화를, 2020년 누적 60,000 다운로드와 연간 110,000 대화를 보고. 현재 GEM은 공식 정책·등록 조언 및 위기 지원은 할 수 없다고 명시 | 통합 편의성은 강력하지만 생성형 AI의 공식 권한을 명시적으로 제한해야 한다. 화면에 “정보 탐색 지원”과 “공식 결정/상담”의 경계를 표시 |
| University of Michigan, **Maizey** | 대학 구성원이 자체 데이터셋으로 RAG 앱을 만들고 Canvas와 연결. 하나의 데이터 프로젝트에서 학생·교직원 등 대상별 앱을 분리 | 대학 차원의 셀프서비스 플랫폼으로 운영. 2026년에는 한 데이터셋에서 여러 목적·대상별 앱을 만드는 구조를 안내 | 중앙 플랫폼과 분산 콘텐츠 소유권을 결합할 수 있다. KNU-Ask도 단일 거대 프롬프트 대신 장학·등록·학사 등 도메인별 정책/검수/응답 템플릿을 분리 |
| UC San Diego, **TritonGPT** | 대학 공개정보 기반의 업무별 Assistant를 제공하고 SSO 및 온프레미스 호스팅으로 기관 통제를 확보 | San Diego Supercomputer Center 내 호스팅, 입력이 기반 모델 학습에 사용되지 않고 다른 사용자가 프롬프트에 접근할 수 없음을 명시 | 개인정보를 다루기 전에 처리 위치, 모델 학습 사용 여부, 보존 기간, 접근 주체를 학생에게 명료하게 공개. 도메인별 Assistant와 권한 범위를 분리 |
| University of Houston, **Shasta** | 11개 학생지원 부서 웹사이트에 단계적으로 공식 챗봇을 배치 | 2025년 10월 초 기준 32,034건 대화, 대학 자체 기준 82% 해결률. Student Business Services에서 10월 4,658개 메시지를 기록했고 부서들은 전화 감소를 보고 | 전면 배포보다 부서별 단계 도입이 안전하다. 단, “해결률” 정의를 재질문 없음인지, 사용자 확인인지, 실제 과업 완료인지 구체화해야 함 |

### 3.1 Georgia State Pounce: 가장 강한 근거는 “대화”가 아니라 “타기팅된 과업 지원”

Pounce의 중요한 구성요소는 자연어 응답만이 아니다. 시스템은 대학의 입학 절차, 학생별 완료 상태, 예상 질문에 대한 초기 답변, 모르는 질문을 학습시키는 운영 절차를 함께 조정했다. 동료검토 RCT는 등록 의사가 이미 있던 학생에게 효과가 집중됐고, 의사가 확인되지 않은 학생에게는 사실상 효과가 없었다고 보고한다. 즉 **같은 봇도 학생 세그먼트와 시점에 따라 효과가 다르다**. [Page & Gehlbach, 2017](https://journals.sagepub.com/doi/10.1177/2332858417749220), [Georgia State 공식 소개](https://success.gsu.edu/reduction-of-summer-melt/)

KNU-Ask에 적용하면 “장학금 알려줘”에 답하는 것에서 끝내지 않고 다음 구조가 필요하다.

- 대상 판정: 학년, 재학 상태, 소속, 캠퍼스, 신청 여부
- 과업 상태: 미확인 → 확인 → 준비 중 → 제출 → 완료/실패
- 기한 이벤트: D-14, D-7, D-1, 마감, 결과 발표
- 다음 행동: 신청 시스템 딥링크, 필요 서류 체크리스트, 담당 부서 이관
- 효과 측정: 답변 열람이 아닌 실제 신청/제출 완료

후속 장기 연구도 교훈을 보강한다. 4년간의 RCT에서 중앙 소유권과 유연한 커뮤니케이션이 지속 운영의 핵심이었고, 효과는 시간 민감 행정 과업 완료에 집중됐으며 학업성취나 재학 지속 효과는 검출되지 않았다. [Mata, Russell & Page, 2026](https://edworkingpapers.com/ai26-1409)

### 3.2 Beacon과 Genie: 학생은 검색창보다 “하루를 정리하는 허브”에 반응한다

Beacon은 FAQ 외에도 시간표, 담당 교원 연락처, 문서 요청, 기기 가용성, 동아리, 캠퍼스 길찾기를 제공하며 웹·앱·Teams에 존재한다. [Staffordshire 공식 Beacon 페이지](https://www.staffs.ac.uk/students/digital-services/beacon), [출범 발표](https://www.staffs.ac.uk/news/2019/01/introducing-beacon-a-digital-friend-to-staffordshire-university-students)

Deakin Genie의 상위 질문은 과제, 시간표, 강의 자료, 학사일정, 다음 일정, 도서 대출처럼 학생의 즉시 행동과 연결됐다. 초기 대학 발표는 25,000명 다운로드와 피크일 최대 12,000 대화를 보고했고, 2020년 연차보고서는 60,000회 이상 다운로드와 연간 110,000회 대화를 기록했다. [Deakin 2019 발표](https://www.deakin.edu.au/about-deakin/news-and-media-releases/articles/deakins-genie-a-virtual-digital-assistant-out-of-the-bottle), [Deakin 2020 연차보고서](https://ww3.deakin.edu.au/__data/assets/pdf_file/0006/2311494/2020-deakin-annual-report.pdf)

그러나 현재 Deakin GEM은 신뢰할 수 있는 대학 웹·지식 문서를 검색할 수 있음에도 “공식 정책·등록·학업 요건 조언”과 “복지·위기 지원”은 할 수 없다고 선을 긋는다. 이는 생성형 UI가 넓어질수록 **공식성 경계와 고위험 이관이 더 중요해진다**는 사례다. [Deakin GEM 안내](https://www.deakin.edu.au/students/study-support/study-resources/artificial-intelligence/approved-genai-tools-for-learning/deakin-gem)

### 3.3 Maizey, TritonGPT, University of Arizona: 거대 단일 봇보다 대학 AI 플랫폼 + 업무별 Assistant

Michigan Maizey는 구성원이 자체 데이터셋을 연결해 지식 기반 챗봇을 만들고 Canvas에 연결하도록 한다. 2026년 안내에서는 데이터와 설정을 담은 프로젝트 하나에서 학생·교직원 또는 목적별 여러 앱을 분리하는 방향을 제시한다. [U-M Maizey 상세 안내](https://its.umich.edu/computing/ai/maizey-in-depth), [Canvas 연동](https://its.umich.edu/computing/ai/canvas-maizey-integration)

UC San Diego는 공개 대학정보 기반의 업무별 Assistant와 범용 Assistant를 나누고, SSO와 기관 내 호스팅을 강조한다. 공식 FAQ는 사용자 입력이 기반 모델을 학습시키지 않으며 다른 사용자가 볼 수 없다고 설명한다. [TritonGPT 안내](https://tritonai.ucsd.edu/tritongpt/index.html), [TritonGPT FAQ](https://tritonai.ucsd.edu/training-resources/faq.html)

University of Arizona도 Housing, Graduate College처럼 서비스별 Assistant를 공개한다. [University of Arizona AI Chatbot Services](https://chatbot.d2l.arizona.edu/)

이 패턴은 KNU-Ask에 다음과 같이 적용할 수 있다.

- 공통 플랫폼: 인증, 로그, 검색, 모델 게이트웨이, 관측, 안전정책
- 도메인 앱: 장학/등록/수강/병무/취업별 데이터 소유자, 검색 필터, 응답 형식, 이관 부서
- 채널 앱: 공개 웹, 로그인 포털, 모바일, 교직원용 검수 화면
- 권한 분리: 공개 공지 Assistant와 학생 개인 기록 Assistant를 별도 인덱스·서비스 계정으로 운영

### 3.4 University of Houston Shasta: 부서별 단계 배포와 운영 지표

Shasta는 2024년 조달·개발을 시작해 학생재정, 입학, 등록 등 11개 부서 사이트로 단계 확대했다. 대학은 2025년 10월 초 기준 32,034개 대화와 82% 해결률, 고객 서비스 전화 감소를 발표했다. [University of Houston 운영 사례](https://www.uh.edu/af/news/press-releases/releases-articles/2025/oct-25/shasta-chatbot-ready-to-answer-questions.php), [서비스 로드맵](https://www.uh.edu/infotech/aisolutions/services-solutions/shastachatbot/)

이 수치는 유망하지만 대학 자체 정의의 해결률이다. KNU-Ask에서는 최소한 다음 세 지표를 분리해야 한다.

- `answer_shown_rate`: 답변을 표시했는가
- `user_confirmed_resolution_rate`: 사용자가 해결됐다고 확인했는가
- `task_completion_rate`: 신청·제출·발급 등 실제 과업이 완료됐는가

## 4. 성공 요인

### 4.1 좁고 측정 가능한 문제에서 시작

등록 이탈 감소, 기한 내 서류 제출, 반복 FAQ 감소처럼 성공 조건이 분명해야 한다. Pounce의 효과가 등록 의사가 확인된 집단에 집중됐다는 사실은 범용화보다 정밀 타기팅이 중요함을 보여준다.

### 4.2 공식 데이터와 최신성 책임자를 함께 지정

문서를 벡터 DB에 넣는 것으로 끝나지 않는다. 각 카테고리마다 다음이 필요하다.

- 콘텐츠 소유 부서와 승인자
- 원문 효력 시작·종료일, 수정일, 적용 대상
- 중복·충돌 공지의 우선순위 규칙
- 답변 오류 또는 “모름”을 검토하는 큐와 SLA
- 크롤링 실패·오래된 인덱스에 대한 경보

### 4.3 답변에서 행동으로 연결

학생지원 질문은 사실 확인보다 “그래서 지금 무엇을 해야 하나”가 목적이다. 답변은 기한, 대상, 필요 서류, 신청 링크, 담당자, 다음 행동을 구조화해야 하며 가능한 경우 바로 신청·예약·문서 발급으로 이어져야 한다.

### 4.4 인간 이관은 실패 처리 기능이 아니라 핵심 기능

챗봇이 낮은 확신으로 답하거나, 여러 정책이 충돌하거나, 개인 예외가 있거나, 정서·위기 신호가 있으면 담당자가 대화 요약과 인용 원문을 넘겨받아야 한다. 이관 후 결과는 지식베이스와 평가셋 개선으로 돌아와야 한다.

### 4.5 학생별 관련성과 알림 통제권

선제 안내는 학생의 현재 상태와 실제 과업에 맞을 때 가치가 있다. ASU Sunny 사례의 혼합 반응은 단순 반복 알림이 주변 소음이 될 수 있음을 보여준다. [ASU 공식 소개](https://news.asu.edu/20190910-sun-devil-life-highly-ranked-first-year-experience-asu-provides-personalized-support), [ASU 학생 매체의 사용자 반응](https://www.statepress.com/article/2023/03/spmagazine-asu-sunny-automated-chatbot)

### 4.6 사용량과 효과를 구분한 평가

2026년 약 500명 규모의 학기 단위 RAG 챗봇 RCT에서는 흥미, 자기효능감, 참여, 성취도 어느 지표에서도 유의미한 효과가 없었다. 이는 좋은 RAG 검색이 곧 학습 성과라는 가정을 경계하게 한다. [Bouvier 외, 2026](https://doi.org/10.1016/j.chbr.2026.101061)

반면 대규모 강의에서 과업별 선제 메시지를 제공한 비생성형 챗봇 연구는 A/B 성적 확률을 4%p 높였고 숙제·보충수업 참여 증가를 보고했다. 핵심 차이는 “챗봇 접근권 제공”이 아니라 구체적 행동을 촉진한 설계다. [Meyer 외, 2024](https://eric.ed.gov/?id=ED672036)

## 5. 실패·정체 요인과 방지책

| 실패·정체 요인 | 나타나는 증상 | KNU-Ask 방지책 |
|---|---|---|
| 범위가 너무 넓음 | 자신 있게 틀린 답, 부서 책임 불명확 | 1차 범위를 공개 공지의 학사 행정으로 고정하고 도메인별 허용 질문을 명시 |
| 데이터가 낡거나 첨부가 누락됨 | 사용자가 원문을 다시 찾아야 함, 날짜·서류 오류 | PDF/HWP/HWPX/이미지 첨부 추출, 원문 수정 감지, 인덱스 신선도 대시보드 |
| “답변 생성”을 “문제 해결”로 오인 | 대화량은 많지만 신청·제출 개선 없음 | 과업 ID와 완료 이벤트를 연결하고 전환 퍼널 측정 |
| 인간 지원과 분리 | 복잡한 질문을 반복하거나 사용자가 포기 | 부서·사유·대화요약·근거를 포함한 티켓 이관과 응답 SLA |
| 낮은 사용 동기 | 선택형 챗봇이 있어도 학생이 찾지 않음 | 포털·공지·모바일의 실제 업무 흐름에 삽입하고, 마감 직전 관련 학생에게 제한적 옵트인 알림 |
| 과도한 알림·가짜 친밀감 | 무시, 차단, 장난성 입력, 신뢰 저하 | 빈도 상한, 조용한 시간, 구독 제어, “AI” 표기, 간결한 행정 톤 |
| 성공 지표 부풀리기 | “응답률/세션 수”만 보고 | 정확성, 근거성, 최신성, 이관, 사용자 확인, 과업 완료를 별도 측정 |
| 개인정보 경계 혼합 | 공개 지식 인덱스에 학생 기록이 복제됨 | 공개/인증/민감 데이터 저장소 분리, 요청 시점 권한 검사, 최소 조회, 감사 로그 |
| 접근성·언어 장벽 | 한국어 표현 변형, 유학생·장애 학생 배제 | 쉬운 한국어, 영어 우선 추가, 키보드/스크린리더 테스트, 핵심 링크와 전화 대안 제공 |
| 공급업체·모델 종속 | 비용 상승, 모델 변경 시 품질 회귀 | 모델 게이트웨이, 골든 질문셋, 임베딩/프롬프트 버전, 데이터 내보내기 계약 |

## 6. 현재 KNU-Ask와의 적합성·간극

### 이미 해외 성공 패턴과 맞는 점

- 답변 근거를 공지로 제한하고 원문 URL을 반환한다.
- 공지의 `active/upcoming/expired/always/unknown` 상태와 종료 경고가 있다.
- 카테고리, 세부 분류, 연도, 학기, 키워드, 임베딩을 결합한 하이브리드 검색을 사용한다.
- 근거 점수가 낮으면 결과를 제외하고 “관련 데이터가 없다”고 응답한다.
- 담당 부서, 전화번호, 운영시간, 신청 기간, 대상, 필요 서류를 구조화할 모델 필드가 있다.
- 본문 해시 기반 변경 감지와 archived 처리, 스키마·임베딩 버전 필드가 있다.

### 운영 전 반드시 메워야 할 간극

1. **근거 단위가 공지 전체이다.** 답변 문장별 문단/페이지 인용과 인용 스니펫이 없어 사용자가 사실을 빠르게 검증하기 어렵다.
2. **첨부파일 본문 추출이 통합되지 않았다.** 대학 공지의 핵심 표·서식·세부 조건이 첨부에 있으면 현재 검색은 중요한 근거를 놓친다.
3. **최신성 실패가 사용자에게 보이지 않는다.** 마지막 성공 수집 시각, 출처별 실패, 데이터 지연 경고가 응답과 운영 화면에 없다.
4. **사람 이관 경로가 없다.** 담당 부서 정보 표시는 있지만 문의 티켓, 운영시간 외 콜백, 대화 요약 전달은 없다.
5. **개인 과업 상태가 없다.** 모든 답변이 공개 공지 기반이며 Pounce식 “나에게 해당하는 미완료 과업”을 지원하지 않는다.
6. **평가·관측 데이터 모델이 없다.** 검색 recall, citation correctness, 무답변, 이관, 사용자 해결 확인, 실제 과업 완료를 기록하지 않는다.
7. **검색 구현이 MVP 수준이다.** 벡터 후보와 단순 키워드 hit를 가중합하며, 문서 청크·정밀 재랭커·충돌 정책·학사 용어 사전이 없다.
8. **캐시 키가 질문 문자열 중심이다.** 향후 사용자·시점·권한·데이터 버전을 고려하지 않으면 개인화 또는 공지 갱신 후 잘못된 답변을 재사용할 수 있다.
9. **프런트엔드의 카테고리/FAQ 선택 흐름이 실제 검색 답변으로 연결되지 않는다.** 현재 선택 후 데이터가 있어도 임시 empty 응답을 추가하는 지점이 남아 있다.
10. **공식 서비스가 아님을 명시한 시연 데이터다.** 실제 도입 전 부서 디렉터리와 공지 API/DB의 공식 연동 및 콘텐츠 승인 체계가 먼저 필요하다.

## 7. 권장 제품·서비스 설계

### 7.1 서비스 범위

초기에는 공개·저위험·고빈도 영역을 다룬다.

- 포함: 학사일정, 등록금, 수강, 휴복학, 장학, 증명서, 병무, 취업 공지 검색
- 조건부: 학생 로그인 후 “내 대상 여부”와 미완료 행정 과업 표시
- 제외 또는 즉시 사람 연결: 학칙 예외 판정, 징계·성적 이의, 법률 조언, 자살·자해 등 위기 상담, 개인별 최종 수혜 자격 결정

### 7.2 권장 대화 결과 형식

1. 한 문장 결론
2. “나에게 해당하는지” 판단에 필요한 조건
3. 기한과 현재 상태
4. 다음 행동과 공식 링크
5. 필요 서류
6. 문장별 근거 인용 및 원문 수정일
7. 담당 부서와 사람에게 문의 버튼
8. 불확실성 또는 충돌 경고

### 7.3 지식 거버넌스

각 문서에 `source_owner`, `authority_level`, `effective_from`, `effective_to`, `last_verified_at`, `supersedes`, `audience`, `campus`, `review_status`를 둔다. 동일 주제의 공지가 충돌하면 최신 게시일만으로 고르지 말고 상위 규정·정정 공지·부서 승인 문서 순으로 우선한다.

### 7.4 개인화 원칙

- 공개 질문에는 로그인 없이 공개 공지만 사용한다.
- 개인화는 학생이 명시적으로 요청할 때 최소 필드만 SIS에서 실시간 조회한다.
- 개인 기록을 벡터 인덱스에 영구 복제하지 않는다.
- 조회 결과는 응답 생성용 단기 컨텍스트로만 사용하고 권한과 목적을 로그에 남긴다.
- 선제 알림은 옵트인, 빈도 상한, 수신 시간, 채널 선택, 즉시 해지를 제공한다.

## 8. 단계별 적용 로드맵

### 0단계: 운영 가능한 근거 검색(0~2개월)

- 공식 공지 소스와 콘텐츠 소유 부서 확정
- 첨부파일 추출과 페이지/문단 단위 청킹
- 답변 문장별 인용, 마지막 수집 시각, 종료/정정 경고
- 200~500개 실제 학생 질문으로 골든셋 구축
- 검색 실패·크롤링 지연·저확신 대시보드

통과 기준 예시: 근거 포함 답변의 citation correctness 98% 이상, 중요 날짜 오류 0건, 골든셋 retrieval recall@5 90% 이상.

### 1단계: 부서 운영과 사람 이관(2~4개월)

- 장학·등록·학사 3개 도메인을 파일럿
- 콘텐츠 승인/정정 화면과 담당자 SLA
- 티켓 생성, 대화·근거 요약, 처리 결과 회수
- 답변별 해결/미해결 피드백과 실패 분류

통과 기준 예시: 사용자 확인 해결률, 반복 문의 감소, 잘못된 부서 이관률, 사람 응답 시간.

### 2단계: 과업 중심 학생지원(4~8개월)

- 학사 캘린더와 신청 시스템 딥링크
- “이번 주 해야 할 일” 체크리스트
- 대상자별 옵트인 마감 알림
- A/B 또는 단계적 도입으로 과업 완료 효과 평가

통과 기준 예시: 대상 학생의 기한 내 신청/제출 완료율, 마감 후 구제 문의 감소. 단순 클릭률은 보조지표로만 사용.

### 3단계: 제한적 개인화(8개월 이후)

- SSO, 목적별 동의, 권한별 실시간 SIS 조회
- 공개 지식 서비스와 개인 데이터 도구 분리
- 개인정보 영향평가, 침해 대응·감사 체계
- 학과/학년/재학 상태별 Assistant 또는 뷰

## 9. 평가 지표 제안

| 층위 | 핵심 지표 |
|---|---|
| 검색 품질 | retrieval recall@k, 정답 근거 순위, 무관 문서율, 오래된 문서 노출률 |
| 답변 안전성 | citation correctness, 무근거 주장률, 중요 날짜/금액 오류, 적절한 답변 거부율 |
| 사용자 경험 | 해결 확인률, 재질문율, 포기율, 이관 선택률, 접근성 과업 성공률 |
| 운영 | 콘텐츠 승인 시간, 크롤링 지연, 이관 SLA, 반복 실패 질문의 수정 리드타임 |
| 학생 성과 | 기한 내 제출·신청·등록 완료율, 누락/구제 요청률, 세그먼트별 격차 |
| 비용 | 해결 건당 모델비용, 자동 해결 건당 운영비, 사람 상담 절감시간 |

모든 지표는 전체 평균뿐 아니라 신입생/재학생, 유학생, 장애 학생, 캠퍼스, 학과, 질문 언어별로 나눠 불균형을 확인해야 한다.

## 10. 최종 권고

KNU-Ask가 따라야 할 해외의 모범은 하나의 제품이 아니라 세 가지 원칙의 결합이다.

- **Pounce의 과업 타기팅:** 누구에게 어떤 마감 행동이 필요한지 명확히 한다.
- **Beacon/Genie의 행동 연결:** 답변을 시간표, 문서, 신청, 담당자 같은 실제 학생 여정과 연결한다.
- **Maizey/TritonGPT의 플랫폼 경계:** 중앙 안전·데이터 플랫폼 위에서 도메인별 Assistant와 권한을 분리한다.

따라서 가까운 목표는 “대학의 모든 것을 답하는 AI”가 아니라 **공식 공지에서 검증 가능한 답을 제시하고, 학생이 다음 행동을 완료하거나 적절한 사람에게 넘어가도록 돕는 신뢰 가능한 행정 내비게이터**여야 한다. 개인화와 선제 알림은 이 기반의 정확성·운영 책임·측정 체계가 검증된 뒤 추가하는 것이 타당하다.

## 참고 출처

- [Georgia State — Reduction of Summer Melt](https://success.gsu.edu/reduction-of-summer-melt/)
- [Page & Gehlbach (2017), How an Artificially Intelligent Virtual Assistant Helps Students Navigate the Road to College](https://journals.sagepub.com/doi/10.1177/2332858417749220)
- [Mata, Russell & Page (2026), Scaling Student Support with Conversational Artificial Intelligence](https://edworkingpapers.com/ai26-1409)
- [Staffordshire University — Beacon](https://www.staffs.ac.uk/students/digital-services/beacon)
- [Deakin University — Genie 운영 사례](https://www.deakin.edu.au/about-deakin/news-and-media-releases/articles/deakins-genie-a-virtual-digital-assistant-out-of-the-bottle)
- [Deakin University — GEM](https://www.deakin.edu.au/students/study-support/study-resources/artificial-intelligence/approved-genai-tools-for-learning/deakin-gem)
- [University of Michigan — Maizey](https://its.umich.edu/computing/ai/maizey-in-depth)
- [UC San Diego — TritonGPT](https://tritonai.ucsd.edu/tritongpt/index.html)
- [University of Houston — Shasta 운영 사례](https://www.uh.edu/af/news/press-releases/releases-articles/2025/oct-25/shasta-chatbot-ready-to-answer-questions.php)
- [Bouvier et al. (2026), AI Chatbots in Higher Education: Comparing Expectations to Evidence](https://doi.org/10.1016/j.chbr.2026.101061)
- [Meyer et al. (2024), Let's Chat: Leveraging Chatbot Outreach for Improved Course Performance](https://eric.ed.gov/?id=ED672036)

