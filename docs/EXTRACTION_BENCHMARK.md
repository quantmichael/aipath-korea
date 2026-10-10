# Conference Extraction Quality Benchmark — PHASE 5.6 (2026-10-04 KST)

## 고정 조건과 해석 범위

이 보고서는 모델·프롬프트·validator를 조정하기 **전**의 `conference-grounded-v2` 순수 성능이다. 모델은 `gpt-5.4-nano`, prompt는 `conference-grounded-2026-10-04-v2`, 기본 예산은 일 5회·월 100회다. 기존 ICCAI v2 결과 1건을 기준선으로 재사용하고, 신규 공식 페이지 4건은 기존 Source Registry → CollectionRun → Generic HtmlCollector → HTTP GET → LocalRawContentStorage → RawDocument → ExtractionBudgetGuard → OpenAIExtractionProvider → `agent_candidates` 순서로 처리했다. 새 페이지마다 HTTP 수집 1회·LLM 호출 1회, 자동/수동 재시도 0회다. 다섯 후보는 모두 `REVIEW_REQUIRED`; 서비스 게시·Service Table 변경·migration은 없다. 신규 Source 4건은 `runtime_benchmark_phase56_*`로 표기하고 수집 후 `HOLD/is_active=false`로 두었다.

원문 Ground Truth는 이 작업에서 저장한 HTML과 공개 공식 페이지에 실제 기재된 것만 사용했다. 신청 URL은 행사 등록으로 안전하게 귀속 가능한 링크만 인정한다. `registration_start/deadline`은 논문 투고·저자 등록 일자가 아닌 **참가 등록** 일정이다. `format`은 장소 추측이 아니라 in-person/online/hybrid가 명시된 경우에만 정답으로 표시한다. `official_url`은 수집한 공식 페이지 URL로 평가한다. AAAI의 `Registration Information` 링크는 참가 등록 페이지이므로 등록 URL로 인정했고, IEEE 페이지의 다른 행사 도메인으로 보이는 등록 링크는 이 페이지의 참가 등록 URL로 확정하지 않았다.

## Benchmark Sources와 provenance

