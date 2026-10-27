"""Writes a small Born-digital PDF, built the way the tests build theirs, for driving the app by hand."""

import sys
from pathlib import Path

from conftest import build_born_digital_pdf

PAGES = [
    [
        "Dental caries is a biofilm-mediated disease that destroys the mineralized tissues of the teeth.",
        "Fluoride varnish applied twice a year reduces the incidence of caries in children.",
    ],
    ["The periodontal ligament (PDL) anchors each tooth to the alveolar bone and absorbs occlusal forces."],
]

if __name__ == "__main__":
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    print(build_born_digital_pdf(target, PAGES))
