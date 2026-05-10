from sqlalchemy.orm import Session

from app.models import Item
from app.services.legacy import ensure_user_item_state
from app.services.presenters import item_state_flags, item_to_out
from app.services.recommendations import record_item_event


class ItemNotFound(KeyError):
    pass


def set_item_flag(db: Session, item_id: str, field: str, value: bool | None):
    item = db.get(Item, item_id)
    if not item:
        raise ItemNotFound(item_id)
    state = ensure_user_item_state(db, item_id)
    current = item_state_flags(db, item)[field]
    next_value = (not current) if value is None else value
    setattr(state, field, next_value)
    event_type = {
        "read": "read" if next_value else "unread",
        "starred": "star" if next_value else "unstar",
        "hidden": "hide" if next_value else "unhide",
    }.get(field)
    if event_type:
        record_item_event(db, item_id, event_type, commit=False)
    db.commit()
    db.refresh(item)
    return item_to_out(item, db)
