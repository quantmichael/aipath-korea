# PHASE 7.3A — Deterministic Normalization & Extraction Integrity

## 경계와 실행 계약

`RawDocument → ExtractionRunner → agent_candidates.extracted_payload → NormalizationRunner → agent_candidates.normalized_payload → QualityGateRunner`가 수동 실행 경계다. `AgentRuntime.normalization_runner().run(candidate_id)`는 저장된 Agent Candidate의 **검증된 필드만** 읽어 정규화하고 같은 Candidate의 `normalized_payload`와 `provenance.normalization`을 한 번에 기록한다. 재실행 시 입력 hash와 저장 결과가 같으면 읽기만 하고, 다르면 거부한다. Quality Repository는 정규화 입력 hash가 현재 extraction과 다르면 temporal 값을 제공하지 않고 `STALE`로 진단한다. 자동 스케줄러/전체 orchestration은 이 단계의 범위가 아니다.

기존 `agent_candidates.normalized_payload` 및 `provenance` JSONB와 `updated_at`을 사용하므로 migration은 **필요 없고 적용하지 않았다**. 저장 writer는 Agent Candidate만 갱신한다. `updated_at` 조건부 갱신과 이후 readback으로 동시 수정·저장 실패를 감지한다. 기존 Candidate는 재처리하거나 수정하지 않았다.

## Normalization contract

`normalization_version=conference-temporal-v1`, `input_sha256`, `status`, `reason_codes`, `fields`, `event_time_kind`, `timezone_basis`, `application_time_kind`, `application_timezone_basis`를 저장한다. 네 시간 필드 `event_start/end`, `registration_start/deadline` 각각에는 `kind`, ISO calendar `date`, 확인된 경우에만 offset-aware `datetime`, `timezone_basis`, 검토용 `timezone_hint`, 근거 block ID, source URL, 진단 reason이 있다. Gate·PublishCandidate handoff에는 `event_start_at/event_end_at` 및 `application_start_at/application_deadline_at`을 전달한다.

| 입력 근거 | 정밀도 | 시간대 근거 | `*_at` |
| --- | --- | --- | --- |
| 검증된 달력 날짜 | `DATE_ONLY` | 검증된 city+country의 제한적 hint가 있으면 `DERIVED`, 아니면 `UNKNOWN` | `null` |
| 검증된 ISO 시각과 명시적 offset/Z가 cited evidence에 그대로 있음 | `DATETIME_CONFIRMED` | `CONFIRMED` | offset-aware ISO datetime |
| 누락·미검증·잘못된 날짜·모호한 범위 | `UNKNOWN` | `UNKNOWN` | `null` |

도시/국가 기반 `DERIVED`는 표시·검토 힌트이며 실제 행사 시각을 만들지 않는다. 날짜-only에 자정이나 오전 9시를 붙이지 않는다. 기간 순서가 뒤집히면 집계 정밀도는 `UNKNOWN`이고 `*_at`을 내보내지 않는다. 시작/종료 중 제공된 값 하나라도 invalid면 기간 정밀도를 `UNKNOWN`으로 보류한다. `registration_start/deadline`에도 같은 원칙을 적용한다. 정규화는 검증 단계의 점수나 Service Publish 정책을 바꾸지 않는다.

Quality Gate는 기존 결정 순서를 유지한다. Verification이 먼저 REVIEW로 끝나도 저장된 `checks.normalization`에 `MISSING/STALE/NORMALIZED/REVIEW_REQUIRED`, 정밀도·시간대·reason을 남긴다. `PUBLISH_READY`인 경우에만 등록 기간의 확인된 datetime과 정밀도 근거를 `PublishCandidate`에 전달한다.

## Deterministic extraction 수정

연도와 양 끝점이 명시된 동일 월 범위(`August 7–13, 2027`, `2–7 October 2027`) 및 같은 연도의 월 경계 범위(`Nov 30 - Dec 2 2027`)만 양 끝 날짜로 해석한다. 역순·잘못된 달력 날짜·연도 없는 범위는 거부한다. Cited evidence에 그대로 나타나는 offset-aware ISO datetime만 확인된 시각으로 인정한다. URL 필드에서 유효한 `L` ID 하나가 잘못 `evidence_block_ids`에 들어간 경우에는 나머지 ID도 실제 block인지 확인하고 해당 링크를 검증한다. 근거 없는 URL이나 날짜를 생성하지 않는다.

추출 provenance에는 모델이 반환한 원래 `model_document_confidence`, 검증 후 `validated_document_confidence`, `confidence_clamped`를 별도로 기록한다. 기존 `review reason → 최대 0.49` 정책과 Verification 0.75 / Publish 0.85 임계치는 그대로다.

## 기존 5건의 읽기 전용 예상 평가

실제 `extracted_payload`와 로컬 RawDocument를 읽어 현재 validator·normalizer로 다시 계산했다. 아래 결과는 **메모리 계산**이며 Candidate·DB의 기존 값은 바꾸지 않았다. 원래 모델 confidence는 이전 저장 형식에 남아 있지 않아 재계산에는 저장된 0.49를 입력했다. 따라서 점수 회복 여부는 주장하지 않는다.

| Candidate | 새 validator로 바뀌는 값 | 새 temporal 결과 | 여전히 남는 차단 |
| --- | --- | --- | --- |
| ICCAI `ec29ff04…` | 필수 필드 변화 없음 | 행사 `DATE_ONLY/DERIVED`, 접수 `DATE_ONLY` | summary invalid, Source trust UNKNOWN, 같은 RawDocument의 과거 후보와 POSSIBLE_DUPLICATE |
| AAAI `8c507bc3…` | 공식 URL `L000` 복구 | 행사 `DATE_ONLY/UNKNOWN` | title·summary invalid, organizer missing |
| ECAI `5477387f…` | 행사 10/02~10/07 범위 복구 | 행사 `DATE_ONLY/UNKNOWN` | summary·organizer invalid |
| IJCAI `8cff75a8…` | 행사 08/07~08/13 범위 복구 | 행사 `DATE_ONLY/DERIVED` | organizer missing |
| IEEE CAI `b761f645…` | 행사 시작 11/30 복구 | 행사 `DATE_ONLY/UNKNOWN` | summary invalid, organizer missing |

모두 명시된 *날짜*만 확인됐다. 시각·timezone이 명시됐다는 증거는 없으므로 새 코드만 적용해도 다섯 건은 Service Publish 대상이 아니다. 실제 DB의 `normalized_payload`는 계속 비어 있다.

## 남은 차단과 검증

Source 승인/활성화, Service Source 매핑, 검증된 행사·참가 등록 시각의 수집, 남은 summary/organizer/title 근거 문제, 전체 수동 orchestration은 후속 범위다. 미래 행사에서 접수 기간이 없는 경우 현재 Service 공개 상태 resolver는 `scheduled/open`을 결정하지 못한다. 이 단계에서 LLM 호출·Source 활성화·Service DB write는 0회/0건이다. Agent Supabase writer는 mock readback으로 검증했으며 실제 DB write는 이번 단계에서 실행하지 않았다. 기존 120개를 포함한 전체 테스트 결과는 133개 통과다.
