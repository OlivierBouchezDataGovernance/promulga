"""Translation hooks (gettext). The interface is in English today; texts are
marked so that French, Dutch... can be added later without touching the code.

- In templates: ``{{ _("Submit for review") }}`` or ``{% trans %}...{% endtrans %}``.
- In Python: ``from promulga.i18n import _``.
- Extraction (later): ``pybabel extract -F babel.cfg -o promulga/locales/messages.pot .``,
  then ``pybabel init -d promulga/locales -l fr`` and ``pybabel compile -d promulga/locales``.

Until a catalogue exists, ``_`` returns the English text unchanged.
"""

from __future__ import annotations

import gettext
from pathlib import Path

LOCALES = Path(__file__).resolve().parent / "locales"
_translations = gettext.translation("messages", localedir=LOCALES, languages=["en"], fallback=True)


def _(message: str, **kwargs) -> str:
    text = _translations.gettext(message)
    return text % kwargs if kwargs else text


def ngettext(singular: str, plural: str, n: int, **kwargs) -> str:
    text = _translations.ngettext(singular, plural, n)
    return text % {"num": n, **kwargs}


def install(env) -> None:
    """Enables {{ _() }} and {% trans %} in a Jinja environment."""
    env.add_extension("jinja2.ext.i18n")
    env.install_gettext_callables(gettext=_translations.gettext, ngettext=_translations.ngettext, newstyle=True)
