from __future__ import annotations

import asyncio
import logging

import chainlit as cl

from rag.retrieve import retrieve_documents
from services.database import initialize_database
from services.llm import generate_answer


logger = logging.getLogger(__name__)


@cl.on_chat_start
async def on_chat_start() -> None:
    try:
        await asyncio.to_thread(initialize_database)
    except Exception as exc:
        logger.exception("Database initialization failed")
        await cl.Message(content=f"Startup error: {exc}").send()
        return
    await cl.Message(content="RAG assistant is ready. Ask me anything.").send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    question = message.content.strip()
    if not question:
        await cl.Message(content="Please ask a question.").send()
        return

    try:
        docs = await asyncio.to_thread(retrieve_documents, question, 3)
        context = "\n\n---\n\n".join(docs) if docs else "No relevant context found."
        answer = await asyncio.to_thread(generate_answer, question, context)
        await cl.Message(content=answer).send()
    except Exception as exc:
        logger.exception("RAG request failed")
        await cl.Message(content=f"Error: {exc}").send()

