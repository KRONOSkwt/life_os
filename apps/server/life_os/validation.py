"""Validate the shared wire contract and relationships within a training session."""

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker


class InvalidMessage(ValueError):
    pass


class Messages:
    def __init__(self, path: Path):
        self.schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(
            self.schema, format_checker=FormatChecker()
        )

    def validate(self, payload: object, definition: str = "pushRequest") -> None:
        schema = {**self.schema, "$ref": f"#/$defs/{definition}"}
        error = next(self.validator.evolve(schema=schema).iter_errors(payload), None)
        if error:
            location = "/".join(map(str, error.absolute_path)) or "body"
            raise InvalidMessage(f"Invalid {definition} at {location}")

    def push(self, payload: object) -> dict:
        self.validate(payload)
        seen = set()
        for operation in payload["operations"]:
            self._canonical_ids(operation)
            if operation["operation_id"] in seen:
                raise InvalidMessage("Duplicate operation_id within the batch")
            seen.add(operation["operation_id"])
            if operation["action"] == "delete":
                continue
            data = operation["data"]
            if operation["entity_type"] == "routine":
                self._routine(data)
            else:
                exercises = self._routine(data["routine_snapshot"])
                series = data["sets"]
                if len({item["id"] for item in series}) != len(series):
                    raise InvalidMessage("Duplicate set ID")
                if any(item["exercise_id"] not in exercises for item in series):
                    raise InvalidMessage("Set exercise is absent from routine_snapshot")
                if data["finished_at"] is not None:
                    start = datetime.fromisoformat(data["started_at"].replace("Z", "+00:00").replace("z", "+00:00"))
                    end = datetime.fromisoformat(data["finished_at"].replace("Z", "+00:00").replace("z", "+00:00"))
                    if end < start:
                        raise InvalidMessage("Session finishes before it starts")
        return payload

    @staticmethod
    def _routine(data: dict) -> set[str]:
        if not data["name"].strip():
            raise InvalidMessage("Routine name cannot be blank")
        exercises = data["exercises"]
        ids = {item["id"] for item in exercises}
        if len(ids) != len(exercises):
            raise InvalidMessage("Duplicate exercise ID")
        for item in exercises:
            if not item["name"].strip() or item["reps_min"] > item["reps_max"]:
                raise InvalidMessage("Invalid exercise name or repetition range")
        return ids

    @classmethod
    def _canonical_ids(cls, value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "id" or key.endswith("_id"):
                    if str(UUID(item)) != item:
                        raise InvalidMessage("UUIDs must use canonical lowercase form")
                else:
                    cls._canonical_ids(item)
        elif isinstance(value, list):
            for item in value:
                cls._canonical_ids(item)
