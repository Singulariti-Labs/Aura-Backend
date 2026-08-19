import unittest

from app.Context.message_groups import (
    AtomicMessageBlock,
    build_atomic_blocks,
    sanitize_messages_for_compressor,
    select_preserved_tail,
    reduce_oversized_tail_block,
)
from app.Context.token_counter import TokenCounter


class AtomicMessageBlockTests(unittest.TestCase):
    def test_parallel_tool_exchange_is_one_atomic_block(self):
        messages = [
            {"sequence": 1, "role": "user", "content": [{"type": "text", "text": "inspect"}]},
            {
                "sequence": 2,
                "role": "assistant",
                "content": [
                    {"type": "tool_call", "tool_call_id": "a", "name": "read", "input": {}},
                    {"type": "tool_call", "tool_call_id": "b", "name": "read", "input": {}},
                ],
            },
            {"sequence": 3, "role": "tool", "tool_call_id": "a", "content": [{"type": "text", "text": "A"}]},
            {"sequence": 4, "role": "tool", "tool_call_id": "b", "content": [{"type": "text", "text": "B"}]},
        ]

        blocks = build_atomic_blocks(messages, TokenCounter())

        self.assertEqual(len(blocks), 2)
        self.assertEqual([m["sequence"] for m in blocks[1].messages], [2, 3, 4])
        self.assertTrue(blocks[1].complete)

    def test_incomplete_tool_exchange_cannot_be_selected(self):
        messages = [
            {
                "sequence": 1,
                "role": "assistant",
                "content": [
                    {"type": "tool_call", "tool_call_id": "a", "name": "read", "input": {}},
                    {"type": "tool_call", "tool_call_id": "b", "name": "read", "input": {}},
                ],
            },
            {"sequence": 2, "role": "tool", "tool_call_id": "a", "content": [{"type": "text", "text": "A"}]},
        ]
        blocks = build_atomic_blocks(messages, TokenCounter())

        with self.assertRaisesRegex(ValueError, "incomplete"):
            select_preserved_tail(
                blocks,
                tail_budget=100,
                soft_ceiling=150,
                min_tail_blocks=3,
            )

    def test_soft_ceiling_keeps_whole_next_block(self):
        blocks = [
            AtomicMessageBlock([{"sequence": 1}], 13000),
            AtomicMessageBlock([{"sequence": 2}], 4000),
        ]

        older, tail = select_preserved_tail(
            blocks,
            tail_budget=14000,
            soft_ceiling=21000,
            min_tail_blocks=1,
        )

        self.assertEqual(older, [])
        self.assertEqual(len(tail), 2)

    def test_minimum_blocks_falls_back_from_three_to_two(self):
        blocks = [
            AtomicMessageBlock([{"sequence": 1}], 70),
            AtomicMessageBlock([{"sequence": 2}], 60),
            AtomicMessageBlock([{"sequence": 3}], 60),
        ]

        older, tail = select_preserved_tail(
            blocks,
            tail_budget=80,
            soft_ceiling=125,
            min_tail_blocks=3,
        )

        self.assertEqual(len(older), 1)
        self.assertEqual([b.messages[0]["sequence"] for b in tail], [2, 3])

    def test_inline_media_is_not_sent_to_compressor(self):
        sanitized = sanitize_messages_for_compressor(
            [
                {
                    "sequence": 9,
                    "role": "tool",
                    "content": [
                        {
                            "type": "image",
                            "media_type": "image/png",
                            "image_url": "data:image/png;base64,VERY_LARGE_DATA",
                        }
                    ],
                }
            ]
        )

        text = sanitized[0]["content"][0]["text"]
        self.assertIn("inline-media:9:0", text)
        self.assertNotIn("VERY_LARGE_DATA", text)

    def test_oversized_latest_tool_block_keeps_ids_but_reduces_output(self):
        messages = [
            {
                "sequence": 1,
                "role": "assistant",
                "content": [
                    {"type": "tool_call", "tool_call_id": "call", "name": "read_file", "input": {}}
                ],
            },
            {
                "sequence": 2,
                "role": "tool",
                "tool_call_id": "call",
                "content": [{"type": "text", "text": "x" * 20000}],
            },
        ]
        counter = TokenCounter()
        original = build_atomic_blocks(messages, counter)[0]

        reduced = reduce_oversized_tail_block(
            original,
            counter=counter,
            target_tokens=500,
        )

        self.assertEqual(reduced.messages[0]["content"][0]["tool_call_id"], "call")
        self.assertEqual(reduced.messages[1]["tool_call_id"], "call")
        self.assertTrue(reduced.messages[1]["output_reduced"])
        self.assertLess(reduced.token_count, original.token_count)


if __name__ == "__main__":
    unittest.main()
