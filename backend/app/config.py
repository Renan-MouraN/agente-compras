"""Configuração lida de variáveis de ambiente (ver .env.example na raiz)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    mcp_server_url: str
    mcp_timeout_seconds: float
    openrouter_api_key: str
    openrouter_model: str
    openrouter_base_url: str
    openrouter_timeout_seconds: float
    agent_max_steps: int
    agent_timeout_seconds: float

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.getenv("DATABASE_URL", "postgresql+asyncpg://compras:compras@localhost:5432/compras"),
            mcp_server_url=os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp"),
            mcp_timeout_seconds=float(os.getenv("MCP_TIMEOUT_SECONDS", "30")),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
            openrouter_model=os.getenv("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5"),
            openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            openrouter_timeout_seconds=float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "60")),
            agent_max_steps=int(os.getenv("AGENT_MAX_STEPS", "10")),
            # Prazo total de um pedido (todas as rodadas LLM <-> ferramentas). Deve ficar
            # abaixo do timeout do frontend (150s) para o backend sempre responder primeiro.
            agent_timeout_seconds=float(os.getenv("AGENT_TIMEOUT_SECONDS", "120")),
        )
