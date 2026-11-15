"""SQLAlchemy models, one module per table.

Every model is imported here so that ``Base.metadata`` is complete for
Alembic autogeneration. Models hold columns, constraints and indexes only;
they contain no business logic and never publish messages.

Design: DATABASE_DESIGN.md Section 2.
"""
