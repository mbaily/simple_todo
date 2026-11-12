"""Request helpers shared by the daisyUI frontend's routes."""


def wants_fragment(request):
    """True when HTMX should receive only the workspace fragment.

    Boosted navigation and history restores replace the whole page, so they
    receive the full document (toolbar, title and workspace).
    """
    headers = request.headers
    return (headers.get("HX-Request") == "true"
            and headers.get("HX-History-Restore-Request") != "true"
            and headers.get("HX-Boosted") != "true")
