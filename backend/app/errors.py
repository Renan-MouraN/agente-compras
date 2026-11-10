"""Erros de aplicação com código estável e mensagem pronta para mostrar no chat."""

from __future__ import annotations


class AppError(Exception):
    def __init__(self, status_code: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status_code = status_code
        self.codigo = codigo
        self.mensagem = mensagem


def llm_nao_configurado() -> AppError:
    return AppError(
        503,
        "llm_nao_configurado",
        "A chave da OpenRouter não está configurada. Defina OPENROUTER_API_KEY no arquivo .env e reinicie o backend.",
    )


def mcp_indisponivel() -> AppError:
    return AppError(
        503,
        "mcp_indisponivel",
        "O servidor de ferramentas (MCP) não está acessível no momento. Tente novamente em instantes.",
    )


def mcp_timeout() -> AppError:
    return AppError(504, "mcp_timeout", "O servidor de ferramentas (MCP) demorou demais para responder.")


def catalogo(codigo: str, mensagem: str) -> AppError:
    """Falha da DummyJSON reportada por uma ferramenta MCP."""
    status = 504 if codigo == "catalogo_timeout" else 503
    return AppError(status, codigo, mensagem)
