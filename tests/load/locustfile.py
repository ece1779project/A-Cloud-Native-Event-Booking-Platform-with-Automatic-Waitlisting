"""PRD load scenarios for mixed API traffic and concurrent registration.

Accounts and the target published session are prepared before the run. Locust
users map deterministically to those accounts, so rate limits cannot hide
database concurrency behavior behind one shared identity.
"""

import itertools
import os
import random
import uuid

from locust import HttpUser, between, tag, task
from locust.exception import StopUser

SESSION_ID = os.environ.get("LOAD_SESSION_ID", "")
EMAIL_TEMPLATE = os.environ.get("LOAD_EMAIL_TEMPLATE", "load{index}@example.com")
PASSWORD = os.environ.get("LOAD_PASSWORD", "Load-test-only-123!")
_USER_NUMBERS = itertools.count()
_COMMAND_NUMBERS = itertools.count(1)
_STOP_AFTER_COMMANDS = int(os.environ.get("LOAD_STOP_AFTER_COMMANDS", "0"))
_EXPECTED_REJECTIONS = {404, 409, 429}


class BookingUser(HttpUser):
    """Approximately 7:3 reads/writes with one request per scheduled task."""

    wait_time = between(0.8, 1.2)

    def on_start(self) -> None:
        if not SESSION_ID:
            raise RuntimeError("LOAD_SESSION_ID is required")
        index = next(_USER_NUMBERS)
        response = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": EMAIL_TEMPLATE.format(index=index),
                "password": PASSWORD,
            },
            name="POST /auth/login",
        )
        if response.status_code != 200:
            raise StopUser
        self.headers = {
            "Authorization": f"Bearer {response.json()['data']['access_token']}"
        }
        self.participation_id: str | None = None
        self.state: str | None = None
        self.last_command: tuple[str, dict, str] | None = None

    @tag("mixed")
    @task(7)
    def read_state(self) -> None:
        if random.random() < 0.5:
            with self.client.get(
                f"/api/v1/sessions/{SESSION_ID}",
                headers=self.headers,
                name="GET /sessions/{id}",
                catch_response=True,
            ) as response:
                self._classify(response)
            return
        with self.client.get(
            f"/api/v1/sessions/{SESSION_ID}/my-participation",
            headers=self.headers,
            name="GET /sessions/{id}/my-participation",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                participation = response.json()["data"]
                self.participation_id = participation["id"]
                self.state = participation["state"]
            elif response.status_code == 404:
                self.participation_id = None
                self.state = None
            self._classify(response)

    @tag("mixed", "booking")
    @task(3)
    def registration_write(self) -> None:
        if self.last_command is not None and random.random() < 0.2:
            path, body, key = self.last_command
            self._post_command(path, body, key, "POST command replay")
            self._stop_at_command_limit()
            return

        if self.participation_id is None or self.state in {
            None,
            "CANCELLED",
            "EXPIRED",
        }:
            suffix = "book" if random.random() < 0.5 else "waitlist"
            path = f"/api/v1/sessions/{SESSION_ID}/{suffix}"
            body: dict = {}
        elif self.state in {"HELD", "OFFERED"}:
            path = f"/api/v1/participations/{self.participation_id}/confirm"
            body = {"simulation_result": "success"}
        elif self.state == "CONFIRMED":
            path = f"/api/v1/participations/{self.participation_id}/cancel"
            body = {}
        elif self.state == "WAITLISTED":
            path = f"/api/v1/participations/{self.participation_id}/leave"
            body = {}
        else:
            return

        key = str(uuid.uuid4())
        self.last_command = (path, body, key)
        response = self._post_command(path, body, key, "POST registration command")
        self._stop_at_command_limit()
        if response.status_code in {200, 201}:
            participation = response.json()["data"]
            self.participation_id = participation["id"]
            self.state = participation["state"]

    def _post_command(
        self, path: str, body: dict, key: str, name: str
    ):
        with self.client.post(
            path,
            headers={**self.headers, "Idempotency-Key": key},
            json=body,
            name=name,
            catch_response=True,
        ) as response:
            self._classify(response)
            return response

    @staticmethod
    def _classify(response) -> None:
        if response.status_code < 400:
            response.success()
        elif response.status_code in _EXPECTED_REJECTIONS:
            # Business conflicts and configured rate limits are reported by
            # status code but are not unexpected transport/server failures.
            response.success()
        else:
            response.failure(f"unexpected HTTP {response.status_code}")

    def _stop_at_command_limit(self) -> None:
        number = next(_COMMAND_NUMBERS)
        if (
            _STOP_AFTER_COMMANDS > 0
            and number >= _STOP_AFTER_COMMANDS
            and self.environment.runner is not None
        ):
            self.environment.runner.quit()
