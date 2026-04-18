"""
Voice Agent Handler - hello-world

This handler processes events from voice conversations.
Customize the event handlers below to build your agent's behavior.
"""

from primfunctions.events import (
    Event,
    StartEvent,
    TextEvent,
    StopEvent,
    TextToSpeechEvent,
)
from primfunctions.context import Context


async def handler(event: Event, context: Context):
    """
    Main event handler for the voice agent.

    Args:
        event: The incoming event (StartEvent, TextEvent, StopEvent, etc.)
        context: Execution context with session data and utilities

    Yields:
        Response events (TextToSpeechEvent, etc.)
    """

    # Session started - greet the user
    if isinstance(event, StartEvent):
        yield TextToSpeechEvent(
            text="Hello! How can I help you today?",
            voice="brooke",
        )

    # User said something - process their input
    elif isinstance(event, TextEvent):
        user_text = event.data.get("text", "")

        # TODO: Add your conversation logic here
        # Example: Call an LLM, look up data, route to different flows

        yield TextToSpeechEvent(
            text=f"I heard you say: {user_text}",
            voice="brooke",
        )

    # Session ending - say goodbye
    elif isinstance(event, StopEvent):
        yield TextToSpeechEvent(
            text="Goodbye! Have a great day.",
            voice="brooke",
        )
