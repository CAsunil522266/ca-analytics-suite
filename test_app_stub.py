"""Smoke test for app.py WITHOUT Streamlit installed: a stub 'streamlit' module drives each page end to end.

Run:  python test_app_stub.py
(With Streamlit installed you can also use streamlit.testing.v1.AppTest.)
"""
import importlib
import sys
import types


class Ctx:
    """Stub for any Streamlit container: attribute calls return sensible values; usable as a context manager."""

    def __init__(self, state, radio_choice=None):
        self._s, self._radio = state, radio_choice
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        def fn(*a, **k):
            self.calls.append(name)
            if name == "columns":
                n = a[0] if isinstance(a[0], int) else len(a[0])
                return [Ctx(self._s) for _ in range(n)]
            if name == "tabs":
                return [Ctx(self._s) for _ in a[0]]
            if name in ("button", "toggle", "checkbox"):
                return k.get("value", True) if name == "toggle" else True
            if name == "download_button":
                data = a[1] if len(a) > 1 else k.get("data")
                self._s.setdefault("downloads", []).append((a[0], len(data)))
                return False
            if name == "radio":
                return self._radio if (self._radio and self._radio in a[1]) else a[1][0]
            if name in ("selectbox",):
                return a[1][k.get("index", 0)]
            if name == "select_slider":
                return k["value"]
            if name in ("slider",):
                return a[3]
            if name == "number_input":
                return a[3] if len(a) > 3 else k.get("value", 0.0)
            if name == "text_input":
                return a[1] if len(a) > 1 else k.get("value", "")
            if name in ("file_uploader",):
                return None
            if name == "pyplot":
                self._s["figs"] = self._s.get("figs", 0) + 1
            return Ctx(self._s)
        return fn


def run(page):
    state = {}
    st = Ctx(state, page)
    st.session_state = {}
    st.set_page_config = lambda **k: None
    st.sidebar = Ctx(state, page)
    mod = types.ModuleType("streamlit")
    mod.__dict__.update({n: getattr(st, n) for n in ("session_state", "sidebar", "set_page_config")})
    mod.__getattr__ = lambda name: getattr(st, name)
    sys.modules["streamlit"] = mod
    sys.modules.pop("app", None)
    importlib.import_module("app")
    return state


if __name__ == "__main__":
    for page in ["Bank reconciliation", "GSTR-2B vs purchase register", "MIS dashboard"]:
        s = run(page)
        print(f"{page:32s} OK  figures={s.get('figs', 0)}  downloads={s.get('downloads', [])}")
