"""USACE Publications website category listings."""

USACE_PUBLICATIONS_BASE = (
    "https://www.publications.usace.army.mil/USACE-Publications"
)

USACE_PUBLICATION_CATEGORIES: list[tuple[str, str]] = [
    ("Army Regulations Supplements", "Army-Regulations-Supplements"),
    ("CG's Policy Notices", "CGs-Policy-Notices"),
    ("Engineer Circulars", "Engineer-Circulars"),
    ("Engineer Design Guides", "Engineer-Design-Guides"),
    ("Engineer Forms", "Engineer-Forms"),
    ("Engineer Manuals", "Engineer-Manuals"),
    ("Engineer Pamphlets", "Engineer-Pamphlets"),
    ("Engineer Regulations", "Engineer-Regulations"),
    ("Engineer Technical Letters", "Engineer-Technical-Letters"),
    ("Engineer Standards Graphics", "Engineer-Standards-Graphics"),
    ("Miscellaneous", "Miscellaneous"),
]


def publication_category_url(slug: str) -> str:
    return f"{USACE_PUBLICATIONS_BASE}/{slug}/"


USACE_PUBLICATION_CATEGORY_SITES = [
    {"name": name, "url": publication_category_url(slug)}
    for name, slug in USACE_PUBLICATION_CATEGORIES
]
