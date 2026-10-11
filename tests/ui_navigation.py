"""Explicit entry into the new analysis workspace for existing panel tests."""
from streamlit.testing.v1 import AppTest


def analysis_app(path, *, section='Overview'):
    app = AppTest.from_file(path).run(timeout=20)
    app.switch_page('app_pages/analyze.py')
    app.session_state.website_page = 'Analyze'
    app.session_state.analysis_section = section
    return app


def navigate(app, section):
    control(app, 'analysis_section').set_value(section).run(timeout=20)
    assert not app.exception
    return app


def control(app, key):
    return next(widget for widget in app.get('button_group') if widget.key == key)


def go_page(app, page):
    app.switch_page('app_pages/' + page.lower() + '.py').run(timeout=20)
    assert not app.exception
    return app
