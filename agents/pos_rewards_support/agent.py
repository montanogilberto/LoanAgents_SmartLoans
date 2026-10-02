"""POS Rewards Support Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.pos_rewards_support.prompt import INSTRUCTION
from retrieval.hybrid import hybrid_search
from tools.backend_api import get_reward_balance, get_reward_transactions

pos_rewards_support_agent = Agent(
    name="pos_rewards_support_agent",
    description=(
        "Chat assistant for a POS GMO CLIENT (not staff) asking about their "
        "own loyalty points balance and history from the Rewards Dashboard — "
        "advisory only, always scoped to the clientId in the turn's context, "
        "never another client's data."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_reward_balance),
        FunctionTool(func=get_reward_transactions),
        FunctionTool(func=hybrid_search),
    ],
    output_key="pos_rewards_support_reply",
)
