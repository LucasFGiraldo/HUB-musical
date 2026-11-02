"""Microfone: o YAMNet decide se o som é música e o Shazam identifica a faixa.
Só roda quando o Spotify não está tocando (o Spotify tem prioridade)."""

import asyncio
import io
import logging
import os
import time
import wave
from collections import deque

import numpy as np
from ai_edge_litert.interpreter import Interpreter
from shazamio import Shazam

from . import config, db, poller, settings

RATE = 16000
CHUNK_BYTES = RATE * 2          # 1 s de áudio mono int16
BUFFER_SECONDS = 15
WINDOW_SECONDS = 2              # áudio analisado pelo YAMNet a cada segundo
RETRY_SECONDS = 10              # sem faixa identificada, intervalo entre tentativas
LISTEN_GRACE_SECONDS = 3        # tolerância de silêncio antes de esconder "Ouvindo música..."
MUSIC_CLASS = 132               # índice da classe "Music" (models/yamnet_class_map.csv)
MODEL_FILE = config.ROOT / "backend" / "models" / "yamnet.tflite"
DEVICE = os.environ.get("HUB_MIC_DEVICE", "plughw:1,0")
log = logging.getLogger("hub.listener")

status = {
    "state": "desligado",
    "music_score": None,
    "last_result": None,
    "last_identify_at": None,
    "error": None,
}


class _Detector:
    def __init__(self) -> None:
        self._it = Interpreter(str(MODEL_FILE), num_threads=2)
        self._in = self._it.get_input_details()[0]["index"]
        self._out = self._it.get_output_details()[0]["index"]
        self._it.resize_tensor_input(self._in, [RATE * WINDOW_SECONDS], strict=True)
        self._it.allocate_tensors()

    def music_scores(self, audio: np.ndarray) -> tuple[float, float]:
        """(média da janela, último ~1 s). O último quadro reage rápido a uma pausa entre faixas."""
        self._it.set_tensor(self._in, audio)
        self._it.invoke()
        frames = self._it.get_tensor(self._out)[:, MUSIC_CLASS]
        return float(frames.mean()), float(frames[-1])


def _wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return out.getvalue()


def _parse(t: dict) -> dict:
    meta = {m.get("title"): m.get("text")
            for s in t.get("sections", []) if s.get("type") == "SONG"
            for m in s.get("metadata", [])}
    images = t.get("images") or {}
    return {
        "authorized": True,
        "playing": True,
        "source": "mic",
        "id": f"shazam:{t.get('key')}",
        "title": t.get("title", "?"),
        "artists": [t.get("subtitle", "?")],
        "album": meta.get("Album"),
        "release_date": meta.get("Released"),
        "cover": images.get("coverarthq") or images.get("coverart"),
        "duration_ms": None,
        "progress_ms": None,
    }


# Muda sempre que a faixa sai da tela: identificações que já estavam em andamento são descartadas
_epoch = 0
# Última faixa do microfone gravada no histórico (evita gravar de novo a mesma faixa reidentificada)
_last_logged: str | None = None


def _drop_track() -> None:
    """Tira a faixa do microfone da tela (a música parou ou pode ter mudado)."""
    global _epoch
    _epoch += 1
    if poller.state["mic"] is not None:
        poller.state["mic"] = None
        poller.publish_now()


def _clear() -> None:
    poller.set_listening(False)
    _drop_track()


async def _identify(shazam: Shazam, pcm: bytes, epoch: int) -> None:
    global _last_logged
    try:
        out = await shazam.recognize(_wav(pcm))
    except Exception as exc:
        log.warning("Shazam falhou: %s", exc)
        status["error"] = f"Shazam: {exc}"
        return
    status["last_identify_at"] = time.time()
    if epoch != _epoch or poller.state["now"].get("playing"):
        return  # a faixa mudou ou o Spotify começou a tocar enquanto o Shazam respondia
    t = out.get("track")
    if not t:
        status["last_result"] = "não reconhecida"
        return
    track = _parse(t)
    status.update(last_result=f"{track['title']} — {track['artists'][0]}", error=None)
    if (poller.state["mic"] or {}).get("id") != track["id"]:
        poller.state["mic"] = track
        poller.set_listening(False)  # identificada: a tela passa a mostrar a faixa
        poller.publish_now()
        if track["id"] != _last_logged:
            _last_logged = track["id"]
            await asyncio.to_thread(db.add_play, track)
            poller.publish("history", {})


async def _listen(detector: _Detector, shazam: Shazam) -> None:
    proc = await asyncio.create_subprocess_exec(
        "arecord", "-q", "-D", DEVICE, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    buf: deque[bytes] = deque(maxlen=BUFFER_SECONDS)
    streak, gap, last_music, last_try = 0, 0, 0.0, float("-inf")
    identifying: asyncio.Task | None = None
    try:
        while settings.get().mic_enabled:
            try:
                buf.append(await proc.stdout.readexactly(CHUNK_BYTES))
            except asyncio.IncompleteReadError:
                err = (await proc.stderr.read()).decode().strip()
                raise RuntimeError(f"arecord parou: {err or 'sem mensagem'}")
            s, now = settings.get(), time.time()

            if poller.state["now"].get("playing"):
                streak = gap = 0
                status.update(state="spotify ativo", music_score=None)
                _clear()
                continue
            if len(buf) < WINDOW_SECONDS:
                continue

            window = b"".join(list(buf)[-WINDOW_SECONDS:])
            audio = np.frombuffer(window, np.int16).astype(np.float32) / 32768
            score, latest = await asyncio.to_thread(detector.music_scores, audio)
            status["music_score"] = round(score, 3)

            # Pausa e volta da música com uma faixa na tela: provavelmente começou outra.
            # Tira a faixa (aparece "Ouvindo música...") e identifica de novo sem esperar o recheck.
            if latest < s.music_threshold:
                gap += 1
            else:
                if gap >= s.gap_seconds and poller.state["mic"] is not None:
                    log.info("pausa de %s s entre músicas: identificando de novo", gap)
                    _drop_track()
                    streak, last_try = 0, float("-inf")
                gap = 0

            if score >= s.music_threshold:
                streak += 1
                last_music = now
            else:
                streak = 0
            if now - last_music > s.silence_seconds:
                _clear()
            # "Ouvindo música...": a partir de 2 s de música seguidos, sem faixa na tela; some após 3 s sem música
            if poller.state["mic"] is None:
                if streak >= 2:
                    poller.set_listening(True)
                elif now - last_music > LISTEN_GRACE_SECONDS:
                    poller.set_listening(False)

            busy = identifying is not None and not identifying.done()
            interval = s.recheck_seconds if poller.state["mic"] else RETRY_SECONDS
            if streak >= s.music_seconds and not busy and now - last_try >= interval:
                last_try = now
                # Só o áudio desde que a música (re)começou, para não mandar o fim da faixa anterior
                clip = b"".join(list(buf)[-min(s.clip_seconds, streak + 1):])
                identifying = asyncio.create_task(_identify(shazam, clip, _epoch))
                busy = True
            status["state"] = "identificando" if busy else "música detectada" if streak else "aguardando música"
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


async def run() -> None:
    detector = await asyncio.to_thread(_Detector)
    shazam = Shazam()
    while True:
        if not settings.get().mic_enabled:
            status.update(state="desligado", music_score=None)
            _clear()
            await asyncio.sleep(2)
            continue
        try:
            status["error"] = None
            await _listen(detector, shazam)
        except Exception as exc:  # o laço não pode morrer
            log.exception("falha no microfone")
            status.update(state="erro", error=str(exc))
            await asyncio.sleep(5)
