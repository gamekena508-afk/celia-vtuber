"""Fundacao persistente para logs, candidatos e ativos da Celia.

O log e append-only em JSONL. SQLite guarda uma copia indexada de cada
registro para que consultas nao dependam de varrer o historico ou abrir WAVs.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
import uuid
import os
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from string import Formatter
from typing import Any
from .state_store import file_lock

SCHEMA_LOG = "celia.interacao.log.v1"
SCHEMA_CANDIDATO = "celia.memoria.candidato.v1"
SCHEMA_ATIVO = "celia.banco.ativo.v1"
SCHEMA_ATIVO_V2 = "celia.banco.ativo.v2"
CAMADAS = frozenset({"L0", "L1", "L2", "L3"})
VARIAVEIS_TEMPLATE = frozenset({"nome", "jogo", "pontuacao", "acao"})

_LOG_FIELDS = frozenset({
    "turn_id", "timestamp", "personagem", "actor", "input_normalizado",
    "input", "resposta", "intencao", "emocao", "contexto", "entidades",
    "variaveis", "fonte", "modelo", "tts_engine", "latencias",
    "bank_hit", "bank_miss", "bank_rejected_low_confidence",
    "bank_rejected_context", "bank_resultado", "bank_ativo_id",
    "bank_candidatos", "bank_regra", "bank_confianca", "bank_restricoes",
    "bank_razao", "bank_duracao_ms",
    "fallback_generation", "template_id", "repeticoes", "candidato_a_reuso",
    "estado_consolidacao", "current_session_occurrences", "repetition_evidence",
})
_BANK_FIELDS = frozenset({
    "texto", "texto_canonico", "camada", "categoria", "personagem",
    "intencao", "emocao", "contexto", "entidades", "variaveis",
    "fonte", "modelo", "template_id", "turn_id", "variantes",
    "estado_consolidacao", "audio_path", "audio_sha256", "motor",
    "perfil_voz", "estado_validacao", "selecao",
})
_FORBIDDEN_KEYS = frozenset({
    "audio", "audio_bytes", "audio_payload", "raw_audio", "raw_audio_bytes",
    "wav", "waveform", "password", "passwd", "token", "access_token",
    "api_key", "apikey", "secret", "authorization", "cookie",
    "email", "cpf", "cnpj", "phone", "telefone", "address", "endereco",
    "ssn", "document", "documento", "credit_card", "cartao",
})
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalizar_texto(texto: str) -> str:
    """Normaliza texto para deduplicacao sem alterar o texto canonico salvo."""
    if not isinstance(texto, str):
        raise TypeError("texto precisa ser str")
    decomposed = unicodedata.normalize("NFKD", texto.casefold())
    sem_acento = "".join(c for c in decomposed if not unicodedata.combining(c))
    sem_pontuacao = re.sub(r"[^a-z0-9]+", " ", sem_acento)
    return re.sub(r"\s+", " ", sem_pontuacao).strip()


def hash_texto(texto: str) -> str:
    return hashlib.sha256(normalizar_texto(texto).encode("utf-8")).hexdigest()


def _agora_ou_existente(valor: Any) -> Any:
    return valor if valor is not None else agora_iso()


def _chave_proibida(chave: Any) -> bool:
    normalizada = str(chave).casefold().replace("-", "_")
    return normalizada in _FORBIDDEN_KEYS


def _json_seguro(valor: Any) -> Any:
    """Remove payloads brutos e segredos antes de qualquer persistencia."""
    if isinstance(valor, Mapping):
        return {
            str(chave): _json_seguro(item)
            for chave, item in valor.items()
            if not _chave_proibida(chave)
        }
    if isinstance(valor, (list, tuple)):
        return [_json_seguro(item) for item in valor]
    if isinstance(valor, Path):
        return str(valor)
    if valor is None or isinstance(valor, (str, int, float, bool)):
        return valor
    raise TypeError(f"valor nao serializavel: {type(valor).__name__}")


def _texto_e_variaveis(texto: str) -> set[str]:
    encontrados: set[str] = set()
    partes = Formatter().parse(texto)
    for _, campo, formato, conversao in partes:
        if campo is None:
            continue
        nome = campo.split(".", 1)[0].split("[", 1)[0]
        if not _IDENTIFIER.fullmatch(nome) or formato or conversao:
            raise ValueError("template possui variavel invalida")
        if nome not in VARIAVEIS_TEMPLATE:
            raise ValueError(f"variavel de template nao permitida: {nome}")
        encontrados.add(nome)
    return encontrados


def _validar_camada(
    texto: str,
    camada: Any,
    *,
    template_id: Any = None,
    variaveis: Any = None,
    contexto: Any = None,
) -> tuple[str, list[str], set[str]]:
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("texto obrigatorio")
    camada = str(camada or "").strip().upper()
    if camada not in CAMADAS:
        raise ValueError("camada precisa ser L0, L1, L2 ou L3")

    encontradas = _texto_e_variaveis(texto)
    declaradas = [] if variaveis is None else [str(item).strip() for item in variaveis]
    if any(item not in VARIAVEIS_TEMPLATE for item in declaradas):
        raise ValueError("variavel declarada nao permitida")
    if len(set(declaradas)) != len(declaradas):
        raise ValueError("variaveis declaradas duplicadas")
    if not encontradas.issubset(set(declaradas)):
        faltantes = sorted(encontradas.difference(declaradas))
        raise ValueError(f"variavel nao declarada: {faltantes[0]}")

    tem_contexto = contexto not in (None, "", {}, [], ())
    if camada == "L0":
        if encontradas or declaradas or template_id or tem_contexto:
            raise ValueError("L0 nao aceita variaveis, template ou contexto")
        if len(texto.strip()) > 80 or len(texto.split()) > 12:
            raise ValueError("L0 precisa ser texto curtissimo")
    elif camada == "L1":
        if encontradas or declaradas or template_id or tem_contexto:
            raise ValueError("L1 nao aceita variaveis, template ou contexto")
        if len(texto.strip()) > 240 or len(texto.split()) > 32:
            raise ValueError("L1 precisa ser texto curto")
    elif camada == "L2":
        if template_id is not None and not str(template_id).strip():
            raise ValueError("template_id nao pode ser vazio")
    elif encontradas or declaradas or template_id:
        raise ValueError("L3 nao aceita template ou variaveis")

    return camada, declaradas, encontradas


def _validar_selecao(selecao: Any) -> dict[str, Any] | None:
    """Validate the timing rule separately from the text of an active."""
    if selecao is None:
        return None
    if not isinstance(selecao, dict):
        raise ValueError("selecao precisa ser um dict")

    intencao = str(selecao.get("intencao") or "").strip()
    gatilhos = selecao.get("gatilhos")
    contextos = selecao.get("contextos_permitidos")
    if not intencao:
        raise ValueError("selecao.intencao obrigatoria")
    if not isinstance(gatilhos, list) or not gatilhos:
        raise ValueError("selecao.gatilhos precisa ser uma lista nao vazia")
    if not isinstance(contextos, list):
        raise ValueError("selecao.contextos_permitidos precisa ser uma lista")

    gatilhos_normalizados: list[str] = []
    for gatilho in gatilhos:
        if not isinstance(gatilho, str) or not normalizar_texto(gatilho):
            raise ValueError("selecao.gatilhos precisa conter textos validos")
        normalizado = normalizar_texto(gatilho)
        if normalizado not in gatilhos_normalizados:
            gatilhos_normalizados.append(normalizado)
    contextos_limpos: list[str] = []
    for contexto in contextos:
        if not isinstance(contexto, str) or not contexto.strip():
            raise ValueError("selecao.contextos_permitidos precisa conter textos validos")
        valor = contexto.strip()
        if valor not in contextos_limpos:
            contextos_limpos.append(valor)

    resultado: dict[str, Any] = {
        "intencao": intencao,
        "gatilhos": gatilhos_normalizados,
        "contextos_permitidos": contextos_limpos,
    }
    if "restricoes" in selecao:
        if not isinstance(selecao["restricoes"], dict):
            raise ValueError("selecao.restricoes precisa ser um dict")
        resultado["restricoes"] = _json_seguro(selecao["restricoes"])
    if "confianca_minima" in selecao:
        confianca = selecao["confianca_minima"]
        if isinstance(confianca, bool) or not isinstance(confianca, (int, float)):
            raise ValueError("selecao.confianca_minima precisa ser numero")
        if not 0 <= float(confianca) <= 1:
            raise ValueError("selecao.confianca_minima fora do intervalo")
        resultado["confianca_minima"] = float(confianca)
    return resultado


class Memoria:
    """Armazena os tres registros F2 sem promover dados implicitamente."""

    def __init__(self, raiz: Path):
        self.raiz = Path(raiz).expanduser().resolve()
        self.raiz.mkdir(parents=True, exist_ok=True)
        self.log_path = self.raiz / "interacoes.jsonl"
        self.db_path = self.raiz / "memoria.sqlite3"
        self._lock = threading.RLock()
        self._closed = False
        with self._conexao() as db:
            db.execute("PRAGMA journal_mode = WAL")
            self._criar_indice(db)

    @contextmanager
    def _conexao(self):
        if self._closed:
            raise RuntimeError("Memoria is closed")
        db = sqlite3.connect(str(self.db_path), timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 30000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _criar_indice(self, db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS registros (
                id TEXT PRIMARY KEY,
                tipo TEXT NOT NULL,
                schema TEXT NOT NULL,
                turn_id TEXT,
                categoria TEXT,
                intencao TEXT,
                template_id TEXT,
                texto_hash TEXT,
                estado TEXT,
                camada TEXT,
                personagem TEXT,
                criado_em TEXT,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_registros_id
                ON registros(id);
            CREATE INDEX IF NOT EXISTS idx_registros_categoria
                ON registros(categoria);
            CREATE INDEX IF NOT EXISTS idx_registros_intencao
                ON registros(intencao);
            CREATE INDEX IF NOT EXISTS idx_registros_template_id
                ON registros(template_id);
            CREATE INDEX IF NOT EXISTS idx_registros_texto_hash
                ON registros(texto_hash);
            CREATE INDEX IF NOT EXISTS idx_registros_estado
                ON registros(estado);
            CREATE INDEX IF NOT EXISTS idx_registros_camada
                ON registros(camada);
            CREATE INDEX IF NOT EXISTS idx_registros_personagem
                ON registros(personagem);
            CREATE INDEX IF NOT EXISTS idx_registros_turn_id
                ON registros(turn_id);
            CREATE TABLE IF NOT EXISTS ativo_gatilhos (
                ativo_id TEXT NOT NULL,
                gatilho TEXT NOT NULL,
                PRIMARY KEY (ativo_id, gatilho)
            );
            CREATE INDEX IF NOT EXISTS idx_ativo_gatilhos_gatilho
                ON ativo_gatilhos(gatilho);
            """
        )

    def _guardar_indice(self, db: sqlite3.Connection, registro: dict[str, Any], tipo: str) -> None:
        db.execute(
            """
            INSERT INTO registros (
                id, tipo, schema, turn_id, categoria, intencao, template_id,
                texto_hash, estado, camada, personagem, criado_em, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                registro["id"],
                tipo,
                registro["schema"],
                registro.get("turn_id"),
                registro.get("categoria"),
                registro.get("intencao"),
                registro.get("template_id"),
                registro.get("texto_hash"),
                registro.get("estado"),
                registro.get("camada"),
                registro.get("personagem"),
                registro.get("criado_em", registro.get("timestamp")),
                json.dumps(registro, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        selecao = registro.get("selecao")
        if tipo == "ativo" and isinstance(selecao, dict):
            for gatilho in selecao.get("gatilhos", []):
                db.execute(
                    "INSERT OR IGNORE INTO ativo_gatilhos (ativo_id, gatilho) VALUES (?, ?)",
                    (registro["id"], str(gatilho)),
                )

    def _append_log(self, registro: dict[str, Any]) -> None:
        with file_lock(self.log_path):
            with self.log_path.open("a", encoding="utf-8") as arquivo:
                arquivo.write(json.dumps(registro, ensure_ascii=False, separators=(",", ":")))
                arquivo.write("\n")
                arquivo.flush()
                os.fsync(arquivo.fileno())

    def registrar_turno(self, **campos: Any) -> str:
        if not str(campos.get("turn_id") or "").strip():
            raise ValueError("turn_id obrigatorio")
        registro_id = uuid.uuid4().hex
        registro: dict[str, Any] = {
            "schema": SCHEMA_LOG,
            "id": registro_id,
            "timestamp": _agora_ou_existente(campos.get("timestamp")),
        }
        registro.update({
            chave: _json_seguro(campos[chave])
            for chave in _LOG_FIELDS
            if chave in campos and not _chave_proibida(chave)
        })
        registro["turn_id"] = str(registro["turn_id"])
        with self._lock:
            self._append_log(registro)
            with self._conexao() as db:
                self._guardar_indice(db, registro, "log")
        return registro_id

    def logs_do_turno(self, turn_id: str) -> list[dict[str, Any]]:
        with self._lock:
            with self._conexao() as db:
                rows = db.execute(
                    "SELECT payload FROM registros WHERE tipo = 'log' AND turn_id = ? ORDER BY rowid",
                    (str(turn_id),),
                ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def propor_candidato(self, **campos: Any) -> str:
        camada, variaveis, _ = _validar_camada(
            campos.get("texto"), campos.get("camada"),
            template_id=campos.get("template_id"),
            variaveis=campos.get("variaveis"), contexto=campos.get("contexto"),
        )
        personagem = str(campos.get("personagem") or "").strip()
        if not personagem:
            raise ValueError("personagem obrigatorio para separar bancos")
        texto = campos["texto"]
        registro_id = uuid.uuid4().hex
        registro: dict[str, Any] = {
            "schema": SCHEMA_CANDIDATO,
            "id": registro_id,
            "estado": "pendente",
            "criado_em": agora_iso(),
            "texto": texto,
            "texto_hash": hash_texto(texto),
            "camada": camada,
            "personagem": personagem,
            "variaveis": variaveis,
        }
        for chave in _BANK_FIELDS:
            if chave in {"texto", "texto_canonico", "camada", "personagem", "variaveis"}:
                continue
            if chave in campos:
                registro[chave] = _json_seguro(campos[chave])
        if campos.get("template_id") is not None:
            registro["template_id"] = str(campos["template_id"])
        if "texto_canonico" in campos:
            registro["texto_canonico"] = str(campos["texto_canonico"])
        with self._lock:
            with self._conexao() as db:
                self._guardar_indice(db, registro, "candidato")
        return registro_id

    def candidato(self, id: str) -> dict[str, Any] | None:
        return self._por_id(id, "candidato")

    def publicar_ativo(self, **campos: Any) -> str:
        selecao = _validar_selecao(campos.get("selecao"))
        camada, variaveis, _ = _validar_camada(
            campos.get("texto"), campos.get("camada"),
            template_id=campos.get("template_id"),
            variaveis=campos.get("variaveis"), contexto=campos.get("contexto"),
        )
        personagem = str(campos.get("personagem") or "").strip()
        categoria = str(campos.get("categoria") or "").strip()
        motor = str(campos.get("motor") or "").strip().casefold()
        perfil_voz = str(campos.get("perfil_voz") or "").strip()
        estado_validacao = str(campos.get("estado_validacao") or "").strip().casefold()
        if not personagem:
            raise ValueError("personagem obrigatorio para separar bancos")
        if not categoria:
            raise ValueError("categoria obrigatoria para ativo")
        if motor == "x_tts":
            raise ValueError("x_tts nao pode ser publicado como ativo permanente")
        if motor != "omnivoice":
            raise ValueError("ativo permanente exige motor omnivoice")
        if not perfil_voz:
            raise ValueError("perfil_voz obrigatorio")
        if estado_validacao != "aprovado":
            raise ValueError("ativo exige estado_validacao aprovado")

        caminho = Path(campos.get("audio_path") or "").expanduser()
        if not caminho.is_absolute():
            caminho = Path.cwd() / caminho
        caminho = caminho.resolve()
        if not caminho.is_file():
            raise ValueError("audio_path ausente")
        if any(parte in {"_archive", "_quarantine"} for parte in caminho.parts):
            raise ValueError("arquivo em quarentena nao pode virar ativo")
        digest = self._sha256_arquivo(caminho)
        informado = campos.get("audio_sha256")
        if informado is not None:
            informado = str(informado).strip().casefold()
            if not re.fullmatch(r"[0-9a-f]{64}", informado):
                raise ValueError("audio_sha256 invalido")
            if informado != digest:
                raise ValueError("audio_sha256 nao corresponde ao arquivo")

        texto = campos["texto"]
        registro_id = uuid.uuid4().hex
        registro: dict[str, Any] = {
            "schema": SCHEMA_ATIVO_V2 if selecao is not None else SCHEMA_ATIVO,
            "id": registro_id,
            "estado": "publicado",
            "texto": texto,
            "texto_hash": hash_texto(texto),
            "camada": camada,
            "personagem": personagem,
            "categoria": categoria,
            "audio_path": str(caminho),
            "motor": motor,
            "perfil_voz": perfil_voz,
            "estado_validacao": estado_validacao,
            "audio_sha256": digest,
            "criado_em": agora_iso(),
            "variantes": _json_seguro(campos.get("variantes", [])),
            "variaveis": variaveis,
        }
        if selecao is not None:
            registro["selecao"] = selecao
            registro["intencao"] = str(campos.get("intencao") or selecao["intencao"])
        for chave in _BANK_FIELDS:
            if chave in registro or chave in {"texto", "texto_canonico", "camada", "personagem", "variaveis", "variantes"}:
                continue
            if chave in campos:
                registro[chave] = _json_seguro(campos[chave])
        if campos.get("template_id") is not None:
            registro["template_id"] = str(campos["template_id"])
        if "texto_canonico" in campos:
            registro["texto_canonico"] = str(campos["texto_canonico"])
        if "candidato_id" in campos:
            registro["candidato_id"] = str(campos["candidato_id"])
        with self._lock:
            with self._conexao() as db:
                self._guardar_indice(db, registro, "ativo")
        return registro_id

    def ativo(self, id: str) -> dict[str, Any] | None:
        return self._por_id(id, "ativo")

    def verificar_audio(self, ativo_id: str) -> bool:
        registro = self.ativo(ativo_id)
        if not registro:
            return False
        caminho = Path(registro.get("audio_path", ""))
        if not caminho.is_file():
            return False
        try:
            return self._sha256_arquivo(caminho) == registro.get("audio_sha256")
        except OSError:
            return False

    def buscar(
        self,
        *,
        categoria: str | None = None,
        intencao: str | None = None,
        gatilho: str | None = None,
        template_id: str | None = None,
        texto_hash: str | None = None,
        estado: str | None = None,
        camada: str | None = None,
        personagem: str | None = None,
    ) -> list[dict[str, Any]]:
        filtros = {
            "categoria": categoria,
            "intencao": intencao,
            "template_id": template_id,
            "texto_hash": texto_hash,
            "estado": estado,
            "camada": camada,
            "personagem": personagem,
        }
        where = []
        valores = []
        for coluna, valor in filtros.items():
            if valor is not None:
                where.append(f"{coluna} = ?")
                valores.append(valor)
        where.insert(0, "tipo IN ('candidato', 'ativo')")
        if gatilho is not None:
            where.append(
                "EXISTS (SELECT 1 FROM ativo_gatilhos ag "
                "WHERE ag.ativo_id = registros.id AND ag.gatilho = ?)"
            )
            valores.append(normalizar_texto(gatilho))
        clausula = f" WHERE {' AND '.join(where)}"
        with self._lock:
            with self._conexao() as db:
                rows = db.execute(
                    f"SELECT payload FROM registros{clausula} ORDER BY rowid", valores
                ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def _por_id(self, registro_id: str, tipo: str) -> dict[str, Any] | None:
        with self._lock:
            with self._conexao() as db:
                row = db.execute(
                    "SELECT payload FROM registros WHERE id = ? AND tipo = ?",
                    (str(registro_id), tipo),
                ).fetchone()
        return json.loads(row["payload"]) if row else None

    @staticmethod
    def _sha256_arquivo(caminho: Path) -> str:
        digest = hashlib.sha256()
        with caminho.open("rb") as arquivo:
            for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
                digest.update(bloco)
        return digest.hexdigest()

    def backup(self, destination: str | Path) -> Path:
        """Create a transactionally consistent SQLite snapshot via SQLite backup API."""
        target = Path(destination).expanduser().resolve()
        if target == self.db_path:
            raise ValueError("backup destination must differ from the live database")
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        os.close(fd)
        temp = Path(temp_name)
        try:
            with self._lock, self._conexao() as source:
                copy = sqlite3.connect(str(temp), timeout=30)
                try:
                    source.backup(copy)
                    check = copy.execute("PRAGMA integrity_check").fetchone()
                    if not check or check[0] != "ok":
                        raise sqlite3.DatabaseError("SQLite backup integrity check failed")
                    copy.commit()
                finally:
                    copy.close()
            with temp.open("rb+") as stream:
                os.fsync(stream.fileno())
            os.replace(temp, target)
            return target
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def close(self) -> None:
        with self._lock:
            self._closed = True

__all__ = [
    "SCHEMA_ATIVO",
    "SCHEMA_ATIVO_V2",
    "SCHEMA_CANDIDATO",
    "SCHEMA_LOG",
    "Memoria",
    "registrar_turno_seguro",
    "hash_texto",
    "normalizar_texto",
]


def registrar_turno_seguro(memoria: Memoria | None, **campos: Any) -> str | None:
    """Persist a turn without making memory a dependency of speech."""
    if memoria is None:
        return None
    try:
        return memoria.registrar_turno(**campos)
    except Exception as exc:  # noqa: BLE001 - observability must not break speech
        import logging

        logging.getLogger(__name__).error(
            "falha ao persistir memoria do turno %s: %s",
            str(campos.get("turn_id") or "desconhecido"),
            type(exc).__name__,
            exc_info=True,
        )
        return None
