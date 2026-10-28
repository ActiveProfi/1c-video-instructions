"""Числа и счета словами для озвучки. Текст на экране не меняется."""

ONES = {
    "m": ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"],
    "f": ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"],
}
TEENS = [
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
]
TENS = [
    "",
    "",
    "двадцать",
    "тридцать",
    "сорок",
    "пятьдесят",
    "шестьдесят",
    "семьдесят",
    "восемьдесят",
    "девяносто",
]
HUNDREDS = [
    "",
    "сто",
    "двести",
    "триста",
    "четыреста",
    "пятьсот",
    "шестьсот",
    "семьсот",
    "восемьсот",
    "девятьсот",
]


def plural_form(number, forms):
    """forms: (1, 2, 5) — рубль, рубля, рублей."""
    value = abs(int(number)) % 100
    if 11 <= value <= 14:
        return forms[2]
    last = value % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def _triad(number, gender):
    words = []
    hundreds, rest = divmod(number, 100)
    if hundreds:
        words.append(HUNDREDS[hundreds])
    if 10 <= rest <= 19:
        words.append(TEENS[rest - 10])
        return words
    tens, ones = divmod(rest, 10)
    if tens:
        words.append(TENS[tens])
    if ones:
        words.append(ONES[gender][ones])
    return words


def speak_int(number, gender="m"):
    number = int(number)
    if number < 0:
        raise ValueError("Ожидалось неотрицательное число")
    if number == 0:
        return "ноль"
    parts = []
    millions, rest = divmod(number, 1_000_000)
    thousands, rest = divmod(rest, 1000)
    if millions:
        parts.extend(_triad(millions, "m"))
        parts.append(plural_form(millions, ("миллион", "миллиона", "миллионов")))
    if thousands:
        parts.extend(_triad(thousands, "f"))
        parts.append(plural_form(thousands, ("тысяча", "тысячи", "тысяч")))
    if rest:
        parts.extend(_triad(rest, gender))
    return " ".join(parts)


def speak_rubles(amount):
    amount = int(amount)
    return f"{speak_int(amount)} {plural_form(amount, ('рубль', 'рубля', 'рублей'))}"


def speak_account(code):
    """10.01 → «десять ноль один», как в уже принятой озвучке проводок."""
    left, right = str(code).split(".")
    return f"{speak_int(int(left))} ноль {speak_int(int(right))}"


def speak_configuration(name):
    spoken = name.replace("1С:", "один эс:").replace("1С", "один эс")
    spoken = spoken.replace("3.0", "три ноль")
    return spoken
