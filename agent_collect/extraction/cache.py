"""Rebind an immutable extraction artifact only after deterministic validation."""

import hashlib
import json
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.extraction.schema import (
    EXTRACTION_VERSION, FIELD_NAMES, ExtractionStatus,
    StoredExtraction, validate_provider_payload,
)
from agent_collect.extraction.prompts import PROMPT_VERSION
from agent_collect.extraction.validation import validate_candidate


class UnsafeCache(ValueError):
    """An existing cache artifact must never trigger a billable fallback."""


def reuse_artifact(cached, *, document, documents, raw, prepared, fingerprint,
                   provider, settings, extracted_at, processing_job_id=None,
                   processing_version=None):
    provenance = cached.provenance
    if not isinstance(provenance, dict) or not isinstance(cached.payload, dict):
        raise UnsafeCache("CACHE_INVALID_ARTIFACT")
    expected = {
        'extraction_version': EXTRACTION_VERSION, 'prompt_version': PROMPT_VERSION,
        'provider': provider.name, 'model': provider.model,
        'content_sha256': document.content_sha256,
    }
    if cached.fingerprint != fingerprint or any(provenance.get(k) != v for k, v in expected.items()):
        raise UnsafeCache('CACHE_KEY_MISMATCH')
    if cached.payload.get('schema_version') != EXTRACTION_VERSION:
        raise UnsafeCache('CACHE_SCHEMA_MISMATCH')
    if cached.status not in {ExtractionStatus.EXTRACTED, ExtractionStatus.REVIEW_REQUIRED,
                             ExtractionStatus.NOT_CONFERENCE}:
        raise UnsafeCache('CACHE_INVALID_ARTIFACT')
    origin = documents.get(cached.raw_document_id)
    if (origin is None or origin.source_id != cached.source_id or
            origin.run_id != cached.run_id or origin.source_url != cached.source_url or
            origin.content_sha256 != document.content_sha256 or
            hashlib.sha256(raw).hexdigest() != document.content_sha256):
        raise UnsafeCache('CACHE_PROVENANCE_MISMATCH')
    original = preprocess_html(raw, source_url=origin.source_url,
                               max_input_chars=settings.extraction_max_input_chars)
    if provenance.get('input_hash') != hashlib.sha256(original.model_input.encode()).hexdigest():
        raise UnsafeCache('CACHE_INPUT_MISMATCH')
    if original.evidence_blocks != prepared.evidence_blocks:
        raise UnsafeCache('CACHE_EVIDENCE_MISMATCH')
    fields = cached.payload.get('fields')
    if not isinstance(fields, dict) or set(fields) != set(FIELD_NAMES):
        raise UnsafeCache('CACHE_INVALID_ARTIFACT')
    selected = {}
    original_links = {link.link_id: link for link in original.link_evidence}
    new_links = {link.link_id: link for link in prepared.link_evidence}
    for name, field in fields.items():
        if not isinstance(field, dict) or field.get('source_url') != origin.source_url:
            raise UnsafeCache('CACHE_PROVENANCE_MISMATCH')
        # A fetched-page L000 may be rebound when the model selected the ID
        # rather than asserted a URL. All other cited links must remain identical.
        for ref in field.get('link_ids', []):
            if ref not in original_links or ref not in new_links:
                raise UnsafeCache('CACHE_EVIDENCE_MISMATCH')
            if ref != 'L000' and original_links[ref] != new_links[ref]:
                raise UnsafeCache('CACHE_EVIDENCE_MISMATCH')
            if ref == 'L000' and origin.source_url != document.source_url and field.get('raw_value') is not None:
                raise UnsafeCache('CACHE_SOURCE_URL_MISMATCH')
        selected[name] = {'value': field.get('raw_value'),
                          'evidence_block_ids': list(field.get('evidence_block_ids', [])),
                          'link_ids': list(field.get('link_ids', [])),
                          'confidence': field.get('confidence')}
    request_payload = {'fields': selected, 'is_conference': cached.payload.get('is_conference'),
                       'document_confidence': cached.payload.get('document_confidence')}
    def validate(content, url):
        return validate_candidate(validate_provider_payload(request_payload, source_url=url),
                                  prepared=content, source_url=url,
                                  max_evidence_chars=settings.extraction_max_evidence_chars)
    try:
        verified_origin = validate(original, origin.source_url)
        candidate = validate(prepared, document.source_url)
    except (ValueError, TypeError, KeyError) as error:
        raise UnsafeCache('CACHE_INVALID_ARTIFACT') from error
    for name, field in fields.items():
        old = verified_origin.fields[name]
        new = candidate.fields[name]
        if field.get('validation_status') == 'VALID':
            if (old.validation_status != 'VALID' or old.value != field.get('value') or
                    old.evidence_text != field.get('evidence_text') or new.validation_status != 'VALID'):
                raise UnsafeCache('CACHE_EVIDENCE_MISMATCH')
        elif new.value is not None:
            raise UnsafeCache('CACHE_INVALID_FIELD_UPGRADE')
    status = (ExtractionStatus.NOT_CONFERENCE if not candidate.is_conference else
              ExtractionStatus.REVIEW_REQUIRED if candidate.review_reasons else ExtractionStatus.EXTRACTED)
    # Existing same-Raw cache hits keep their row and never overwrite provenance.
    if cached.raw_document_id == document.id:
        return cached, candidate, False
    input_hash = hashlib.sha256(prepared.model_input.encode()).hexdigest()
    new_provenance = {k: deepcopy(provenance[k]) for k in expected}
    new_provenance.update({
        'schema_version': EXTRACTION_VERSION, 'extraction_status': status.value,
        'input_hash': input_hash, 'input_truncated': prepared.truncated,
        'extracted_at': extracted_at.isoformat(), 'cache_hit': True,
        'cache_reused': True, 'llm_saved': True,
        'reused_from_raw_document_id': str(origin.id),
        'reused_from_candidate_id': str(cached.id),
        'cached_artifact_sha256': hashlib.sha256(json.dumps(
            {'payload': cached.payload, 'provenance': cached.provenance},
            sort_keys=True, default=str).encode()).hexdigest(),
        'raw_document_id': str(document.id), 'source_id': str(document.source_id),
        'collection_run_id': str(document.run_id),
        'processing_job_id': str(processing_job_id) if processing_job_id else None,
        'processing_version': processing_version,
        'evidence_revalidated': True, 'fetched_page_link_rebound': origin.source_url != document.source_url,
    })
    row = StoredExtraction(
        uuid5(NAMESPACE_URL, f'aipath-extraction:{document.id}:{fingerprint}'),
        document.id, document.run_id, document.source_id, document.source_url,
        fingerprint, status, candidate.as_payload(), new_provenance)
    return row, candidate, True
