import gradio as gr
from src.pages.header import render_header, with_light_mode_head
from src.page_timing import timed_page_load

def _header_root(request: gr.Request):
    # Use keyword args so order can't be swapped by Gradio
    return render_header(path="/", request=request)

def make_login_page() -> gr.Blocks:
    with gr.Blocks(
        title="The List Control Center",
        head=with_light_mode_head(None),
    ) as login_page:
        hdr = gr.HTML()
        gr.Markdown("## Welcome\nYou can browse public pages as a guest. Sign in for contribution privileges.")
        gr.Markdown("- Public information\n- Marketing copy\n- Anything you want here")

        login_page.load(timed_page_load("/", _header_root), outputs=[hdr])

    return login_page
