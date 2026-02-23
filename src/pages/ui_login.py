import gradio as gr
from src.pages.header import render_header, with_light_mode_head
from src.page_timing import timed_page_load

def _header_root(request: gr.Request):
    # Use keyword args so order can't be swapped by Gradio
    return render_header(path="/", request=request)

def make_login_page() -> gr.Blocks:
    with gr.Blocks(
        title="Centro de control de recetas",
        head=with_light_mode_head(None),
    ) as login_page:
        hdr = gr.HTML()
        gr.Markdown(
            "## Bienvenido\nSi no has iniciado sesión, esta es la única página disponible. "
            "Si ya iniciaste sesión pero no ves menús, tu cuenta aún no tiene privilegios."
        )
        gr.Markdown(
            "Si inicias sesión por primera vez, tu cuenta comienza sin privilegios. "
            "Un usuario con permisos de gestión debe habilitar tu acceso."
        )

        login_page.load(timed_page_load("/", _header_root), outputs=[hdr])

    return login_page
