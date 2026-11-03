"""Parâmetros ajustáveis (API em /api/settings), guardados em data/settings.json."""

from pydantic import BaseModel, Field

from . import config

FILE = config.DATA_DIR / "settings.json"


class Settings(BaseModel):
    mic_enabled: bool = True
    # Confiança mínima do YAMNet na classe "Music" (0 a 1)
    music_threshold: float = Field(0.25, ge=0.05, le=0.95)
    # Segundos seguidos de música antes de chamar o Shazam
    music_seconds: int = Field(4, ge=1, le=30)
    # Segundos de áudio enviados ao Shazam
    clip_seconds: int = Field(8, ge=3, le=15)
    # Com música identificada, de quanto em quanto tempo confere se a faixa mudou
    recheck_seconds: int = Field(30, ge=10, le=300)
    # Sem música por esse tempo, a faixa do microfone sai da tela
    silence_seconds: int = Field(15, ge=5, le=120)
    # Pausa (em segundos) que, quando a música volta, indica uma nova faixa: mostra "Ouvindo música..." e identifica de novo
    gap_seconds: int = Field(2, ge=1, le=10)
    # Letra sincronizada das músicas do Spotify
    lyrics_enabled: bool = True
    # Aparelhos do Spotify a ignorar (ex.: o carro): nome exato, sem diferenciar maiúsculas
    ignored_devices: list[str] = []
    # Aparelhos que também vão para a rua (celulares entram sozinhos): com o iPhone fora de casa,
    # o que tocar neles não aparece ao vivo na tela, só no histórico
    portable_devices: list[str] = []
    # Horários em que a tela troca para o tema noturno e volta ao claro (HH:MM)
    night_from: str = Field("19:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    day_from: str = Field("08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # Tela de espera: alterna entre as últimas músicas e as leituras da estante
    books_enabled: bool = True
    idle_rotate_seconds: int = Field(10, ge=5, le=120)
    # Quantas capas de livro na tela, e se os já lidos completam a fila depois dos "lendo"
    books_covers: int = Field(5, ge=1, le=6)
    books_show_read: bool = True


_current: Settings | None = None


def get() -> Settings:
    global _current
    if _current is None:
        try:
            _current = Settings.model_validate_json(FILE.read_text())
        except (FileNotFoundError, ValueError):
            _current = Settings()
    return _current


def update(changes: dict) -> Settings:
    """Valida e salva; lança pydantic.ValidationError se algum valor for inválido."""
    global _current
    _current = Settings.model_validate({**get().model_dump(), **changes})
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    FILE.write_text(_current.model_dump_json(indent=2))
    return _current
