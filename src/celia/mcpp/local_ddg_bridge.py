"""
Local DDG Bridge - Servidor Bridge simples embutido que usa DuckDuckGo via Python
===================================================================================
Substitui a necessidade de servidor bridge externo (porta 12394).
Implementa pesquisa DuckDuckGo local usando a biblioteca duckduckgo-search.
"""

import asyncio
import json
import time
from typing import Dict, Any, Optional
from loguru import logger

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False
    logger.warning("duckduckgo-search not installed. DDG tools will be unavailable.")


class LocalDDgBridge:
    """Bridge local para pesquisa DuckDuckGo."""
    
    def __init__(self):
        self._initialized = False
    
    def initialize(self):
        """Inicializa o bridge."""
        if not DDG_AVAILABLE:
            logger.error("Cannot initialize DDG bridge: duckduckgo-search not installed")
            return False
        
        self._initialized = True
        logger.info("🔍 Local DDG Bridge initialized successfully")
        return True
    
    async def process_request(self, text: str) -> Dict[str, Any]:
        """
        Processa pedido de pesquisa e retorna resultado.
        
        Args:
            text: Texto do usuário
            
        Returns:
            Dict com resposta, tools usadas, etc
        """
        start_time = time.time()
        
        if not self._initialized:
            return {
                "response": "",
                "tool_used": False,
                "tools": [],
                "error": "DDG Bridge not initialized"
            }
        
        # Extrair query de pesquisa do texto
        query = self._extract_search_query(text)
        if not query:
            return {
                "response": "",
                "tool_used": False,
                "tools": [],
                "error": "No search query detected"
            }
        
        logger.info(f"🔍 DDG Search query: '{query}'")
        
        try:
            # Executar pesquisa DuckDuckGo
            results = await asyncio.to_thread(self._search_ddg, query, max_results=5)
            
            if not results:
                return {
                    "response": "Nenhum resultado encontrado para essa pesquisa.",
                    "tool_used": True,
                    "tools": [{"name": "ddg-search", "result": "No results"}],
                    "error": None,
                    "elapsed_ms": int((time.time() - start_time) * 1000)
                }
            
            # Formatar resultados
            formatted_results = []
            for i, result in enumerate(results, 1):
                formatted_results.append(
                    f"{i}. {result.get('title', 'Sem título')}\n"
                    f"   {result.get('body', 'Sem descrição')}\n"
                    f"   Fonte: {result.get('href', 'URL não disponível')}"
                )
            
            result_text = "\n\n".join(formatted_results)
            
            return {
                "response": result_text,
                "tool_used": True,
                "tools": [{"name": "ddg-search", "result": result_text}],
                "error": None,
                "elapsed_ms": int((time.time() - start_time) * 1000)
            }
            
        except Exception as e:
            logger.error(f"DDG search error: {type(e).__name__}: {e}")
            return {
                "response": "",
                "tool_used": False,
                "tools": [],
                "error": f"Search failed: {str(e)}",
                "elapsed_ms": int((time.time() - start_time) * 1000)
            }
    
    def _extract_search_query(self, text: str) -> Optional[str]:
        """Extrai query de pesquisa do texto do usuário."""
        text_lower = text.lower()
        
        # Padrões que indicam pesquisa
        search_patterns = [
            "pesquisa", "pesquise", "buscar", "busca", "procure", "procura",
            "quem é", "quem e ", "o que é", "o que e ", "qual é", "qual e ",
            "quantos", "quantas", "quando", "onde", "como", "por que",
            "explique", "explica", "descubra", "encontre", "verifique"
        ]
        
        for pattern in search_patterns:
            if pattern in text_lower:
                # Usar o texto original como query (LLM já instrui bem)
                return text
        
        return None
    
    def _search_ddg(self, query: str, max_results: int = 5) -> list:
        """Executa pesquisa DuckDuckGo."""
        if not DDG_AVAILABLE:
            return []
        
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
                return results
        except Exception as e:
            logger.error(f"DuckDuckGo search error: {e}")
            return []


# Singleton instance
local_ddg_bridge = LocalDDgBridge()
