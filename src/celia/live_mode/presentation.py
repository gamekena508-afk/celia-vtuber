"""Entradas curtas dos companheiros, sem uma chamada extra ao LLM."""


def present_comment(author: str, text: str, turn: int) -> str:
    line = text if text.endswith(('.', '!', '?')) else text + '.'
    return (f'{author}: {line}',f'{author} disse: {line}')[turn % 2]



def gift_thanks(author: str, gift: str, turn: int, character: str, *, grouped=False) -> str:
    thanks = 'obrigada' if character == 'ursinho' else 'obrigado'
    if grouped:
        return f'{author}, {thanks} pelos presentes! Eu vi o {gift} também.'
    if 'rosa' in gift.casefold() or 'rose' in gift.casefold():
        choices = (
            f'{author}, valeu pela rosa! Já tenho um jardim aqui em cima.',
            f'Olha a rosa do {author}, Célia. Combinou comigo, vai.',
            f'{thanks.capitalize()} pela rosa, {author}. Essa eu guardo.',
        )
    else:
        choices = (
            f'{author}, valeu pelo {gift}! Esse chegou no capricho.',
            f'Célia, olha o {gift} que o {author} mandou! Gostei desse.',
            f'{thanks.capitalize()} pelo {gift}, {author}. Até acordei aqui em cima.',
            f'Eu vi esse {gift}, {author}! {thanks.capitalize()}, de verdade.',
        )
    return choices[turn % len(choices)]
