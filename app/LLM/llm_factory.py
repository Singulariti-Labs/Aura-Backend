from langchain.chat_models import ChatOpenAI, ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel

from app.Types.agent_types import LLMConfig

class LLMFactory:
    @staticmethod
    def create_llm(llm_config: LLMConfig) -> BaseChatModel:
        try:
            if llm_config.provider == "openai":
                return ChatOpenAI(model=llm_config.model_name)
            elif llm_config.provider == "anthropic":
                return ChatAnthropic(model=llm_config.model_name)
            else:
                raise ValueError(f"Unsupported provider: {llm_config.provider}")
        except Exception as e:
            raise RuntimeError(
                f"Error creating LLM instance for provider '{llm_config.provider}' "
                f"with model '{llm_config.model_name}': {str(e)}"
            )