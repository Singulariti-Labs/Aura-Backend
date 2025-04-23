from app.Types.agent_types import LLMConfig
from langchain_core.language_models.chat_models import BaseChatModel

class ILLMFactory():
    """
    Interface for LLMFactory to create instances of language models.
    """

    def create_llm(llm_config: LLMConfig) -> BaseChatModel:
        """
        Create and return an instance of a chat model based on the provided config.

        Args:
            llm_config (LLMConfig): Configuration specifying the LLM provider and model.

        Returns:
            BaseChatModel: An instance of the appropriate chat model.
        """