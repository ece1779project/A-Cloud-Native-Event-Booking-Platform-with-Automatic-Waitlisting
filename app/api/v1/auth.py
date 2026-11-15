"""Authentication routes: ``POST /auth/login``, ``/auth/refresh`` and
``/auth/logout``. Sets and clears the refresh and CSRF cookies.

Design: TECHNICAL_DESIGN.md Sections 8.2, 9.1 and 9.2.
"""

from fastapi import APIRouter

router = APIRouter(tags=["auth"])
