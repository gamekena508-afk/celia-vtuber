"""FAISS-backed embedding index for semantic memory book search."""

import json
import os
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_DIM = 384


class EmbeddingIndex:
    """Persisted FAISS index for semantic similarity search over memory book items."""

    def __init__(
        self,
        base_dir: str,
        model_name: str = DEFAULT_MODEL,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self._model_name = model_name
        self._index_path = os.path.join(base_dir, "embeddings.faiss")
        self._map_path = os.path.join(base_dir, "embeddings_map.json")
        self._lock = threading.RLock()
        self._model = None
        self._index = None
        self._id_to_fid: Dict[str, int] = {}
        self._fid_to_id: Dict[int, str] = {}
        self._fid_counter: int = 0

        if enabled:
            self._init_index()

    def _init_index(self) -> None:
        try:
            import faiss  # noqa: F401
        except ImportError:
            logger.warning("faiss-cpu not installed — semantic search disabled. Run: pip install faiss-cpu")
            self.enabled = False
            return

        try:
            if os.path.exists(self._map_path):
                with open(self._map_path, "r", encoding="utf-8") as f:
                    m = json.load(f)
                self._id_to_fid = {k: int(v) for k, v in m.get("id_to_fid", {}).items()}
                self._fid_to_id = {int(k): v for k, v in m.get("fid_to_id", {}).items()}
                self._fid_counter = int(m.get("fid_counter", 0))

            import faiss
            if os.path.exists(self._index_path) and self._id_to_fid:
                self._index = faiss.read_index(self._index_path)
            else:
                self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(_DIM))

            logger.debug(f"EmbeddingIndex ready: {len(self._id_to_fid)} items at {self._index_path}")
        except Exception as e:
            logger.warning(f"EmbeddingIndex init failed, running without embeddings: {e}")
            self.enabled = False

    def _ensure_model(self) -> None:
        if self._model is None:
            logger.info(f"Loading embedding model: {self._model_name} (first-time, may take a moment)")
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)

    def _embed(self, text: str):
        import numpy as np
        self._ensure_model()
        vec = self._model.encode([text[:512]], normalize_embeddings=True)[0]
        return vec.astype(np.float32)

    def _save_map(self) -> None:
        tmp = self._map_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id_to_fid": self._id_to_fid,
                    "fid_to_id": {str(k): v for k, v in self._fid_to_id.items()},
                    "fid_counter": self._fid_counter,
                },
                f,
            )
        os.replace(tmp, self._map_path)

    def _save_index(self) -> None:
        import faiss
        faiss.write_index(self._index, self._index_path)

    @property
    def is_empty(self) -> bool:
        return len(self._id_to_fid) == 0

    def add(self, item_id: str, text: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            if item_id in self._id_to_fid:
                old_fid = self._id_to_fid[item_id]
                try:
                    import numpy as np
                    self._index.remove_ids(np.array([old_fid], dtype=np.int64))
                except Exception:
                    pass
                self._fid_to_id.pop(old_fid, None)
            try:
                import numpy as np
                vec = self._embed(text)
                fid = self._fid_counter
                self._fid_counter += 1
                self._index.add_with_ids(vec.reshape(1, -1), np.array([fid], dtype=np.int64))
                self._id_to_fid[item_id] = fid
                self._fid_to_id[fid] = item_id
                self._save_map()
                self._save_index()
            except Exception as e:
                logger.warning(f"EmbeddingIndex.add failed for {item_id}: {e}")

    def remove(self, item_id: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            fid = self._id_to_fid.pop(item_id, None)
            if fid is None:
                return
            self._fid_to_id.pop(fid, None)
            try:
                import numpy as np
                self._index.remove_ids(np.array([fid], dtype=np.int64))
                self._save_map()
                self._save_index()
            except Exception as e:
                logger.warning(f"EmbeddingIndex.remove failed: {e}")

    def remove_many(self, item_ids: List[str]) -> None:
        if not self.enabled or not item_ids:
            return
        with self._lock:
            fids = []
            for item_id in item_ids:
                fid = self._id_to_fid.pop(item_id, None)
                if fid is not None:
                    self._fid_to_id.pop(fid, None)
                    fids.append(fid)
            if not fids:
                return
            try:
                import numpy as np
                self._index.remove_ids(np.array(fids, dtype=np.int64))
                self._save_map()
                self._save_index()
            except Exception as e:
                logger.warning(f"EmbeddingIndex.remove_many failed: {e}")

    def clear(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            try:
                import faiss
                self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(_DIM))
                self._id_to_fid = {}
                self._fid_to_id = {}
                self._fid_counter = 0
                self._save_map()
                self._save_index()
            except Exception as e:
                logger.warning(f"EmbeddingIndex.clear failed: {e}")

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        if not self.enabled:
            return []
        with self._lock:
            if not self._id_to_fid or self._index is None or self._index.ntotal == 0:
                return []
            try:
                import numpy as np
                vec = self._embed(query)
                k = min(top_k, self._index.ntotal)
                scores, fids = self._index.search(vec.reshape(1, -1), k)
                results: List[Tuple[str, float]] = []
                for score, fid in zip(scores[0], fids[0]):
                    if fid < 0:
                        continue
                    item_id = self._fid_to_id.get(int(fid))
                    if item_id and item_id in self._id_to_fid:
                        results.append((item_id, float(score)))
                return results
            except Exception as e:
                logger.warning(f"EmbeddingIndex.search failed: {e}")
                return []

    def backfill(self, items: List[Dict[str, Any]], summary_fn: Callable[[Dict[str, Any]], str]) -> int:
        """Embed all items not yet in the index. Returns count added."""
        if not self.enabled:
            return 0
        count = 0
        for item in items:
            item_id = item.get("id")
            if not item_id or item_id in self._id_to_fid:
                continue
            text = summary_fn(item)
            if text:
                self.add(item_id, text)
                count += 1
        if count:
            logger.info(f"EmbeddingIndex backfilled {count} items")
        return count
