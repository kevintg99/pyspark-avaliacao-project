"""Exceções compartilhadas."""


class ProviderError(Exception):
    """Falha recuperável de uma fonte (o pipeline tenta outra)."""


class RateLimitedError(ProviderError):
    """A fonte recusou por limite de requisições/cota."""


class AuthError(ProviderError):
    """Chave inválida/ausente: não adianta tentar de novo nesta execução."""


class UnreachableError(ProviderError):
    """Sem conexão com a fonte (DNS, proxy, rede): desativa a fonte por um tempo."""
