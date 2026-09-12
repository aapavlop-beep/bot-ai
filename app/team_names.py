from __future__ import annotations

import re


# Единое отображаемое название КХЛ. Источники могут отдавать город,
# английское имя или название клуба — наружу всегда отдаём название клуба.
TEAM_NAMES: dict[str, str] = {
    # Барыс / Астана
    "barys astana": "Барыс",
    "astana": "Барыс",
    "barys": "Барыс",
    "барыс астана": "Барыс",
    "барыс": "Барыс",
    # Амур / Хабаровск
    "khabarovsk": "Амур",
    "amur khabarovsk": "Амур",
    "amur": "Амур",
    "хабаровск": "Амур",
    "амур": "Амур",
    # Динамо
    "dynamo moscow": "Динамо Москва",
    "dynamo moskva": "Динамо Москва",
    "динамо москва": "Динамо Москва",
    "динамо": "Динамо Москва",
    "dinamo minsk": "Динамо Минск",
    "dynamo minsk": "Динамо Минск",
    "динамо минск": "Динамо Минск",
    # Спартак / Череповец
    "spartak moscow": "Спартак Москва",
    "spartak": "Спартак Москва",
    "спартак москва": "Спартак Москва",
    "спартак": "Спартак Москва",
    "cherepovets": "Северсталь",
    "severstal cherepovets": "Северсталь",
    "severstal": "Северсталь",
    "череповец": "Северсталь",
    "северсталь": "Северсталь",
    # СКА / Сочи
    "ska st. petersburg": "СКА",
    "ska st petersburg": "СКА",
    "ska saint petersburg": "СКА",
    "ska": "СКА",
    "ска санкт петербург": "СКА",
    "ска": "СКА",
    "sochi": "Сочи",
    "сочи": "Сочи",
    # Восток/Центр
    "nizhny novgorod": "Торпедо",
    "torpedo nizhny novgorod": "Торпедо",
    "torpedo": "Торпедо",
    "нижний новгород": "Торпедо",
    "торпедо": "Торпедо",
    "niznekamsk": "Нефтехимик",
    "nizhnekamsk": "Нефтехимик",
    "neftekhimik": "Нефтехимик",
    "neftekhimik nizhnekamsk": "Нефтехимик",
    "нефтехимик": "Нефтехимик",
    "нижнекамск": "Нефтехимик",
    "yekaterinburg": "Автомобилист",
    "ekaterinburg": "Автомобилист",
    "avtomobilist": "Автомобилист",
    "автомобилист": "Автомобилист",
    "екатеринбург": "Автомобилист",
    "cska moscow": "ЦСКА",
    "cska": "ЦСКА",
    "цска москва": "ЦСКА",
    "цска": "ЦСКА",
    "vladivostok": "Адмирал",
    "admiral vladivostok": "Адмирал",
    "admiral": "Адмирал",
    "владивосток": "Адмирал",
    "адмирал": "Адмирал",
    "bars kazan": "Ак Барс",
    "ak bars kazan": "Ак Барс",
    "ak bars": "Ак Барс",
    "ак барс казань": "Ак Барс",
    "ак барс": "Ак Барс",
    "magnitogorsk": "Металлург",
    "metallurg magnitogorsk": "Металлург",
    "metallurg mg": "Металлург",
    "металлург магнитогорск": "Металлург",
    "металлург мг": "Металлург",
    "металлург": "Металлург",
    "lada tolyatti": "Лада",
    "lada": "Лада",
    "лада тольятти": "Лада",
    "лада": "Лада",
    "tractor chelyabinsk": "Трактор",
    "traktor chelyabinsk": "Трактор",
    "traktor": "Трактор",
    "трактор": "Трактор",
    "chelyabinsk": "Трактор",
    "salavat yulaev": "Салават Юлаев",
    "salavat": "Салават Юлаев",
    "салават юлаев": "Салават Юлаев",
    "sibir novosibirsk": "Сибирь",
    "sibir": "Сибирь",
    "новосибирск": "Сибирь",
    "сибирь": "Сибирь",
    "lokomotiv yaroslavl": "Локомотив",
    "lokomotiv": "Локомотив",
    "ярославль": "Локомотив",
    "локомотив": "Локомотив",
    "avangard omsk": "Авангард",
    "avangard": "Авангард",
    "омск": "Авангард",
    "авангард": "Авангард",
    "traktor": "Трактор",
    "kunlun red star": "Куньлунь Ред Стар",
    "kunlun": "Куньлунь Ред Стар",
    "кунлунь ред стар": "Куньлунь Ред Стар",
    "куньлунь ред стар": "Куньлунь Ред Стар",
    "vityaz": "Витязь",
    "витязь": "Витязь",
}


def _norm(value: str) -> str:
    text = value.lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9]+", " ", text)
    return " ".join(text.split())


def display_team_name(value: str) -> str:
    """Return a stable Russian club name for any known KHL alias."""
    raw = str(value or "").strip()
    if not raw:
        return raw
    return TEAM_NAMES.get(_norm(raw), raw)