| Source | 유형·구조·공식성 | Source / Run / RawDocument / Candidate ID | 원문 크기·토큰 입력 | 결과 |
|---|---|---|---|---|
| [ICCAI 2027](https://www.iccai.net/) | 독립 공식 사이트, 날짜 조각·등록 외부 링크 | `d0162f63-a840-41bf-9a51-a4d246eae0af` / `48156a62-8324-44a0-bcd3-6c5dd3bd12da` / `b89f68e5-afd2-4206-846f-d0337c41a3c1` / `ec29ff04-9a4e-4b0c-8d85-93a0c2f22f3f` | 22,590 B; 3,048 input tokens | 9/20 valid |
| [AAAI-27](https://aaai.org/conference/aaai/aaai-27/) | 주최 학회 `aaai.org` 공식 CMS, 긴 HTML·많은 링크 | `2f424f79-09ff-4816-b047-f5cfd9984aa9` / `f459cdc2-99b1-41cc-972b-653a1694f053` / `debcb4aa-956e-4624-89dd-f4a97ed928e8` / `8c507bc3-cd02-471a-92cc-cbe4663d9469` | 197,037 B; 3,151 tokens | 4/20 valid |
| [ECAI 2027](https://ecai2027.org/) | 행사 공식 독립 사이트, 유럽식 날짜·복수 주최기관·장소 | `cb1a9457-ca55-4281-b4ae-c27283f6b881` / `9593e54a-77c8-4e34-b9e2-0775b4a424c9` / `0e2b01a3-557a-46d5-913e-9d594866513b` / `5477387f-cf27-45b3-80cf-967a0d30967c` | 24,870 B; 2,347 tokens | 5/20 valid |
| [IJCAI 2027](https://2027.ijcai.org/) | IJCAI 공식 연도별 사이트, 짧은 본문·본행사/위성행사 날짜 | `301a9a13-326b-4f8f-a94c-3df92c1ec672` / `b6c573de-0361-4368-9ff7-c2cba9bdf098` / `9fdaa39d-0334-4098-bdd4-9d9457e8237f` / `8cff75a8-94a3-4c8e-83ac-8cc1e19fd09a` | 123,748 B; 1,632 tokens | 7/20 valid |
| [IEEE CAI 2027](https://cai.ieee.org/2027/) | IEEE 공식 사이트의 정보 적은 페이지, 연도 경계 날짜·장소 | `96d0bc4f-5e98-4507-9e78-2b6648da225c` / `18f778a8-99f2-4898-9dd8-0eec3147620d` / `7d925778-2985-42bf-89cd-c6a30a4d4951` / `b761f645-d858-46f0-8ee9-154b37fc0b0f` | 73,420 B; 1,984 tokens | 7/20 valid |

AAAI의 `input_truncated=true`는 **링크 일부 선택 제외** 때문이며, 전처리 본문 3,147자는 입력에 모두 들어갔다. 다른 네 문서는 선택 입력이 잘리지 않았다. 다섯 페이지는 검색 결과·뉴스 요약이 아닌 주최 기관 또는 행사 자체의 공개 HTML이다.

## 수동 Core Field Ground Truth

Core는 `title, organizer, official_url, registration_url, registration_start, registration_deadline, event_start, event_end, country, city, venue, format, fee`의 13개다. 아래 표에 없는 Core 필드는 해당 **페이지**에 명확한 정보가 없어 `SOURCE_MISSING`으로 주석화했다. URL은 위 공식 Source 링크를 `official_url` 정답으로 사용한다.

| Source | 원문에 있는 Core Ground Truth (`official_url` 포함) | 존재 13개 중 |
|---|---|---:|
| ICCAI | title=ICCAI 2027; organizer=Yonsei University; registration_url=`http://confsys.iconf.org/register/iccai2027`; registration_deadline=2026-12-20; event=2027-04-23~26; country=South Korea; city=Seoul | 9 |
| AAAI | title=The 41st Annual AAAI Conference on Artificial Intelligence; organizer=Association for the Advancement of Artificial Intelligence(공식 페이지 헤더); registration_url=`https://aaai.org/conference/aaai/aaai-27/aaai-27-registration/`; event=2027-02-16~23; country=Canada; city=Montréal | 8 |
| ECAI | title=ECAI 2027 / European Conference on Artificial Intelligence; organizer=EurAI·EETN·기재된 공동 기관; event=2027-10-02~07; country=Greece; city=Athens; venue=Megaron Athens International Conference Center; format=in person | 9 |
| IJCAI | title=IJCAI 2027; event=2027-08-07~13 (Kyoto 본행사); country=Japan; city=Kyoto. 위성행사 Hengqin 날짜는 본행사 일자로 합치지 않음 | 6 |
| IEEE CAI | title=IEEE CAI 2027; organizer=IEEE(공식 페이지 브랜딩); event=2027-11-30~12-02; country=Singapore; city=Singapore; venue=Marina Bay Sands | 8 |

특히 ECAI 페이지의 논문 제출일, IJCAI 페이지의 초록/논문 제출 마감일, AAAI의 저자 등록일을 **참가 등록 마감일**로 오인하지 않았다. IEEE 페이지의 `Registration Fees & Benefits` 링크는 `2027.ieeespsummit.org`로 향해 CAI 행사 등록 URL로 확정하지 않았다. `SOURCE_MISSING`은 행사 전체에 그 정보가 없다는 뜻이 아니라, **수집한 단일 페이지에서 확인되지 않았다는 뜻**이다.

## 지표와 정의

Core Precision은 `수동 Ground Truth와 일치한 validated core / validated core`, Core Recall은 `일치한 validated core / source-present core`다. URL 끝 슬래시·행사명의 명백한 약칭은 동일 대상으로 수동 판단했다. Evidence Pass Rate는 `VALID 상태의 필드 / 모델이 raw value 또는 근거 ID를 낸 필드`이며 Core/optional을 함께 센다. 모델이 아무 값도 내지 않은 필드는 이 분모에서 제외한다. 확정된 hallucination attempt는 **원문에 없는 사실을 모델이 내고 validator가 막은 사례**다. 의역·복합 문장이 단순 문자열 validator에서 탈락했다고 모두 환각으로 세지 않았다. Unsupported Value Count도 확인 가능한 원문 반례가 있는 경우만 센다.

| 지표 | ICCAI | AAAI | ECAI | IJCAI | IEEE | 합계 |
|---|---:|---:|---:|---:|---:|---:|
| Source-present Core | 9 | 8 | 9 | 6 | 8 | 40 |
| Correct validated Core | 9 | 4 | 5 | 4 | 5 | 27 |
| Incorrect validated Core | 0 | 0 | 0 | 0 | 0 | 0 |
| Extraction Miss (source-present→null) | 0 | 4 | 4 | 2 | 3 | 13 |
| Source Missing (core) | 4 | 5 | 4 | 7 | 5 | 25 |
| Evidence attempted / passed | 11 / 9 | 7 / 4 | 12 / 5 | 9 / 7 | 10 / 7 | 49 / 32 |
| Validated all fields / 20 | 9 | 4 | 5 | 7 | 7 | 32 / 100 |
| REVIEW_REQUIRED | 1 | 1 | 1 | 1 | 1 | 5 / 5 |

- **Core Precision: 27/27 = 100%**, 이 5페이지의 수동 Ground Truth 기준이다. 작은 표본의 무오류를 일반 정확도로 외삽하지 않는다.
- **Core Recall: 27/40 = 67.5%**. Source Missing 25칸은 Recall 분모에서 제외한다.
- **Evidence Validation Pass Rate: 32/49 = 65.3%**. 이는 사실 정확도 지표가 아니라 현 validator 통과율이다.
- **확정 Hallucination Attempts 0, 확정 Unsupported Values 0**. 불일치한 긴 요약·태그에는 의역/조합이 있어 자동으로 환각 판정하지 않았다. 전체 환각 부재를 증명하지는 않는다.
- REVIEW_REQUIRED 비율 **5/5 = 100%**. 어떤 후보도 검증·게시 승인을 받은 것이 아니다.

## 20-field Quality Matrix

`V=VALID`, `M=MISSING`, `VM=VALUE_MISMATCH`, `DM=DATE_MISMATCH`, `EL=EVIDENCE_LIMIT_EXCEEDED`, `ME=MISSING_EVIDENCE`. `M`은 모델 결과의 상태이며 Source Missing과 동일하지 않다. Core의 Source Missing/Extraction Miss는 위 수동 Ground Truth 표와 결합해서 판정한다. Optional 필드에는 완전한 수동 Ground Truth를 만들지 않았으므로 Core Precision/Recall에 넣지 않는다. 모든 문서의 전체 후보 상태는 `REVIEW_REQUIRED`다.

| Field | ICCAI | AAAI | ECAI | IJCAI | IEEE |
|---|---:|---:|---:|---:|---:|
| title | V | VM | V | V | V |
| organizer | V | M | VM | M | M |
| summary | VM | VM | EL | V | VM |
| description | ME | M | VM | V | V |
| official_url | V | M | V | V | V |
| registration_url | V | M | M | M | M |
| registration_start | M | M | M | M | M |
| registration_deadline | V | M | M | M | M |
| event_start | V | V | DM | DM | DM |
| event_end | V | V | DM | DM | V |
| country | V | V | V | V | V |
| city | V | V | V | V | M |
| venue | M | M | V | M | V |
| format | M | M | M | M | M |
| language | M | M | M | M | M |
| fee | M | M | M | M | M |
| currency | M | M | M | M | M |
| eligibility | M | M | M | M | M |
| category | M | M | VM | V | V |
| tags | M | M | VM | M | VM |

아래 상세 표는 모델이 값 또는 근거 ID를 낸 49칸을 **원시값/검증값/ID/검증 상태**로 기록한다. 나머지 51칸은 위 매트릭스의 `M`이며 `raw=null`, `validated=null`, 근거 ID 없음이다. 원시값이 긴 경우에도 값을 임의로 단축하지 않았다. 원문 근거 텍스트와 hash는 각 `agent_candidates.extracted_payload` 및 RawDocument에서 재조회할 수 있다.

### ICCAI — 11 attempted, 9 validated

| Field | Raw value | Validated value | Evidence IDs | Validation |
|---|---|---|---|---|
| title | ICCAI 2027 | ICCAI 2027 | B001/B010/B011/B009 | VALID |
| organizer | Yonsei University | Yonsei University | B012 | VALID |
| summary | ICCAI 2027 (13th International Conference on Computing and Artificial Intelligence) will be held in Seoul, South Korea during April 23–26, 2027. | — | B011/B012/B005/B006/B010 | VALUE_MISMATCH |
| description | ICCAI 2027 is an international conference devoted specifically to facilitate synergies in research and development in the areas of Computing and Artificial Intelligence, providing a communication platform for experts and scholars from around the world. | — | B016/B017/B018/B019/B020/B021 | MISSING_EVIDENCE |
| official_url | — | https://www.iccai.net/ | B000/L000 | VALID via L000 |
| registration_url | — | http://confsys.iconf.org/register/iccai2027 | L007 | VALID |
| registration_deadline | Dec. 20, 2026 | 2026-12-20 | B038 | VALID |
| event_start | April 23, 2027 | 2027-04-23 | B005/B039 | VALID |
| event_end | April 26, 2027 | 2027-04-26 | B005/B039 | VALID |
| country | South Korea | South Korea | B001/B006 | VALID |
| city | Seoul | Seoul | B001/B006 | VALID |

`B000`은 실제 블록이 아닌 모델의 불필요한 ID다. 이 저장 후보는 유효한 링크 `L000`으로만 URL을 검증했고, 이후 코드가 URL 필드의 불필요한 블록 ID를 제거하지만 이 기존 행은 덮어쓰지 않았다.

### AAAI — 7 attempted, 4 validated

| Field | Raw value | Validated value | Evidence IDs | Validation |
|---|---|---|---|---|
| title | The 41st Annual AAAI Conference on Artificial Intelligence (AAAI-27) | — | B016/B073 | VALUE_MISMATCH |
| summary | The Forty-First AAAI Conference on Artificial Intelligence (AAAI-27) will be held in Montréal, Canada, from February 16 to February 23, 2027. | — | B073/B017/B016/B070 | VALUE_MISMATCH |
| official_url | — | — | B001/L000 (both supplied as block IDs; link IDs empty) | MISSING |
| event_start | February 16, 2027 | 2027-02-16 | B017/B069/B073 | VALID |
| event_end | February 23, 2027 | 2027-02-23 | B017/B069/B073 | VALID |
| country | Canada | Canada | B017/B073 | VALID |
| city | Montréal | Montréal | B017/B073 | VALID |

AAAI의 `L000`은 입력 링크로 존재했으나 모델이 `link_ids` 대신 `evidence_block_ids`에 넣어 URL을 놓쳤다. Organizer의 학회명은 수집 HTML에 있으나 navigation/header 제거 후 선택 본문에는 완전한 이름이 없어 전처리 손실도 있다.

### ECAI — 12 attempted, 5 validated

| Field | Raw value | Validated value | Evidence IDs | Validation |
|---|---|---|---|---|
| title | ECAI 2027 – European Conference on Artificial Intelligence | ECAI 2027 – European Conference on Artificial Intelligence | B001 | VALID |
| organizer | European Association for Artificial Intelligence (EurAI); Hellenic Artificial Intelligence Society (EETN); National and Kapodistrian University of Athens; University of Pireus; National Centre of Scientific Research "Demokritos" | — | B044/B045/B046/L002/L003/L004/L005/L006 | VALUE_MISMATCH |
| summary | ECAI 2027 is held in person in Athens and includes technical papers plus AI-related events, workshops, tutorials, and sister conferences for starting researchers (STAIRS). | — | B031/B032/B033/B034/B035/B036 | EVIDENCE_LIMIT_EXCEEDED |
| description | The programme will include workshops, tutorials and the Doctoral Consortium; the main conference includes PAIS; the pre-conference is in Athens at School of Science–NKUA; the main conference is at Megaron Athens International Conference Center. | — | B024/B029/B012/B023/B028 | VALUE_MISMATCH |
| official_url | — | https://ecai2027.org/ | L000 | VALID |
| event_start | 2 October 2027 | — | B005 | DATE_MISMATCH |
| event_end | 7 October 2027 | — | B005 | DATE_MISMATCH |
| country | Greece | Greece | B006 | VALID |
| city | Athens | Athens | B006 | VALID |
| venue | Megaron Athens International Conference Center | Megaron Athens International Conference Center | B028 | VALID |
| category | Artificial Intelligence research conference | — | B001 | VALUE_MISMATCH |
| tags | AI; European Conference on Artificial Intelligence; ECAI 2027 | — | B001 | VALUE_MISMATCH |

### IJCAI — 9 attempted, 7 validated

| Field | Raw value | Validated value | Evidence IDs | Validation |
|---|---|---|---|---|
| title | IJCAI 2027 | IJCAI 2027 | B001 | VALID |
| summary | Welcome to IJCAI 2027, the 36th International Joint Conference on Artificial Intelligence, the premier international gathering of researchers in AI! | 동일 원문 | B004 | VALID |
| description | IJCAI 2027 will take place in Kyoto, Japan, from August 7–13, 2027, with a satellite event in Hengqin, China, from August 15–17, 2027. | 동일 원문 | B005 | VALID |
| official_url | — | https://2027.ijcai.org/ | L000 | VALID |
| event_start | August 7–13, 2027 | — | B005 | DATE_MISMATCH |
| event_end | August 7–13, 2027 | — | B005 | DATE_MISMATCH |
| country | Japan | Japan | B005 | VALID |
| city | Kyoto | Kyoto | B005 | VALID |
| category | International Joint Conference on Artificial Intelligence | 동일 원문 | B004 | VALID |

### IEEE CAI — 10 attempted, 7 validated

| Field | Raw value | Validated value | Evidence IDs | Validation |
|---|---|---|---|---|
| title | IEEE CAI 2027 | IEEE CAI 2027 | B001/B009 | VALID |
| summary | IEEE CAI 2027 is an international conference and exhibition with an emphasis on the applications of AI and key AI verticals that impact industrial technology applications and innovations. | — | B017 | VALUE_MISMATCH |
| description | The IEEE Conference on Artificial Intelligence (IEEE CAI) is an international conference and exhibition with an emphasis on the applications of AI and key AI verticals that impact industrial technology applications and innovations. | 동일 원문 | B017 | VALID |
| official_url | — | https://cai.ieee.org/2027 | L007 | VALID |
| event_start | 2027-11-30 | — | B008 | DATE_MISMATCH |
| event_end | 2027-12-02 | 2027-12-02 | B008 | VALID |
| country | Singapore | Singapore | B008 | VALID |
| venue | Marina Bay Sands | Marina Bay Sands | B008 | VALID |
| category | Conference and exhibition | 동일 원문 | B017 | VALID |
| tags | applications of AI, AI verticals, industrial technology | — | B017 | VALUE_MISMATCH |

## Failure Classification와 의사결정

| 분류 | 관찰된 사례와 범위 | 판단 |
|---|---|---|
| A Model Extraction | AAAI organizer/registration URL 누락, ECAI `in person` 누락, IEEE Singapore city/organizer 누락 | 본문에 있는 Core 정보도 null인 사례가 있다. 단일 원인을 모델 성능으로 확정할 수는 없다. |
| B Preprocessor | AAAI 완전한 학회명은 수집 HTML header에 있지만 전처리 본문에서 제거됨; ECAI 조직명은 여러 블록에 걸침 | 헤더 선택적 보존과 조직명 블록 경계가 개선 후보. 이번 Phase에서 변경 안 함. |
| C Prompt / ID 선택 | AAAI 공식 URL의 `L000`을 `evidence_block_ids`에 둠; IJCAI start/end 모두 하나의 날짜 범위 문자열 제출; AAAI 제목에 약칭을 덧붙임 | 출력 형식 지시 또는 nano의 준수 문제. Prompt 변경 없이 재현 기록. |
| D Evidence Resolution | ICCAI 과거 후보의 불필요한 `B000`, ECAI 복수 기관의 여러 링크/블록 혼합 | URL은 링크가 독립 증거이며, 복합 값은 현재 한 문자열/최대 5블록 규칙과 충돌. |
| E Deterministic Validation | ECAI `2–7 October 2027`, IEEE `Nov 30 - Dec 2 2027`, IJCAI 범위 값 모두 날짜 탈락 | 명시적 연도·범위가 있는데도 현재 parser/validator가 처리하지 못한 false negative. 임의 완화가 아닌 범위 양끝의 결정적 파싱이 다음 검토 대상. |
| F Source 정보 부족 | 25/65 Core 슬롯, 특히 참가 등록 시점·수수료·상세 형식 | 단일 페이지에 없는 정보는 실패 분모에서 제외했다. 추가 상세 페이지를 긁지 않았다. |
| G Schema | ECAI 다중 organizer가 단일 문자열, IEEE Singapore의 city/country 동명, 본행사와 위성행사 날짜 분리 | 수집 내용과 20-field schema의 표현 한계를 후속 단계에서 결정해야 한다. |

현재 `gpt-5.4-nano`는 이 고정 v2 벤치마크에서 **Core precision 100%지만 recall 67.5%, 5/5 review**다. 모델을 즉시 바꿔야 한다는 품질 인과 결론은 내리지 않는다. 날짜 false negative는 모델보다 deterministic parser/출력 형식이 큰 원인이다. 다만 [OpenAI Docs의 공식 deprecation 공지](https://developers.openai.com/api/docs/deprecations)에 따르면 `gpt-5.4-nano`는 2026-10-01 deprecated, 2027-04-01 API 중단 예정이고 권장 후속은 `gpt-6-luna`다. 따라서 현 모델은 **이번 비교 기준선으로만 유지**하고, 별도 승인된 후속 Phase에서 후속 모델을 동일 자료·동일 지표로 평가해야 한다. 이번 Phase에서 다른 모델 호출은 0회다. 비용 금액은 계정 과금 내역을 확인하지 않아 추정하지 않는다.

## Token, Storage, Readiness

| Source | Input | Output | Total |
|---|---:|---:|---:|
| ICCAI (PHASE 5.5 기준선 호출) | 3,048 | 664 | 3,712 |
| AAAI (이번 Phase) | 3,151 | 577 | 3,728 |
| ECAI (이번 Phase) | 2,347 | 729 | 3,076 |
| IJCAI (이번 Phase) | 1,632 | 904 | 2,536 |
| IEEE (이번 Phase) | 1,984 | 587 | 2,571 |
| **전체 5문서** | **12,162** | **3,461** | **15,623** |
| **이번 Phase 4호출만** | **9,114** | **2,797** | **11,911** |

원문은 로컬 `tmp/agent-raw-phase5`(ICCAI 1파일)와 `tmp/agent-raw-phase56`(신규 4파일)에 저장돼 있다. 총 **5파일, 441,665 B** (파일시스템 할당량 기준 약 444 KiB)다. 이 경로는 영속 운영 볼륨이 아니고 다른 호스트·배포에서 재조회가 보장되지 않는다. Supabase Storage/S3 전환은 이번 Phase에서 구현하지 않았다.

**PHASE 6 readiness: 보류 권고.** 현재는 같은 페이지에서 날짜가 명시돼도 3/5 새 문서의 시작일이 탈락하고, AAAI는 title·official URL까지 빠졌다. 날짜 범위 파싱, Link-ID/문자열 필드 근거 처리, 표본 확대, 영속 Raw Storage 계획을 별도 범위에서 결정한 뒤 재평가하는 것이 안전하다. PHASE 6 기능은 시작하지 않았다.
