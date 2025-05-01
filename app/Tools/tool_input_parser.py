class ToolInputParser:
    """
    A utility class responsible for parsing structured or raw inputs provided to tools.
    """
    @staticmethod
    def parse(inputs: dict | str):
        """
        Parse the Input that need to provide to the Tools/Subagents

        Returns:
        dict: A normalized dictionary with the following keys:
            - "query": The main query string (required)
            - "screenshot": A base64 or URL string of an image (optional)
            - "chat_history": History context for the LLM (optional)
            - "system_info": Any additional system metadata (optional)

        Raises:
        ValueError: If a query is not found in the input.
        """
        if isinstance(inputs, str):
            return {
                "query": inputs,
                "screenshot": None,
                "chat_history": None,
                "system_info": None
        }

        else:
            input_data = inputs.get("input")
            query, screenshot = None, None

            if isinstance(input_data, str):
                query = input_data
            elif isinstance(input_data, list):
                for item in input_data:
                    if item["type"] == "text":
                        query = item["text"]
                    elif item["type"] == "image_url":
                        screenshot = item["image_url"]["url"]
            
            if not query:
                raise ValueError("Query noyt found in supervisor")

            return {
                "query": query,
                "screenshot": screenshot,
                "chat_history": inputs.get("chat_history_for_llm"),
                "system_info": inputs.get("system_info") or None
            }