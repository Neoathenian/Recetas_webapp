import pathlib

CSS_DIR = pathlib.Path(__file__).parent

def load_css(name: str) -> str:
    path = CSS_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"/* missing CSS file: {name} */"