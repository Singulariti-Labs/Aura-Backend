from typing import Optional, Dict, List, Any
from langchain_core.language_models.chat_models import BaseChatModel

from app.LLM.memory import Memory, Message
from app.Types.agent_types import SystemInfo, LLMConfig, StepStatus, ROLE_TYPE
from app.helper import update_memory
from langchain_tavily import TavilySearch
from app.Prompts.deep_research import SUMMARIZE_SEARCHED_CONTENT_PROMPT, GAP_DETECTOR_PROMPT, FINAL_ANSWER_GENERATOR_PROMPT, QUERY_MODIFIER_PROMPT
from app.LLM.llm_factory import LLMFactory
from app.Types.agent_types import DeepSearchInputQueries, DeepResearchActionInput, GapDetectionToolInput
from datetime import datetime

import asyncio
import json
import re

class DeepResearchAgent():

    def __init__(self,  llm: BaseChatModel, memory: Optional[Memory] = None, maxTokens: int = 128000):

        self.llm = llm;
        self.max_tokens = maxTokens
        self.shared_memory = memory
        # self.interaction_agent_prompt = DEEP_REAEARCH_PROMPT
        self.max_failure = 4
        self.max_steps = 100
        self.max_actions_per_step = 10
        self.failure_count = 0
        self.interaction_tool_id = None
        self.depth = 5

    async def invoke(self, query: str, tool_call_id: str, base64_image: Optional[str] = None):
        "Invoking the Deep Research Agent to perform research on given query"
        try:
            self.query = query
            self.deep_research_tool_id = tool_call_id
            self.today = datetime.now().strftime("%Y-%m-%d")

            modified_query = await self.get_modified_query(self.query)

            queries = modified_query

            # system_prompt = ChatPromptTemplate.from_messages([
            #     ("system", prompt),
            #     ("human", "{today}"),
            # ])

            result = await self.think(queries=queries)
            update_memory(
                role="tool",
                content=result,
                name="deep_research",
                tool_call_id=self.deep_research_tool_id,
                base64_image=base64_image,
                memory=self.shared_memory
            )

            return result
        except Exception as e:
            return f"[💥 Error] Deep Research Agent Invoke Failed: {str(e)}"

    async def think(self, queries: list[DeepSearchInputQueries]):
        "This method runs the flow in react agent manner in the loop"

        try:
            search_memory = []
            depth = self.depth
       
            for i in range(depth):
                print(f" 🏃‍♂️ Running DEEP RESEARCH AGENT Iteration {i + 1}")

                web_search_input = {"queries": queries, "search_memory": search_memory}
                searched_results = await self.act(web_search_input)

                search_memory = searched_results.get("search_memory")
                content_summary = searched_results.get("summary")

                #Pass to the gap_detection till depth-1
                if i < depth-1:
                    print("🧠 Finding The Gap In Information Recived")
                    gap_detection_input = {"search_memory": search_memory, "user_query": self.query, "summarize_result": content_summary}
                    re_search_queries = await self.gap_detection_tool(gap_detection_input)

                    if re_search_queries == "done":
                        #break the loop
                        print(f"✅ Agent Completed The Task Sucessfully With after {i+1} Iteration With Done Statement")
                        break
                    queries = re_search_queries
                else:
                    print(f"Exiting Task loop Due To Max Depth Reached..., \n Generating Final Answer With Key Findings")
        
            # Make the Final summary 
            final_result = await self.final_answer_generator(search_memory=search_memory, user_query=self.query)

            return final_result

        except Exception as e:
            error_message = f"An error occurred while processing your request: {str(e)}"
            update_memory(role="assistant", content=error_message, memory=self.shared_memory)
            return error_message
            
    async def act(self, input: DeepResearchActionInput):
        "This method is responsible for taking the action on the given queries and web search those queries"
        try:
            queries = input.get("queries")
            search_memory = input.get("search_memory")
            search_tool = TavilySearch(max_results= 7, topic= "general")
            
            loop = asyncio.get_event_loop()
            tasks = [loop.run_in_executor(None, search_tool.invoke, {"query": item["query"]}) for item in queries]
            web_search_result = await asyncio.gather(*tasks)

            query_to_results = {item["query"]: item["results"] for item in web_search_result}


            for item in queries:
                item["results"] = query_to_results.get(item["query"], [])
            
            search_memory.append(queries)

            content_summary = await self.get_content_summary(search_memory=search_memory, user_query = self.query)

            final_result = {
                "search_memory": search_memory,
                "summary": content_summary
            }
            return final_result
        except Exception as e:
            print(f"[💥 Error] occured while web searching in Deep Research Agent: {str(e)}")
            return
    
    async def get_content_summary(self, search_memory: Optional[list[DeepSearchInputQueries]], user_query: str):
        "This method makes the short summary of all the gathered results to find the gap"
        try:
            prompt_template = SUMMARIZE_SEARCHED_CONTENT_PROMPT

            message = prompt_template.format(search_memory=search_memory, user_query=user_query)

            response = await self.llm.ainvoke(message)
            return response.content
    
        except Exception as e:
            print(f"[💥 Error] occured while generating content summary in Deep Research Agent: {str(e)}")
            return
    
    async def gap_detection_tool(self, inputs: GapDetectionToolInput):
        "This method finds the gap in the information recived and on that basis generates new queries to search"

        try:
            prompt_template = GAP_DETECTOR_PROMPT
            
            search_memory = inputs.get("search_memory")
            user_query = inputs.get("user_query")
            summarize_result = inputs.get("summarize_result")

            message = prompt_template.format(search_memory=search_memory, user_query=user_query, summarize_result=summarize_result, today=self.today)

            response = await self.llm.ainvoke(message)

            if response.content != "done":
                queries =  self.extract_json_array(response.content)
                return queries
            else:
                return "done"
        
        except Exception as e:
            print(f"[💥 Error] occured while finding gaps in the received information in Deep Research Agent: {str(e)}")
            return
    
    async def get_modified_query(self, user_query: str):
        """This method takes the user query and modify that query to get most optimised result from the web"""
            
        try:
            prompt_template = QUERY_MODIFIER_PROMPT

            message = prompt_template.format(user_query=user_query, today=self.today)

            response = await self.llm.ainvoke(message)
            queries =  self.extract_json_array(response.content)
            return queries

        except Exception as e:
            print(f"[💥 Error] occured while modifying the user query in Deep Research Agent: {str(e)}")
            return

    async def final_answer_generator(self, user_query: str, search_memory: Optional[list[DeepSearchInputQueries]]):
        "This method creates the Final Answer"
        
        try:
            prompt_template = FINAL_ANSWER_GENERATOR_PROMPT

            message = prompt_template.format(search_memory=search_memory, user_query=user_query)

            response = await self.llm.ainvoke(message)
            return response.content
        
        except Exception as e:
            print(f"[💥 Error] occured while generating final answer in Deep Research Agent: {str(e)}")
            return
    
    def extract_json_array(self, text: str):
        """
        Extracts the first valid JSON array from a given string and returns it as a Python list.
        Raises ValueError if no valid array is found.
        """
        try:
            # First, try direct JSON parsing
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON array using regex
        match = re.search(r'(\[\s*{.*?}\s*\])', text, re.DOTALL)
        if match:
            json_array_str = match.group(1)
            try:
                return json.loads(json_array_str)
            except json.JSONDecodeError as e:
                raise ValueError(f"Found a JSON-like array but couldn't parse it: {e}")

        raise ValueError("No valid JSON array found in the string.")