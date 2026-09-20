"""400 的两条路：缺键的表单取值与一段坏 json，在 DEBUG 与 TRAP_BAD_REQUEST_ERRORS 四种组合下各给什么。
跑法：PYTHONPATH=src .venv/bin/python repro/trap400.py
"""
from flask import Flask, request

CASES = [
    ("debug=F,trap=unset", False, None),
    ("debug=T,trap=unset", True, None),
    ("debug=F,trap=True", False, True),
    ("debug=T,trap=True", True, True),
    ("debug=F,trap=False", False, False),
]


def make_app(debug, trap):
    app = Flask("probe_a")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "k"
    if debug:
        app.config["DEBUG"] = True
    else:
        app.config["DEBUG"] = False
    if trap is not None:
        app.config["TRAP_BAD_REQUEST_ERRORS"] = trap

    @app.route("/form")
    def form_view():
        return request.form["missing_key"]

    @app.route("/json", methods=["POST"])
    def json_view():
        return request.get_json()

    return app


def run_case(name, debug, trap):
    app = make_app(debug, trap)
    client = app.test_client()
    for ep, method, kw in (
        ("/form", "get", {}),
        ("/json", "post", {"data": "{bad json", "content_type": "application/json"}),
    ):
        label = f"{name} {ep}"
        try:
            rv = getattr(client, method)(ep, **kw)
            kind = f"RESPONSE {rv.status_code}"
            text = rv.get_data(as_text=True)
        except Exception as e:
            kind = f"RAISED {type(e).__name__}"
            try:
                text = e.get_description()
            except Exception:
                text = str(e)
        print(
            f"{label:34s} | {kind:18s} | has_key={'missing_key' in text} "
            f"has_decode={'Failed to decode' in text}"
        )


for name, d, t in CASES:
    run_case(name, d, t)
print("---- done ----")
