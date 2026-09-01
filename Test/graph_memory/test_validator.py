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

    def test_accepts_open_ended_entity_alias_predicate_and_relation_values(self):
        payload = valid_extraction_payload()
        payload["entities"][0]["ref"] = "temporary-ref"
        payload["entities"][0]["type"] = "product"
        payload["entities"][0]["aliases"] = [
            {"type": "catalog_identifier", "value": "CAREERS-001"}
        ]
        payload["facts"][0]["subjectRef"] = "temporary-ref"
        payload["facts"][0]["predicate"] = "approved display headline"
        payload["facts"][0]["relation"]["type"] = "derived_from"
        extraction = ConsolidationExtraction.model_validate(payload)

        validate_extraction(extraction, self.source)

        self.assertEqual(extraction.entities[0].type, "product")
        self.assertEqual(extraction.entities[0].aliases[0].type, "catalog_identifier")
        self.assertEqual(extraction.facts[0].predicate, "approved display headline")
        self.assertEqual(extraction.facts[0].relation.type, "derived_from")

    def test_rejects_secret_like_output(self):
        payload = valid_extraction_payload()
        payload["facts"][0]["value"] = "sk-abcdefghijklmnopqrstuvwxyz123456"
        extraction = ConsolidationExtraction.model_validate(payload)

        with self.assertRaisesRegex(MemoryOutputValidationError, "credential-like"):
            validate_extraction(extraction, self.source)

    def test_does_not_restrict_alias_values_by_alias_type(self):
        payload = valid_extraction_payload()
        payload["entities"][0]["aliases"] = [
            {"type": "slack_id", "value": "U123"}
        ]
        extraction = ConsolidationExtraction.model_validate(payload)

        validate_extraction(extraction, self.source)


if __name__ == "__main__":
    unittest.main()
