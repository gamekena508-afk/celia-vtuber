"""Isolamento do estado global das fontes de visão entre testes.

`vision_sources` é um singleton de processo (tela/câmera, cada uma com o seu
liga/desliga). Sem isto, um teste que liga a tela vazaria para os seguintes.
"""
import pytest


@pytest.fixture(autouse=True)
def _vision_sources_desligadas():
    from celia.core.vision_sources import vision_sources

    vision_sources.reset()
    yield
    vision_sources.reset()
