"""Integration tests: services and API routes against a real PostgreSQL and
Redis. The PostgreSQL fixtures live in ``tests/conftest.py`` because the
concurrency tests share them; fixtures used only here, such as the HTTP
client, belong in this file.
"""
