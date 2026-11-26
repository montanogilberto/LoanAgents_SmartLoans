"""Delivers Gemini Live tool calls the moment they arrive.

ADK >= 1.30 holds a native-audio tool call until turn_complete, but Gemini now sends
turn_complete only after it gets the tool's response: both wait and the call goes
silent forever (measured 2026-11-24, ADK 2.2.0 and 2.15.0). ADK already does this for
its Gemini 3.x models; here it is for the one we use. Harmless on older versions.
"""
from __future__ import annotations

import asyncio

from google.adk.models.llm_response import LlmResponse
from google.genai import types

_END = object()


def deliver_tool_calls_now(connection, on_customer_text=None):
    """Patches one live connection (not the classes) and returns it.

    on_customer_text(text) gets the input transcription as it arrives, also while a
    tool is running — ADK delivers no event until the tool returns."""
    session = getattr(connection, "_gemini_session", None)
    if session is None:
        return connection
    adk_receive, session_receive = connection.receive, session.receive
    queue: asyncio.Queue = asyncio.Queue()

    async def receive_without_tool_calls():
        async for message in session_receive():
            if message.tool_call:
                queue.put_nowait(LlmResponse(content=types.Content(role="model", parts=[
                    types.Part(function_call=fc) for fc in message.tool_call.function_calls or []])))
                message.tool_call = None
            heard = message.server_content and message.server_content.input_transcription
            if on_customer_text and heard and heard.text:
                on_customer_text(heard.text)
            yield message

    async def pump():
        try:
            async for response in adk_receive():
                queue.put_nowait(response)
            queue.put_nowait(_END)
        except Exception as exc:  # noqa: BLE001 — raised again by the reader
            queue.put_nowait(exc)

    async def receive():
        task = asyncio.create_task(pump())
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    session.receive, connection.receive = receive_without_tool_calls, receive
    return connection
