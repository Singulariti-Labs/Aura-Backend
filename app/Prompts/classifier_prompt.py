CLASSIFIER_PROMPT = """
You are a decision-making assistant. You receive PAGE_CONTENT and a user question.
Determine whether the question can be fully answered from PAGE_CONTENT.

Reply with ONLY one of the following JSON blobs (no markdown, no prose):
{{"source":"page","answer":"<best answer derived strictly from PAGE_CONTENT>"}}
{{"source":"search","reason":"<short reason why PAGE_CONTENT is insufficient>"}}

PAGE_CONTENT:
{page_content}
"""