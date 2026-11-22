#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vision MCP Realtime - Módulo de captura e análise de tela
Compatível com Windows e Linux
"""

import platform
import hashlib
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis de ambiente do .env na raiz do projeto
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

IS_WINDOWS = platform.system() == 'Windows'

# Production vision is local-only. Credentials for unrelated providers are untouched.
from celia_paths import RAIZ
sys.path.insert(0, str(RAIZ / "src"))
from celia.core.vision_policy import vision_policy

_policy = vision_policy()
LOCAL_VISION_URL = _policy.url
LOCAL_VISION_MODEL = _policy.model
LOCAL_VISION_ENABLED = _policy.enabled
LOCAL_VISION_TIMEOUT_SEC = _policy.timeout

# Motivo da última falha de leitura, para mensagem HONESTA nas ferramentas:
#   False -> falha real; True -> o navegador ainda não enviou nenhum frame
#   (decisão 1a do dono: "ela só enxerga com o Screen ligado").
ultimo_get_sem_frame = False

# Idade maxima aceita para um frame, em milissegundos.
#
# MEDIDO em 2026-08-27 com a pagina prova-visao-frame.html: quando o Screen e
# desligado, o slot do servidor GUARDA o ultimo frame para sempre e o age_ms so
# cresce. Sem este teto, as ferramentas descreveriam com seguranca uma tela que
# nao existe mais — a Celia juraria estar vendo algo de uma hora atras. Frame
# velho nao e visao, e memoria se passando por visao.
#
# O gancho do navegador manda um frame a cada 2 s, entao 15 s cobre engasgo de
# rede e de aba em segundo plano com folga. Ajustavel por CELIA_VISION_FRAME_MAX_AGE_MS.
IDADE_MAXIMA_MS = int(os.getenv("CELIA_VISION_FRAME_MAX_AGE_MS", "15000"))

# ============================================================================
# CAPTURA DE TELA MULTIPLATAFORMA
# ============================================================================

def capture_screen():
    """Obtém a imagem atual para as ferramentas de visão.

    Windows: captura com PIL ImageGrab (ramo legado inalterado).
    Linux/Wayland: ZERO captura no servidor — lê o último frame que o
    navegador enviou via POST {CELIA_BRIDGE_URL}/bridge/vision/frame.
    Sem frame -> devolve None e marca ultimo_get_sem_frame = True.
    """
    global ultimo_get_sem_frame
    ultimo_get_sem_frame = False
    try:
        if IS_WINDOWS:
            # Windows: usar PIL com ImageGrab
            try:
                from PIL import ImageGrab
                screenshot = ImageGrab.grab()
                return screenshot
            except ImportError:
                print("❌ PIL não instalado. Execute: pip install pillow", file=sys.stderr)
                return None
            except Exception as e:
                print(f"❌ Erro ao capturar tela (PIL): {e}", file=sys.stderr)
                return None
        else:
            # Linux: o navegador é a única fonte (decisão 1a do dono).
            # Nada de mss/spectacle/portal/ImageGrab aqui — no Wayland isso
            # devolveu preto medido, e a decisão do dono vence qualquer utilidade.
            try:
                import PIL.Image

                base_url = (
                    os.getenv("CELIA_BRIDGE_URL", "http://127.0.0.1:12393")
                    .rstrip("/")
                )
                with urllib.request.urlopen(
                    f"{base_url}/bridge/vision/frame", timeout=5
                ) as resposta:
                    if resposta.status != 200:
                        print(
                            f"❌ Leitura do frame devolveu HTTP {resposta.status}",
                            file=sys.stderr,
                        )
                        return None
                    payload = json.loads(resposta.read().decode("utf-8"))
                idade_ms = payload.get("age_ms")
                if isinstance(idade_ms, int) and idade_ms > IDADE_MAXIMA_MS:
                    # Frame parado: o Screen foi desligado ou o navegador travou.
                    # Tratar como AUSENCIA e nao como visao — a mensagem honesta
                    # vale mais que uma descricao confiante de tela fantasma.
                    ultimo_get_sem_frame = True
                    print(
                        f"Frame do navegador parado ha {idade_ms} ms "
                        f"(teto {IDADE_MAXIMA_MS} ms) — tratando como sem frame.",
                        file=sys.stderr,
                    )
                    return None
                data_url = payload.get("image") or ""
                prefixo = "data:image/jpeg;base64,"
                if not data_url.startswith(prefixo):
                    print(
                        "❌ Frame recebido não é um data URL JPEG válido.",
                        file=sys.stderr,
                    )
                    return None
                bruto = base64.b64decode(data_url[len(prefixo):], validate=True)
                img = PIL.Image.open(BytesIO(bruto))
                img.load()  # força decodificação AGORA: erro aqui, não lá embaixo
                return img
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    ultimo_get_sem_frame = True
                    print(
                        "Sem frame do navegador — liga a tela ou a câmera na interface "
                        "(decisão do dono: ela só enxerga com a fonte ligada).",
                        file=sys.stderr,
                    )
                    return None
                print(
                    f"❌ Erro ao ler frame do navegador (HTTP {exc.code}): {exc}",
                    file=sys.stderr,
                )
                return None
            except Exception as e:
                print(f"❌ Erro ao ler frame do navegador: {e}", file=sys.stderr)
                return None

    except Exception as e:
        print(f"❌ Erro geral ao capturar tela: {e}", file=sys.stderr)
        return None


def image_hash(img):
    """Calcula hash da imagem para detectar mudanças"""
    try:
        # Redimensiona para 8x8 para hash rápido
        img_small = img.resize((8, 8))
        # Converte para escala de cinza
        img_gray = img_small.convert('L')
        # Calcula hash
        pixels = list(img_gray.getdata())
        avg = sum(pixels) / len(pixels)
        bits = ''.join('1' if p > avg else '0' for p in pixels)
        return hashlib.md5(bits.encode()).hexdigest()
    except Exception as e:
        print(f"❌ Erro ao calcular hash: {e}")
        return None


def encode_image(img, max_size=400):
    """Codifica imagem para base64 em JPEG (menor e mais rápido que PNG)"""
    try:
        # Redimensiona se for muito grande (mantém aspect ratio)
        if max(img.size) > max_size:
            ratio = max_size / max(img.size)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, resample=1)  # LANCZOS

        # Converte para RGB se necessário (JPEG não suporta transparência)
        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Salva como JPEG (muito menor que PNG, aceito pelo llama-server)
        buffered = BytesIO()
        img.save(buffered, format="JPEG", quality=75)
        img_bytes = buffered.getvalue()
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')

        return img_base64
    except Exception as e:
        print(f"❌ Erro ao codificar imagem: {e}")
        return None


# ============================================================================
# TESTE RÁPIDO
# ============================================================================

if __name__ == "__main__":
    print("🧪 Testando captura de tela...")
    print(f"Sistema operacional: {platform.system()}")

    img = capture_screen()
    if img:
        print(f"✅ Captura OK - Tamanho: {img.size}")

        # Testa hash
        hash_val = image_hash(img)
        if hash_val:
            print(f"✅ Hash: {hash_val}")

        # Testa encoding
        encoded = encode_image(img, max_size=512)
        if encoded:
            print(f"✅ Encoding OK - {len(encoded)} bytes em base64")
    else:
        print("❌ Falha na captura")

    print("\n📦 Dependências necessárias:")
    if IS_WINDOWS:
        print("   pip install pillow")
    else:
        print("   pip install pillow  (fonte da imagem: navegador via /bridge/vision/frame)")
