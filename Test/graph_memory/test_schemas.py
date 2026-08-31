import unittest

from pydantic import ValidationError

from app.GraphMemory.schemas import ConsolidationApiRequest


def valid_request_payload():
    return {
        "prompt_version": "aura-memory-v1",
        "request": {
            "schemaVersion": 1,
            "episode": {
                "id": "task-123",
                "taskId": "task-123",
                "query": "Update the Careers page",
                "status": "completed",
                "startedAt": "2026-08-31T10:00:00.000Z",
                "completedAt": "2026-08-31T10:05:00.000Z",
            },
            "observations": [
                {
                    "id": "obs-1",
                    "type": "external_message",
                    "toolName": "slack.search",
                    "sourceId": "message-456",
                    "content": "Priya approved the headline.",
                    "status": "success",
                    "observedAt": "2026-08-31T10:02:00.000Z",
                }
            ],
            "existingFacts": [
                {
                    "id": "fact-old",
                    "subjectEntityId": "entity-careers",
                    "subject": "Aura Careers Page",
                    "predicate": "headline",
                    "object": "Build your future with Aura",
                    "status": "active",
                }
            ],
        },
    }


class ConsolidationSchemaTests(unittest.TestCase):
    def test_accepts_the_public_camel_case_contract(self):
        request = ConsolidationApiRequest.model_validate(valid_request_payload())

        self.assertEqual(request.request.episode.task_id, "task-123")
        self.assertEqual(request.request.existing_facts[0].object_value, "Build your future with Aura")

    def test_rejects_mismatched_episode_and_task_ids(self):
        payload = valid_request_payload()
        payload["request"]["episode"]["taskId"] = "different-task"

        with self.assertRaisesRegex(ValidationError, "must exactly match"):
            ConsolidationApiRequest.model_validate(payload)

    def test_rejects_duplicate_observation_ids_and_unknown_fields(self):
        payload = valid_request_payload()
        payload["request"]["observations"].append(
            dict(payload["request"]["observations"][0])
        )
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            ConsolidationApiRequest.model_validate(payload)

        payload = valid_request_payload()
        payload["unexpected"] = True
        with self.assertRaises(ValidationError):
            ConsolidationApiRequest.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
