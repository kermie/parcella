"""
Helper functions for logging field changes (audit log).

Usage:
    tracker = ChangeTracker(parcel, "Parcel", ["area_sqm"])
    parcel.area_sqm = new_value
    await tracker.commit(db, user_id)  # writes all detected changes
"""
from typing import Any, Optional

from app.models import ChangeHistory


def _to_string(value: Any) -> Optional[str]:
    """Converts an arbitrary field value into a comparable/storable string."""
    if value is None:
        return None
    if hasattr(value, "value"):  # Enum
        return str(value.value)
    return str(value)


class ChangeTracker:
    """
    Captures an object's state before changes and determines, at
    commit time, which fields actually changed.
    """

    def __init__(self, obj, entity_type: str, fields: list[str]):
        self.entity_id = obj.id
        self.entity_type = entity_type
        self.fields = fields
        self.before = {field: _to_string(getattr(obj, field, None)) for field in fields}
        self.obj = obj

    def detect_changes(self) -> list[ChangeHistory]:
        entries = []
        for field in self.fields:
            new_value = _to_string(getattr(self.obj, field, None))
            old_value = self.before.get(field)
            if new_value != old_value:
                entries.append(
                    ChangeHistory(
                        entity_type=self.entity_type,
                        entity_id=self.entity_id,
                        field_name=field,
                        old_value=old_value,
                        new_value=new_value,
                    )
                )
        return entries

    async def commit(self, db, user_id: Optional[str] = None):
        for entry in self.detect_changes():
            entry.changed_by_id = user_id
            db.add(entry)
