"""Shared TikTok input/output normalization. Read DECISOES.md before changes."""
import re
import unicodedata

def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c)).lower()

def _normalizar(texto: str, extenso: dict) -> tuple[str, str]:
    """(texto dobrado, texto com digitos colados). Minusculas, sem acento, emoji de numero vira numero,
    numero por extenso vira digito, l/i/| colado em digito vira 1."""
    t = unicodedata.normalize("NFKC", texto).replace("\ufe0f", "").replace("\u20e3", "")
    t = _sem_acento(t)
    for palavra, num in sorted(extenso.items(), key=lambda kv: -len(kv[0])):
        t = re.sub(r"(?<![a-z])" + r"\s*".join(re.escape(x) for x in _sem_acento(palavra).split()) + r"(?![a-z])", f" {num} ", t)
    t = re.sub(r"(?<=\d)[li|]|[li|](?=\d)", "1", t)
    colado = re.sub(r"(?<=\d)[\s.\-_,/*+]+(?=\d)", "", t)
    return t, colado

def match_rules(texto: str, b: dict) -> str | None:
    """Match the reader rules without network or runtime side effects."""
    if not isinstance(b, dict) or not any(k in b for k in ('palavras', 'numeros', 'siglas')):
        raise ValueError('invalid filter schema')
    for key in ('palavras', 'numeros', 'siglas', 'siglas_ambiguas'):
        if not isinstance(b.get(key, []), list) or not all(isinstance(v, (str, int)) for v in b.get(key, [])):
            raise ValueError('invalid filter list: ' + key)
    extenso = b.get('numeros_por_extenso', {})
    if not isinstance(extenso, dict):
        raise ValueError('invalid number normalization')
    t, colado = _normalizar(texto, extenso)
    for n in b.get('numeros', []):
        pattern = r'(?<!\d)' + re.escape(str(n)) + r'(?!\d)'
        if re.search(pattern, t) or re.search(pattern, colado):
            return f'numero {n}'
    for sg in b.get('siglas', []) + b.get('siglas_ambiguas', []):
        pattern = r'(?<![a-z])' + r'[\s.\-_*]*'.join(re.escape(c) for c in _sem_acento(str(sg))) + r'(?![a-z])'
        if re.search(pattern, t):
            return f'sigla {sg}'
    for w in b.get('palavras', []):
        if re.search(r'(?<!\w)' + re.escape(_sem_acento(str(w))) + r'(?!\w)', t):
            return f'palavra {w}'
    return None
