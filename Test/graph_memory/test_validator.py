import unittest

from app.GraphMemory.errors import MemoryOutputValidationError
from app.GraphMemory.schemas import ConsolidationApiRequest, ConsolidationExtraction
from app.GraphMemory.validator import validate_extraction
from Test.graph_memory.test_schemas import valid_request_payload


def valid_extraction_payload():
    return {
        "episodeId": "task-123",
        "summary": "The Careers page headline was approved.",
        "entities": [
            {
                "ref": "entity-careers",
                "type": "document",
                "canonicalName": "Aura Careers Page",
                "aliases": [],
            }
        ],
        "facts": [
            {
                "subjectRef": "entity-careers",
                "predicate": "headline",
                "value": "A newly approved headline",
                "confidence": 0.95,
                "importance": 0.8,
                "sourceObservationIds": ["obs-1"],
                "relation": {"type": "updates", "targetFactId": "fact-old"},
            }
        ],
    }


class ExtractionValidatorTests(unittest.TestCase):
    def setUp(self):
        self.source = ConsolidationApiRequest.model_validate(
            valid_request_payload()
        ).request

    def test_accepts_valid_evidence_and_existing_fact_relationship(self):
        extraction = ConsolidationExtraction.model_validate(valid_extraction_payload())
        validate_extraction(extraction, self.source)

    def test_rejects_unknown_evidence(self):
        payload = valid_extraction_payload()
        payload["facts"][0]["sourceObservationIds"] = ["missing-observation"]
        extraction = ConsolidationExtraction.model_validate(payload)

        with self.assertRaisesRegex(MemoryOutputValidationError, "not supplied"):
            validate_extraction(extraction, self.source)

    def test_rejects_relation_without_stable_existing_subject_ref(self):
        payload = valid_extraction_payload()
        payload["entities"][0]["ref"] = "temporary-ref"
        payload["facts"][0]["subjectRef"] = "temporary-ref"
        extraction = ConsolidationExtraction.model_validate(payload)

        with self.assertRaisesRegex(MemoryOutputValidationError, "subjectEntityId"):
            validate_extraction(extraction, self.source)

    def test_rejects_secret_like_output(self):
        payload = valid_extraction_payload()
        payload["facts"][0]["value"] = "sk-abcdefghijklmnopqrstuvwxyz123456"
        extraction = ConsolidationExtraction.model_validate(payload)

        with self.assertRaisesRegex(MemoryOutputValidationError, "credential-like"):
            validate_extraction(extraction, self.source)

    def test_requires_scoped_external_identifiers(self):
        payload = valid_extraction_payload()
        payload["entities"][0]["aliases"] = [
            {"type": "slack_id", "value": "U123"}
        ]
        extraction = ConsolidationExtraction.model_validate(payload)

        with self.assertRaisesRegex(MemoryOutputValidationError, "scope:id"):
            validate_extraction(extraction, self.source)


if __name__ == "__main__":
    unittest.main()
