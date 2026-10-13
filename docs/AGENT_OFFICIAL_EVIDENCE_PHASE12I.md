# PHASE 12I — Official evidence verification (offline)

`agent_collect.discovery.evidence.OfficialEvidencePipeline` is a pure assessment step over an existing `SearchResult`, reviewed `Source`, access-policy snapshot, `CollectionRun`, `RawDocument` bytes, `StoredExtraction`, and `QualityVerification`. It makes no network or database calls and does not create or approve a Source. The search title and snippet are leads, never field evidence.

## Trust and access boundary

An official result requires an exact match to a reviewed official Source and a separate reviewer assertion of its organizer/institution relationship and page type. A familiar domain alone does not establish either fact. The reviewed `EvidenceAccessSnapshot` is bound to the Source approval revision and limits origin, path, validity period, robots permission, terms/storage permission, explicit collection permission, and access restrictions. An eligible auto-approval's terms digest must also match. Any missing or stale proof leaves the result in `REVIEW_REQUIRED` with no raw resolution. These snapshots are explicit trusted inputs; PHASE 12I does not fetch robots.txt or terms, infer permission from a page, or persist a new policy record.

The resolver accepts only an existing successful HTML Collection run and RawDocument linked to that Source, a supported storage reference, and bytes matching its SHA-256. It records the SearchResult URL, original-document URL, direct/recorded-redirect relation, content reference and hash, collected time, Source ID, Collection Run ID, and RawDocument ID. A redirect needs the Collection provenance's requested and final URLs. Search-only results without collected raw bytes cannot become verified opportunities.

## Individual opportunities

Existing Processing fields are replayed against the deterministic evidence blocks and links produced from those raw bytes. The required fields are title, organizer, official URL, registration start/deadline, and event start/end; an official registration URL establishes the application method. A linked `QualityVerification` with `VERIFIED` status and same-RawDocument evidence references is required. Missing, malformed, invented, or uncited fields remain `REVIEW_REQUIRED`. Source text is never executed or followed as an instruction.

Recruitment dates and event dates remain separate. `EXACT_TIME` requires explicit timezone-aware times; `DATE_ONLY` uses the existing KST uncertainty rules at opening and closing dates; `UNKNOWN` cannot be called open. An event page with event dates alone is not a currently recruitable individual notice. The fixed THE NEXT AI fixture demonstrates this distinction; it is not a live discovery or a claim that registration is open.

An approved listing's already-collected links can yield same-origin, safe-URL **proposals** with `execution_enabled=false`. Every individual link still needs its own Source Approval and access check before any later collection. A listing is never included in verified-opportunity yield.

Opportunity identity can compare raw SHA-256, Source plus official external ID, canonical URL plus recruitment window, and normalized title plus recruitment window. Confirmed duplicates remain in the audit but do not count as new yield. Existing RawDocuments are untouched. This assessment does not itself schedule or suppress Processing; existing cache and job policy remain responsible for that decision.

`performance_metrics` reports result, official, resolved, verified, active, upcoming, duplicate, and review counts. `evidence_resolution_rate` divides resolved results by official results; `verified_opportunity_yield` divides verified new opportunities by all search results. Empty denominators return zero. Goal feedback now counts verified yield only when an evidence audit explicitly has `evidence_verified=true`; older search-only quality audits cannot inflate that metric.

## Runtime boundary

The assessor is available for offline callers and tests. Production/Preview Discovery is **not wired to provide reviewed access snapshots, stored raw bytes, or Processing verification to this assessor**. No migration, scheduled follow-up, external collection, API call, gate change, or Service Publish change is part of this phase. Live activation would first require trusted policy record persistence/loading and a read-only artifact join with explicit approval and review of the resulting integration.
