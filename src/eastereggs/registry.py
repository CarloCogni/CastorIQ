# eastereggs/registry.py
"""Registry of available easter-egg games.

To add a new game:
    1. Append a Game entry below.
    2. Add the matching URL pattern in ``urls.py``.
    3. Create a standalone template under ``templates/eastereggs/games/``.
    4. Drop static assets under ``static/eastereggs/<slug>/``.

The gallery view renders every entry as a card.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    """Metadata for a single easter-egg game.

    Attributes:
        slug: Short kebab-case identifier used in URLs and static paths.
        title: Display name on gallery cards and game chrome.
        subtitle: One-line description (<80 chars).
        accent_color: CSS color used for the gallery card accent.
        requires_project: If True, the game's URL takes a project UUID and
            integrates with the live scan (via ``window.postMessage``).
        url_name: The URL name under the ``eastereggs`` namespace.
    """

    slug: str
    title: str
    subtitle: str
    accent_color: str
    requires_project: bool
    url_name: str


GAMES: list[Game] = [
    Game(
        slug="castor-slug",
        title="Castor Slug",
        subtitle="Gun down IFC defects while the scan grinds.",
        accent_color="#3b82f6",
        requires_project=True,
        url_name="castor_slug",
    ),
]


def get_game(slug: str) -> Game | None:
    """Return the Game with this slug, or None if unknown."""
    return next((g for g in GAMES if g.slug == slug), None)
