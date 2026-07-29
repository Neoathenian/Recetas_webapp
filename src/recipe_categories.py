from __future__ import annotations

import re
import unicodedata
from typing import List


ALLOWED_RECIPE_CATEGORIES = (
    "Thermomix",
    "Mamá",
    "Primer plato",
    "Carne",
    "Pescado",
    "Pasta",
    "Abolla",
    "Abuela",
    "Aperitivo",
    "Bebida",
    "Salsas",
    "Postre",
)


def normalize_recipe_category_key(value: object) -> str:
    raw_text = str(value or "").strip().lower()
    if not raw_text:
        return ""
    normalized = unicodedata.normalize("NFKD", raw_text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


ALLOWED_RECIPE_CATEGORY_BY_KEY = {
    normalize_recipe_category_key(label): label.lower()
    for label in ALLOWED_RECIPE_CATEGORIES
}
ALLOWED_RECIPE_CATEGORY_KEYS = frozenset(ALLOWED_RECIPE_CATEGORY_BY_KEY)
ALLOWED_RECIPE_CATEGORIES_FOR_STORAGE = tuple(
    ALLOWED_RECIPE_CATEGORY_BY_KEY[normalize_recipe_category_key(label)]
    for label in ALLOWED_RECIPE_CATEGORIES
)


def filter_allowed_recipe_categories(values: object) -> List[str]:
    if isinstance(values, str):
        raw_values = [chunk.strip() for chunk in re.split(r"[\n,]+", values)]
    elif isinstance(values, (list, tuple, set)):
        raw_values = [str(item or "").strip() for item in values]
    else:
        raw_values = []

    categories: List[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        key = normalize_recipe_category_key(raw_value)
        if not key or key not in ALLOWED_RECIPE_CATEGORY_KEYS or key in seen:
            continue
        seen.add(key)
        categories.append(ALLOWED_RECIPE_CATEGORY_BY_KEY[key])
    return categories
